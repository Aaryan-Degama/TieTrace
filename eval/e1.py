"""E1: placebo lift for Tier B (redesign section 7). Tier C is reported as context only.

Pass rule (fixed): lift >= 1.5 for Tier B AND the 95% interval excludes 1.0.

Method in plain words:
  * Population U = people who authored a (real) topic paper, minus the Tier A people and company staff.
  * Real rate  = share of U that is Tier B relative to the Tier A set: has a COAUTH_B link
                 (2+ recent small papers together) to a Tier-A person.
  * Placebo    = a random set of U, same size as the Tier A set, matched on works_count band
                 (same number of people from each band), drawn 100 times with a fixed seed.
                 Placebo rate = share of U (without the circle itself) with a COAUTH_B link to the circle.
  * Lift = real rate / mean placebo rate.
Run:  FALKORDB_PORT=6380 uv run python eval/e1.py   [--case philip-morris-lung]
"""
import math
import random
import time
from collections import defaultdict

import common as c
import research

c.use_case_arg()  # --case <slug>; default obesity-coke (P2-4)
SEED = 20261016
DRAWS = 100          # redesign section 7
BOOT = 1000          # bootstrap replicates
BAND_EDGES = [10, 25, 50, 100, 200, 500, 1000, 2000]  # works_count bands, fixed before any result was seen
start = time.time()


def band(wc):
    return "unknown" if wc is None else sum(wc >= e for e in BAND_EDGES)


# ---------- data ----------
works = c.load_works()
topic_papers = [w for w, v in works.items() if v["topic"] and v["paper"]]
authors = c.load_authors(works)
tier_a = research._tier_a([])
staff = set(research._staff([]))
topic_authors = {p for w in topic_papers for p in authors.get(w, [])}
U = sorted(topic_authors - set(tier_a) - staff)
N = len(U)
index = {p: i for i, p in enumerate(U)}
print(f"Tier A people: {len(tier_a)} (A1 {sum(v == 'A1' for v in tier_a.values())}, A2 {sum(v == 'A2' for v in tier_a.values())}); "
      f"company staff: {len(staff)}; topic authors: {len(topic_authors)}; population U (topic authors, not Tier A, not staff): {N}")

# recent small papers (same rule as COAUTH_B: since 2021, 2..25 authors), only to flag "active" people
recent = research._meta([])["recent_year"]
active = {p for w, v in works.items() if v["paper"] and (v["year"] or 0) >= recent and 2 <= v["n"] <= 25
          for p in authors.get(w, [])}

wcounts = c.load_works_counts(set(U) | set(tier_a))
# all COAUTH_B edges (stored in both directions): neighbours, and the recent small papers behind each link
adj_all, adj_topic = defaultdict(set), defaultdict(set)
q = ("MATCH (a:Person) WHERE a.oa_id IN $ids MATCH (a)-[c:COAUTH_B]->(b:Person) "
     "RETURN a.oa_id, b.oa_id, c.recent_small_works")
everyone = sorted({p for ps in authors.values() for p in ps})
n_edges = 0
for a, b, ws in c.by_ids(q, everyone, size=1500):
    adj_all[a].add(b)
    n_edges += 1
    # sensitivity: count only papers that are themselves topic papers (Tier A people also have many
    # loaded non-topic papers, which placebo people do not)
    if sum(1 for w in ws if works[w]["topic"]) >= 2:
        adj_topic[a].add(b)
print(f"COAUTH_B edges read: {n_edges} (both directions); with 2+ topic papers: {sum(map(len, adj_topic.values()))}")


# ---------- placebo circles ----------
def draw_circles(key, rng):
    """DRAWS sets of ids from U, same size as the Tier A set, matching the Tier A mix of `key` values."""
    need, pool = defaultdict(int), defaultdict(list)
    for p in tier_a:
        need[key(p)] += 1
    for p in U:
        pool[key(p)].append(p)
    short = {k: n - len(pool[k]) for k, n in need.items() if len(pool[k]) < n}
    circles = []
    for _ in range(DRAWS):
        circles.append({p for k, n in need.items() for p in rng.sample(pool[k], min(n, len(pool[k])))})
    return circles, short


def distances(sources, adj, max_hops=3):
    """{person: hops} for everyone within max_hops of the source set (multi-source breadth-first search)."""
    dist, frontier = {p: 0 for p in sources}, set(sources)
    for h in range(1, max_hops + 1):
        nxt = set()
        for p in frontier:
            for q_ in adj.get(p, ()):
                if q_ not in dist:
                    dist[q_] = h
                    nxt.add(q_)
        frontier = nxt
    return dist


def hit_sets(sources, adj, exclude=()):
    """Indices of U members at exactly 1 hop (Tier B) and at 2-3 hops (Tier C) from `sources`."""
    d = distances(sources, adj)
    b = {index[p] for p, h in d.items() if h == 1 and p in index and p not in exclude}
    c3 = {index[p] for p, h in d.items() if h >= 2 and p in index and p not in exclude}
    return b, c3


# ---------- Poisson bootstrap ----------
def poisson1(rng):
    """One draw from Poisson(mean 1): how many times an expert is counted in a resample."""
    u, k, term = rng.random(), 0, math.exp(-1)
    cum = term
    while u > cum:
        k += 1
        term /= k
        cum += term
    return k


