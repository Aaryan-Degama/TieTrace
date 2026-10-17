"""E2: temporal hold-out (redesign section 7). Primary cut year Y = 2018.

Pass rule (fixed): the weighted Tier B score beats BOTH the volume baseline and the plain-count baseline by
>= 0.05 AUC; at least 30 positives, else UNDERPOWERED.

Method in plain words (nothing after year Y is used to build the predictors):
  * Tier-A-at-Y = people with Tier A evidence (company/foundation-funded work, or company-family affiliation
    on a work) from works published in year <= Y. Same rules as research._tier_a, rebuilt here from the
    graph's raw Work / AUTHORED / FUNDED_BY / AFFILIATED data (checked below against research._tier_a).
  * Experts at Y = people who authored a topic paper in year <= Y and are not Tier-A-at-Y.
  * Positive = an expert who gets NEW Tier A evidence from a work in Y+1..Y+5. Everyone else is a negative.
  * Predictors, all computed from works in year <= Y only:
      volume   = number of loaded papers by the person up to Y
      direct   = has Tier A evidence at Y (constant 0 here, because experts are non-Tier-A by definition)
      plain    = number of DISTINCT Tier-A-at-Y co-authors
      weighted = the fixed score: sum of 1/(n_authors-1) over papers with 2..25 authors in the window
                 Y-4..Y, once per Tier-A-at-Y co-author on the paper (= sum of COAUTH.w to Tier A people)
  * AUC by the rank-sum (Mann-Whitney) method; bootstrap = resample positives and negatives with replacement.
Run:  FALKORDB_PORT=6380 uv run python eval/e2.py   [--case philip-morris-lung]
"""
import random
import time
from collections import defaultdict

import common as c
import research

c.use_case_arg()  # --case <slug>; default obesity-coke (P2-4)
SEED = 20261016
BOOT = 500
SMALL = 25
start = time.time()

# ---------- raw data ----------
works = c.load_works()
authors = c.load_authors(works)
funded, affil = c.load_target_links()
wcounts = c.load_works_counts({p for ps in authors.values() for p in ps})

# Tier A evidence events: person -> list of (year, "A1" | "A2"). Same rules as research._tier_a.
events = defaultdict(list)
for wid, kinds in funded.items():
    w = works.get(wid)
    if w and w["paper"] and w["year"]:
        for p in authors.get(wid, []):
            events[p].append((w["year"], "A1" if "company" in kinds and w["n"] <= SMALL else "A2"))
for p, wid, rel in affil:
    w = works.get(wid)
    if w and w["paper"] and w["year"]:
        events[p].append((w["year"], "A1" if rel != "bottler" and w["n"] <= SMALL else "A2"))
first_any = {p: min(y for y, _ in ev) for p, ev in events.items()}
first_a1 = {p: min(y for y, s in ev if s == "A1") for p, ev in events.items() if any(s == "A1" for _, s in ev)}

# Check the rebuild against research._tier_a. That function has no year filter, but it also ignores works
# without a year (and mine do the same), so the sets must match exactly.
shipped = research._tier_a([])
mine = {p: ("A1" if any(s == "A1" for _, s in ev) else "A2") for p, ev in events.items()}
no_year = {p for p in shipped if p not in mine}
print(f"check: shipped Tier A {len(shipped)}, rebuilt {len(mine)}, same ids and A1/A2 labels: "
      f"{ {k: v for k, v in shipped.items() if k in mine} == mine} (shipped-only: {len(no_year)}, rebuilt-only: {len(set(mine) - set(shipped))})")

papers = [(w["year"], w["n"], authors[wid], w["topic"]) for wid, w in works.items()
          if w["paper"] and w["year"] and wid in authors]
topic_first = {}  # first year a person appears on a topic paper
for year, n, auth, topic in papers:
    if topic:
        for p in auth:
            topic_first[p] = min(topic_first.get(p, 9999), year)


def features(Y, window_back):
    """Predictors for everyone, using only papers with year <= Y. Returns dicts keyed by person id."""
    ta = {p for p, y in first_any.items() if y <= Y}
    vol, plain_all = defaultdict(int), defaultdict(set)
    pair_n, weighted = defaultdict(int), defaultdict(float)
    for year, n, auth, _ in papers:
        if year > Y:
            continue  # nothing after the cut year
        in_ta = [a for a in auth if a in ta]
        for x in auth:
            vol[x] += 1
        if not in_ta:
            continue
        small = 2 <= n <= SMALL and year >= Y - window_back
        for x in auth:
            for a in in_ta:
                if a == x:
                    continue
                plain_all[x].add(a)
                if small:
                    pair_n[(x, a)] += 1
                    weighted[x] += 1 / (n - 1)
    plain_win, is_b = defaultdict(set), set()
    for (x, a), k in pair_n.items():
        plain_win[x].add(a)
        if k >= 2:
            is_b.add(x)  # Tier B: 2+ small recent papers with one Tier-A person
    return ta, vol, plain_all, plain_win, weighted, is_b


def bootstrap(preds, labels, pairs, rng):
    """95% percentile intervals of the AUC differences in `pairs`, resampling positives and negatives."""
    pos = [i for i, y in enumerate(labels) if y]
    neg = [i for i, y in enumerate(labels) if not y]
    groups = {}  # per predictor: score groups [(positive ids, negative ids)] from low to high
    for name, s in preds.items():
        g = defaultdict(lambda: ([], []))
        for i, y in enumerate(labels):
            g[s[i]][0 if y else 1].append(i)
        groups[name] = [g[k] for k in sorted(g)]
    diffs = {p: [] for p in pairs}
    for _ in range(BOOT):
        w = defaultdict(int)
        for i in rng.choices(pos, k=len(pos)):
            w[i] += 1
        for i in rng.choices(neg, k=len(neg)):
            w[i] += 1
        a = {}
        for name, gs in groups.items():
            below, num = 0, 0
            for gp, gn in gs:
                wn = sum(w[i] for i in gn)
                num += sum(w[i] for i in gp) * (below + 0.5 * wn)
                below += wn
            a[name] = num / (len(pos) * len(neg))
        for p in pairs:
            diffs[p].append(a[p[0]] - a[p[1]])
    return {p: (c.percentile(sorted(d), 0.025), c.percentile(sorted(d), 0.975)) for p, d in diffs.items()}


