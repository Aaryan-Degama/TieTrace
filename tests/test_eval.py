"""Tests for the checkpoint-4 evaluations (E7, E3, E1, E2) and eval/results.md. Written by the Reviewer.

Run:  FALKORDB_PORT=6380 uv run python tests/test_eval.py

Everything is recomputed here WITHOUT importing eval/*.py: tests/eval_model.py reads the raw nodes and
edges (slices below the silent 10,000-row cap) and the rest is plain Python (own rank-sum AUC, own
COAUTH_B from AUTHORED, own placebo draws with other seeds). research.tie_report is called only for the
E3 tier/DOI checks (it is the tool under test there). Numbers in eval/results.md are compared with the
recomputed ones. A failing test is reported, not hidden; tests must not be weakened to make them pass.
"""
import bisect
import os
import random
import re
import sys
import traceback
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("FALKORDB_PORT", "6379")
import eval_model as E  # noqa: E402
import research  # noqa: E402

RES = (ROOT / "eval" / "results.md").read_text(encoding="utf-8")
M = None
FAILED = []


def model():
    global M
    if M is None:
        M = E.build()
        M["ev"] = E.tier_a_events(M)
        M["tier_a"] = {p: ("A1" if any(s == "A1" for _, s, _ in v) else "A2") for p, v in M["ev"].items()}
        M["staff"] = {p for p, _, _ in M["aff"]}
        M["topic_papers"] = [w for w in M["topic"] if E.paper(M, w)]
    return M


# ---------------- results.md hygiene ----------------
def test_results_pass_rules_verbatim():
    assert "Tier B lift >= 1.5x AND the 95% interval excludes 1.0" in RES
    assert "Weighted Tier B beats both the volume baseline and the plain-count baseline by >= 0.05 AUC" in RES \
        or "beats both the volume baseline and the plain-count baseline by >= 0.05 AUC" in RES
    assert "at least 30 positives" in RES
    assert "all five must be found" in RES


def test_results_no_banned_words():
    assert not re.search(r"\bconflicted\b", RES, re.I)
    bad = []
    for m in re.finditer(r"\bindependent\b", RES, re.I):
        pre = RES[max(0, m.start() - 4):m.start()].lower()
        # statistical use ("not independent", "as independent") is tolerated; a label like "Independent check" is not
        if not (pre.endswith("not ") or pre.endswith("as ")):
            bad.append(RES[max(0, m.start() - 20):m.end() + 20])
    assert not bad, f"banned word 'independent' in results.md: {bad}"


def test_results_names_only_public_case():
    # Tremblay is in no public GEBN source (results.md says so itself): he is not part of the documented public case
    assert "Tremblay" not in RES, "results.md names a person who is not in the documented public case"


# ---------------- E7 ----------------
def test_e7_numbers():
    m = model()
    W, topic = m["works"], m["topic"]
    n = len(topic)
    assert n == 30140 and "30,140" in RES
    funder = topic & m["any_funder"]
    assert len(funder) == 16965
    affw = set()
    for ids in E.slices(sorted(topic), 2000):
        affw |= {r[0] for r in E.q("MATCH ()-[a:AFFILIATED]->() WHERE a.work IN $ids RETURN DISTINCT a.work", {"ids": ids})}
    fa = topic & (m["any_funder"] | affw)
    assert len(fa) == 29122 and len(topic & affw - funder) == 12157 and n - len(fa) == 1018
    assert round(100 * len(funder) / n, 1) == 56.3 and round(100 * len(fa) / n, 1) == 96.6
    for s in ("16,965", "56.3%", "29,122", "96.6%", "12,157", "1,018"):
        assert s in RES, s
    junk = sum(1 for v in W.values() if (v["doi"] or "").startswith("10.5256/") or (v["title"] or "").lower().startswith(E.JUNK_TITLES))
    nonp = sum(1 for v in W.values() if (v["title"] or "").strip().lower() in ("index", "front matter"))
    untitled = sum(1 for v in W.values() if not (v["title"] or "").strip())
    trunc = sum(1 for w, v in W.items() if w in topic and v["n"] >= 100)
    assert (junk, nonp, untitled, trunc) == (19, 4, 11, 25)
    norm = lambda t: " ".join(re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).split())
    g = defaultdict(list)
    for w, v in W.items():
        if norm(v["title"]):
            g[(norm(v["title"]), v["year"])].append(w)
    d = [x for x in g.values() if len(x) > 1]
    assert (len(d), sum(len(x) for x in d)) == (55, 116)
    assert "55 groups, 116 records" in RES
    sc = Counter(v["scope"] for v in W.values())
    assert (len(W), sc["topic"], sc["funded"], sc["family"]) == (31291, 29867, 817, 607)
    assert "31,291 works (29,867 from the topic load, 817 from the funded load, 607" in RES
    assert len(m["tier_a"]) == 3035 and "3,035" in RES