def bootstrap(real_hits, circle_hits, circle_sizes, rng):
    """Percentile 95% interval for lift = real rate / mean placebo rate.
    Each replicate (1) counts every expert 0, 1, 2... times at random (mean once = resampling experts
    with replacement), the SAME counts for the real group and every placebo circle, and (2) re-picks
    the 100 placebo circles with replacement. ponytail: the number of circle members that fall in the
    resample is replaced by its expectation (denominator = total * (N - |circle|) / N)."""
    relevant = set(real_hits).union(*circle_hits)
    where = defaultdict(list)  # expert index -> circles in which that expert is a hit
    for j, hits in enumerate(circle_hits):
        for i in hits:
            where[i].append(j)
    keep = [1 - circle_sizes[j] / N for j in range(len(circle_hits))]
    lifts = []
    for _ in range(BOOT):
        total = rng.gauss(N - len(relevant), math.sqrt(N - len(relevant)))  # experts that are never hits
        num_real, num_circ = 0, [0] * len(circle_hits)
        for i in relevant:
            m = poisson1(rng)
            total += m
            if i in real_hits:
                num_real += m
            for j in where.get(i, ()):
                num_circ[j] += m
        rates = [num_circ[j] / (total * keep[j]) for j in range(len(circle_hits))]
        mean_plac = sum(rates[rng.randrange(len(rates))] for _ in rates) / len(rates)
        lifts.append((num_real / total) / mean_plac if mean_plac else float("inf"))
    lifts.sort()
    return c.percentile(lifts, 0.025), c.percentile(lifts, 0.975)


def run(name, adj, key, seed):
    rng = random.Random(seed)
    circles, short = draw_circles(key, rng)
    real_b, real_c = hit_sets(set(tier_a), adj)
    cb, cc, sizes = [], [], []
    for r in circles:
        b, c3 = hit_sets(r, adj, exclude=r)
        cb.append(b), cc.append(c3), sizes.append(len(r))
    out = {}
    for label, real, circ in (("Tier B", real_b, cb), ("Tier C (2-3 hops, context only)", real_c, cc)):
        real_rate = len(real) / N
        rates = [len(h) / (N - s) for h, s in zip(circ, sizes)]
        mean = sum(rates) / len(rates)
        sd = (sum((r - mean) ** 2 for r in rates) / (len(rates) - 1)) ** 0.5
        lo2, hi2 = bootstrap(real, circ, sizes, random.Random(seed + 1))  # secondary interval
        lift = real_rate / mean if mean else float("inf")
        # HEADLINE interval: the real rate divided by each of the 100 circle rates; its 2.5th-97.5th percentiles
        circle_lifts = sorted(real_rate / r for r in rates)
        lo, hi = c.percentile(circle_lifts, 0.025), c.percentile(circle_lifts, 0.975)
        out[label] = (lift, lo, hi)
        print(f"[{name}] {label}: real {len(real)}/{N} = {100*real_rate:.2f}% | placebo mean {100*mean:.2f}% "
              f"(sd {100*sd:.2f}, min {100*min(rates):.2f}, max {100*max(rates):.2f}; "
              f"circles with a rate >= real: {sum(r >= real_rate for r in rates)}/{DRAWS}) | "
              f"lift {lift:.2f}x, HEADLINE interval (spread across the {DRAWS} circles; Tier A treated as fixed) "
              f"[{lo:.2f}, {hi:.2f}]; secondary expert-resampling bootstrap [{lo2:.2f}, {hi2:.2f}]")
    print(f"[{name}] circle size {sizes[0]} (Tier A set {len(tier_a)}); strata short of candidates: {short or 'none'}")
    return out


by_band = lambda p: band(wcounts.get(p))
by_band_active = lambda p: (band(wcounts.get(p)), p in active)
print("\nMatching bands (works_count):", ["<10"] + [f"{a}-{b - 1}" for a, b in zip(BAND_EDGES, BAND_EDGES[1:])] + ["2000+"])
print("Tier A people per band:", dict(sorted(((str(k), v) for k, v in
      __import__("collections").Counter(by_band(p) for p in tier_a).items()))))
print("Tier A people with a recent small paper in the loaded data:", sum(p in active for p in tier_a), "of", len(tier_a),
      "| U:", sum(p in active for p in U), "of", N, "\n")

main = run("PRIMARY: all COAUTH_B edges, matched on works_count band", adj_all, by_band, SEED)
lift, lo, hi = main["Tier B"]
verdict = "PASS" if lift >= 1.5 and lo > 1.0 else "FAIL"
print(f"\nE1 pass rule (Tier B lift >= 1.5 and interval excludes 1.0): {verdict}\n")
# Lift depends on how many Tier A sources are used: repeat with a random half of them as the sources.
half = random.Random(SEED + 300).sample(sorted(tier_a), len(tier_a) // 2)
rng_h = random.Random(SEED + 301)
need_h, pool_h = defaultdict(int), defaultdict(list)
for p in half:
    need_h[by_band(p)] += 1
for p in U:
    pool_h[by_band(p)].append(p)
rb = len(hit_sets(set(half), adj_all)[0]) / N
rates_h = []
for _ in range(DRAWS):
    r = {p for k, n in need_h.items() for p in rng_h.sample(pool_h[k], min(n, len(pool_h[k])))}
    rates_h.append(len(hit_sets(r, adj_all, exclude=r)[0]) / (N - len(r)))
print(f"Half-source check (not the pass rule): {len(half)} random Tier A people as sources: real {100*rb:.2f}%, "
      f"placebo mean {100*sum(rates_h)/DRAWS:.2f}%, lift {rb/(sum(rates_h)/DRAWS):.2f}x")
print()
print("Sensitivity checks (not part of the pass rule):")
run("SENS-1: edges that rest on 2+ TOPIC papers only", adj_topic, by_band, SEED + 100)
run("SENS-2: matched on works_count band AND 'has a recent small paper'", adj_all, by_band_active, SEED + 200)
print(f"\nruntime {time.time()-start:.0f}s")