def run_cut(Y, window_back=4, target="any", ci=False, label=""):
    """Primary analysis for one cut year. window_back=4 means the window Y-4..Y."""
    ta, vol, plain_all, plain_win, weighted, is_b = features(Y, window_back)
    first = first_any if target == "any" else first_a1
    # experts at Y: on a topic paper by Y, not Tier A at Y. Positive: first Tier A evidence in Y+1..Y+5.
    experts = sorted(p for p, y in topic_first.items() if y <= Y and p not in ta)
    labels = [1 if Y < first.get(p, 9999) <= Y + 5 else 0 for p in experts]
    npos, nneg = sum(labels), len(labels) - sum(labels)
    preds = {
        "volume (papers up to Y)": [vol[p] for p in experts],
        "direct-only (constant here)": [0 for p in experts],
        "plain count, all Tier-A co-authors": [len(plain_all[p]) for p in experts],
        "plain count, same window and size filter": [len(plain_win[p]) for p in experts],
        "weighted score": [round(weighted[p], 9) for p in experts],
        "tier-first (B, then score)": [(1000.0 if p in is_b else 0.0) + weighted[p] for p in experts],
        "volume, lifetime works_count (leaks the future)": [wcounts.get(p) or 0 for p in experts],
    }
    print(f"\n== {label}cut Y={Y}, window {Y - window_back}..{Y}, target {target} ==")
    print(f"Tier-A-at-Y {len(ta)}; experts {len(experts)}; positives {npos}; negatives {nneg}"
          + ("  -> UNDERPOWERED (< 30 positives)" if npos < 30 else ""))
    if npos == 0:
        return None
    a = {k: c.auc([s for s, y in zip(v, labels) if y], [s for s, y in zip(v, labels) if not y]) for k, v in preds.items()}
    for k, v in a.items():
        print(f"  AUC {v:.3f}  {k}")
    w, vo = a["weighted score"], a["volume (papers up to Y)"]
    pa, pb = a["plain count, all Tier-A co-authors"], a["plain count, same window and size filter"]
    print(f"  weighted - volume {w - vo:+.3f} | weighted - plain(all) {w - pa:+.3f} | weighted - plain(filtered) {w - pb:+.3f}")
    if ci:
        names = ["weighted score", "volume (papers up to Y)", "plain count, all Tier-A co-authors",
                 "plain count, same window and size filter"]
        pairs = [(names[0], n) for n in names[1:]]
        iv = bootstrap({n: preds[n] for n in names}, labels, pairs, random.Random(SEED))
        for (x, y), (lo, hi) in iv.items():
            print(f"  bootstrap 95% interval, AUC({x}) - AUC({y}): [{lo:+.3f}, {hi:+.3f}]")
    passed = npos >= 30 and w - vo >= 0.05 and w - pa >= 0.05 and w - pb >= 0.05
    print(f"  pass rule (all three margins >= 0.05, >= 30 positives): {'PASS' if passed else 'FAIL' if npos >= 30 else 'UNDERPOWERED'}")
    return a


def run_relink(Y, window_back=4):
    """Supplementary: ALL topic authors at Y (Tier-A-at-Y people included); positive = new Tier A evidence
    from a work in Y+1..Y+5. Here 'direct-only' (already Tier A at Y) is a real baseline."""
    ta, vol, plain_all, plain_win, weighted, is_b = features(Y, window_back)
    people = sorted(p for p, y in topic_first.items() if y <= Y)
    labels = [1 if any(Y < y <= Y + 5 for y, _ in events.get(p, [])) else 0 for p in people]
    npos = sum(labels)
    tw = {"A1": 100, "A2": 50}
    direct = [tw[mine_at(p, Y)] if p in ta else 0 for p in people]
    preds = {"volume": [vol[p] for p in people], "direct-only (tier weight)": direct,
             "plain count (filtered)": [len(plain_win[p]) for p in people],
             "full score (tier weight + coauth w)": [d + round(weighted[p], 9) for d, p in zip(direct, people)]}
    print(f"\n== SUPPLEMENTARY re-link, all topic authors, cut Y={Y} ==\npeople {len(people)} (Tier-A-at-Y among them: {sum(p in ta for p in people)}); positives {npos}")
    for k, v in preds.items():
        print(f"  AUC {c.auc([s for s, y in zip(v, labels) if y], [s for s, y in zip(v, labels) if not y]):.3f}  {k}")


def mine_at(p, Y):
    return "A1" if any(s == "A1" and y <= Y for y, s in events[p]) else "A2"


primary = run_cut(2018, ci=True, label="PRIMARY ")
print("\n--- Sensitivity (not the pass rule) ---")
run_cut(2018, window_back=5, label="window Y-5..Y (6 calendar years, the analogue of 2021-2026) ")
run_cut(2018, target="A1", label="target = A1 evidence only ")
run_cut(2016, label="other cut ")
run_cut(2017, label="other cut ")
run_cut(2019, label="other cut ")
run_relink(2018)
print(f"\nruntime {time.time()-start:.0f}s")