def test_e7_per_year_and_sample_disclosure():
    m = model()
    mt = m["meta"]
    for y, lo, to in zip(mt["years"], mt["years_loaded"], mt["years_total"]):
        assert f"{y} {lo}/{to}" in RES, (y, lo, to)
    loaded, total = sum(mt["years_loaded"]), sum(mt["years_total"])
    assert (loaded, total) == (30027, 260689) and round(100 * loaded / total, 1) == 11.5
    assert "30,027 of 260,689 (11.5%)" in RES
    # the redesign said 'all works on the topic since 2010': results.md must say it is a SAMPLE
    assert "per-year SAMPLE" in RES and "most-cited" in RES and "11.5%" in RES
    W, topic = m["works"], m["topic"]
    byy = defaultdict(list)
    for w in topic:
        if W[w]["year"] and 2010 <= W[w]["year"] <= 2025:
            byy[W[w]["year"]].append(w)
    fr = {y: 100 * sum(w in m["any_funder"] for w in ws) / len(ws) for y, ws in byy.items()}
    assert round(min(fr.values())) == 45 and round(max(fr.values())) == 64
    assert round(fr[2010]) == 45 and round(fr[2019]) == 64 and "45% (2010) to 64% (2019)" in RES


# ---------------- E3 ----------------
GEBN = {"James O. Hill": "A5031318756", "Steven N. Blair": "A5105003630",
        "Gregory A. Hand": "A5046953968", "John C. Peters": "A5045252457"}


def test_e3_tiers_and_dois():
    m = model()
    W = m["works"]
    expect = {"A5031318756": ("A2", 7, 0, 7, 4), "A5105003630": ("A1", 71, 6, 65, 59),
              "A5046953968": ("A1", 26, 3, 23, 20), "A5045252457": ("A2", 1, 0, 1, 0)}
    for pid, (tier, n, a1, a2, ontopic) in expect.items():
        d = research.tie_report(pid, public_case=True)["data"]
        assert d["tier"] == tier, (pid, d["tier"])
        ev = d["evidence"]
        assert len(ev) == n and all(e["doi"] for e in ev), pid
        assert (sum(e["tier"] == "A1" for e in ev), sum(e["tier"] == "A2" for e in ev)) == (a1, a2), pid
        assert sum(e["topic_label"] == "on-topic" for e in ev) == ontopic, pid
        # same facts from raw data. The tool lists one item per (work, funder) or affiliation, so a work can appear
        # twice; compare at the level of distinct papers (DOIs).
        mine = {w: (y, s) for y, s, w in m["ev"][pid]}
        strip = lambda s: s.replace("https://doi.org/", "")
        assert {strip(e["doi"]) for e in ev} == {strip(W[w]["doi"]) for w in mine}, pid
        assert {strip(e["doi"]) for e in ev if e["tier"] == "A1"} == {strip(W[w]["doi"]) for w, (_, s) in mine.items() if s == "A1"}, pid
        assert sum(w in m["topic"] for w in mine) <= ontopic <= len(ev)
        assert all(pid in m["auth"][w] for w in mine)
    # Hill: foundation only; Peters: one 2014 foundation-funded paper
    assert all(m["funded"][w] == {"foundation"} for _, _, w in m["ev"]["A5031318756"])
    assert [(y, W[w]["doi"]) for y, _, w in m["ev"]["A5045252457"]] == [(2014, "10.1002/oby.20737")]
    assert "10.1002/oby.20737" in RES and "10.1017/s0007114520001798" in RES


def test_e3_ids_by_evidence():
    q = lambda i: E.q("MATCH (p:Person {oa_id:$i}) OPTIONAL MATCH (p)-[:AFFILIATED]->(o:Org) "
                      "RETURN p.name, p.orcid, p.works_count, collect(DISTINCT o.name)", {"i": i})[0]
    hill, blair, hand, peters = (q(GEBN[k]) for k in GEBN)
    assert hill[0] == "James O. Hill" and hill[1] == "0000-0003-4690-2264" and any("Colorado" in o for o in hill[3])
    assert peters[0] == "John C. Peters" and peters[1] == "0000-0003-2302-8033" and any("Colorado" in o for o in peters[3])
    assert any("South Carolina" in o for o in blair[3]), blair[3]
    assert any("West Virginia" in o for o in hand[3]), hand[3]
    m = model()
    shared = {w for _, _, w in m["ev"][GEBN["Steven N. Blair"]]} & {w for _, _, w in m["ev"][GEBN["Gregory A. Hand"]]}
    assert any(m["works"][w]["doi"] == "10.1016/j.pmedr.2018.07.013" for w in shared)


def test_e3_control_and_split_profiles():
    m = model()
    r = research.tie_report("A5088704695")["data"]
    assert r["tier"] == "B" and r["evidence"] == []
    auth = defaultdict(set)
    for w, ps in m["auth"].items():
        for p in ps:
            auth[p].add(w)
    co = lambda p: {x for w in auth[p] for x in m["auth"][w]} - {p}
    for main, other, shared_n, other_n in (("A5046953968", "A5109223784", 4, 5), ("A5105003630", "A5112298044", 3, 4),
                                             ("A5105003630", "A5087575650", 0, 5)):
        assert other not in m["tier_a"]
        assert len(auth[other]) == 1
        assert len(co(other)) == other_n and len(co(other) & co(main)) == shared_n, (other, len(co(other)), len(co(other) & co(main)))
    assert len(auth["A5046953968"]) == 34 and "34 loaded works" in RES


def test_e3_verdict_wording():
    top = RES[:RES.index("**What this means")]
    assert "4 of 4" in top and "could not be confirmed" in top
    assert not re.search(r"5 of 5", top)


# ---------------- E1 ----------------
def e1_data():
    m = model()
    auth, W = m["auth"], m["works"]
    ta_auth = {p for w in m["topic_papers"] for p in auth.get(w, [])}
    U = sorted(ta_auth - set(m["tier_a"]) - m["staff"])
    pc = Counter()
    for w, v in W.items():
        if v["year"] and v["year"] >= 2021 and v["n"] and 2 <= v["n"] <= 25 and E.paper(m, w):
            a = sorted(set(auth.get(w, [])))
            for i in range(len(a)):
                for j in range(i + 1, len(a)):
                    pc[(a[i], a[j])] += 1
    adj = defaultdict(set)
    for (a, b), k in pc.items():
        if k >= 2:
            adj[a].add(b)
            adj[b].add(a)
    wc = {}
    for sl in E.slices(sorted(set(U) | set(m["tier_a"])), 3000):
        for i, c in E.q("MATCH (p:Person) WHERE p.oa_id IN $ids RETURN p.oa_id, p.works_count", {"ids": sl}):
            wc[i] = c
    return U, adj, wc


def test_e1_real_rate_and_lift():
    m = model()
    U, adj, wc = e1_data()
    Uset = set(U)
    assert len(U) == 81373
    assert sum(len(x) for x in adj.values()) == 50436      # my COAUTH_B (from AUTHORED) has the stored edge count
    real = set()
    for a in m["tier_a"]:
        real |= adj.get(a, set())
    real &= Uset
    assert len(real) == 1299 and "1,299 / 81,373 = 1.60%" in RES
    edges = [10, 25, 50, 100, 200, 500, 1000, 2000]
    band = lambda p: None if wc.get(p) is None else sum(wc[p] >= e for e in edges)
    need = Counter(band(p) for p in m["tier_a"])
    pool = defaultdict(list)
    for p in U:
        pool[band(p)].append(p)
    lifts = []
    for seed in (7, 99, 2027):                              # not the Builder's seed
        rng = random.Random(seed)
        rates = []
        for _ in range(100):
            c = set()
            for k, v in need.items():
                c |= set(rng.sample(pool[k], v))
            h = set()
            for a in c:
                h |= adj.get(a, set())
            rates.append(len((h & Uset) - c) / (len(U) - len(c)))
        lifts.append((len(real) / len(U)) / (sum(rates) / len(rates)))
    print("   own E1 lifts (3 seeds):", [round(x, 3) for x in lifts])
    assert all(0.90 <= x <= 1.00 for x in lifts), lifts     # the Builder reports 0.96x
    reported = float(re.search(r"Tier B lift ([0-9.]+)x, interval", RES).group(1))
    assert abs(reported - sum(lifts) / 3) < 0.03
    verdict_fail = max(lifts) < 1.5                         # under the fixed rule any lift below 1.5 fails
    assert verdict_fail and "**FAIL** (Tier B lift 0.96x" in RES


# ---------------- E2 ----------------
def rank_auc(pos, neg):
    ns = sorted(neg)
    tot = sum(bisect.bisect_left(ns, s) + 0.5 * (bisect.bisect_right(ns, s) - bisect.bisect_left(ns, s)) for s in pos)
    return tot / (len(pos) * len(ns))


def e2(Y, back=4):
    m = model()
    W, auth = m["works"], m["auth"]
    first = {p: min(y for y, _, _ in v if y) for p, v in m["ev"].items() if any(y for y, _, _ in v)}
    ta = {p for p, y in first.items() if y <= Y}
    vol, plain, plainw, wt = Counter(), defaultdict(set), defaultdict(set), defaultdict(float)
    topic_pers = set()
    for w, v in W.items():
        if not v["year"] or v["year"] > Y or not E.paper(m, w):
            continue
        a = set(auth.get(w, []))
        for x in a:
            vol[x] += 1
        if w in m["topic"]:
            topic_pers |= a
        t = a & ta
        for x in a:
            o = t - {x}
            plain[x] |= o
            if 2 <= v["n"] <= 25 and v["year"] >= Y - back and o:
                wt[x] += len(o) / (v["n"] - 1)
                plainw[x] |= o
    exp = sorted(topic_pers - ta)
    lab = [1 if Y < first.get(p, 9999) <= Y + 5 else 0 for p in exp]
    out = {}
    for name, f in (("vol", lambda p: vol[p]), ("plain_all", lambda p: len(plain[p])),
                    ("plain_win", lambda p: len(plainw[p])), ("weighted", lambda p: wt[p])):
        s = [f(p) for p in exp]
        out[name] = rank_auc([x for x, l in zip(s, lab) if l], [x for x, l in zip(s, lab) if not l])
    return ta, exp, lab, first, out


def test_e2_no_leakage():
    m = model()
    ta, exp, lab, first, out = e2(2018)
    assert all(first[p] <= 2018 for p in ta)                # Tier-A-at-Y comes from works up to 2018 only
    assert not (set(exp) & ta)                              # no expert is already Tier A at Y
    for p, l in zip(exp, lab):
        f = first.get(p, 9999)
        assert (2019 <= f <= 2023) if l else (f > 2023), (p, f)   # positives: new link 2019-2023; negatives: none
    W = m["works"]
    on = {p for w in m["topic_papers"] if W[w]["year"] and W[w]["year"] <= 2018 for p in m["auth"].get(w, [])}
    assert set(exp) <= on                                   # every expert has a topic paper by 2018
    assert dict(m["tier_a"]) == dict(research._tier_a([]))  # my no-cut rebuild equals the shipped Tier A set
    assert len(ta) == 1981 and len(exp) == 38530 and sum(lab) == 119
    assert "119 positives" in RES and "38,530" in RES and "38,411" in RES


def test_e2_aucs_and_verdict():
    ta, exp, lab, first, o = e2(2018)
    for k, v in {"vol": 0.720, "plain_all": 0.749, "plain_win": 0.712, "weighted": 0.716}.items():
        assert abs(o[k] - v) < 0.002, (k, o[k], v)
    for row in ("| Volume (papers up to Y) | 0.720 |", "| Plain count, all Tier-A co-authors | 0.749 |",
                "| Plain count, same window and size filter | 0.712 |", "| **Weighted score** | **0.716** |"):
        assert row in RES, row
    margins = [o["weighted"] - o["vol"], o["weighted"] - o["plain_all"], o["weighted"] - o["plain_win"]]
    assert not (sum(lab) >= 30 and all(x >= 0.05 for x in margins))     # fixed rule: FAIL
    assert "**FAIL** (119 positives" in RES
    _, _, lab5, _, o5 = e2(2018, back=5)
    assert abs((o5["weighted"] - o5["vol"]) - 0.005) < 0.003 and abs((o5["weighted"] - o5["plain_all"]) + 0.025) < 0.003
    for Y, npos, wv, wp in ((2016, 149, 0.057, -0.010), (2017, 134, 0.039, -0.024), (2019, 90, 0.014, -0.006)):
        _, _, l, _, oo = e2(Y)
        assert sum(l) == npos, Y
        assert abs((oo["weighted"] - oo["vol"]) - wv) < 0.003 and abs((oo["weighted"] - oo["plain_all"]) - wp) < 0.003, Y
        assert f"{Y} ({npos}," in RES


def test_auc_implementation_sanity():
    assert rank_auc([2, 3], [1, 2]) == (1 + 0.5 + 1 + 1) / 4
    assert rank_auc([1], [1]) == 0.5


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
            print("PASS", name)
        except Exception:
            FAILED.append(name)
            print("FAIL", name)
            traceback.print_exc(limit=3)
    print(f"\n{len(tests) - len(FAILED)}/{len(tests)} passed")
    sys.exit(1 if FAILED else 0)
