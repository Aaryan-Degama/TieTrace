"""Tests for research.py and verifier.py (checkpoint 3). Written by the Reviewer.



Run:  FALKORDB_PORT=6380 uv run python tests/test_research.py

Everything is checked against the live graph `research:obesity-coke` (read-only), and the expected

values are recomputed here WITHOUT calling research.py: bulk reads of the raw nodes and edges

(paged, because FalkorDB silently cuts any result at 10,000 rows), then plain Python for tiers,

scores and breadth-first search. A failing test means the code breaks the spec; it is reported,

not hidden, and tests must not be weakened to make them pass.

"""

import json

import os

import random

import re

import subprocess

import sys

import time

import traceback

from collections import defaultdict, deque

from pathlib import Path



ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT))

os.environ.setdefault("FALKORDB_PORT", "6379")

import research  # noqa: E402

import verifier  # noqa: E402

from falkordb import FalkorDB  # noqa: E402



G = FalkorDB(port=int(os.environ["FALKORDB_PORT"])).select_graph(research.GRAPH)

SEED = 20261017

SMALL, RECENT = 25, 2021

JUNK_TITLES = ("peer review report", "correction:", "corrigendum", "erratum", "notice of retraction")

BANNED = re.compile(r"\b(independent|conflicted)\b", re.IGNORECASE)





def q(query, params=None, timeout=300000):

    return G.ro_query(query, params or {}, timeout).result_set





def paged(base, order, page=8000):

    out, skip = [], 0

    while True:

        rows = q(f"{base} ORDER BY {order} SKIP {skip} LIMIT {page}")

        out += rows

        if len(rows) < page:

            return out

        skip += page





def is_junk(doi, title):

    t = (title or "").lower()

    # junk (reports, corrections) and non-papers (book "Index" / "Front Matter" records): never evidence

    return ((doi or "").startswith("10.5256/") or any(t.startswith(p) for p in JUNK_TITLES)

            or t.strip() in ("index", "front matter"))





# ---------- independent model of the graph (loaded once) ----------

_M = {}

CHANGED = []  # filled in sample(): people whose only Tier A evidence was an Index/Front Matter record (no Tier A now)





def model():

    if _M:

        return _M

    works = {r[0]: r for r in paged("MATCH (w:Work) RETURN w.oa_id, w.doi, w.title, w.year, w.n_authors", "w.oa_id")}

    on_topic = {r[0] for r in paged("MATCH (w:Work)-[:ABOUT]->(:Topic) RETURN DISTINCT w.oa_id", "w.oa_id")}

    bottlers = {r[0] for r in q("MATCH (:Org)-[r:PARENT_OF]->(o:Org) WHERE r.rel = 'bottler' RETURN o.oa_id")}

    funded = paged("MATCH (p:Person)-[:AUTHORED]->(w:Work)-[:FUNDED_BY]->(f:Funder {target:true}) "

                   "RETURN p.oa_id, w.oa_id, f.kind", "p.oa_id, w.oa_id")

    affil = paged("MATCH (p:Person)-[a:AFFILIATED]->(o:Org {target:true}) RETURN p.oa_id, a.work, o.oa_id",

                  "p.oa_id, a.work, o.oa_id")

    # evidence[pid] = {(kind, work, via): (tier, on_topic)}  (junk works dropped)

    evidence = defaultdict(dict)

    for pid, wid, kind in funded:

        _, doi, title, year, n = works[wid]

        if is_junk(doi, title):

            continue

        tier = "A2" if kind == "foundation" or n > SMALL else "A1"

        evidence[pid][("FUNDED_BY", wid, kind)] = (tier, wid in on_topic)

    for pid, wid, oid in affil:

        _, doi, title, year, n = works[wid]

        if is_junk(doi, title):

            continue

        tier = "A2" if oid in bottlers or n > SMALL else "A1"

        evidence[pid][("AFFILIATED", wid, oid)] = (tier, wid in on_topic)

    tier_a = {p: ("A1" if any(t == "A1" for t, _ in ev.values()) else "A2") for p, ev in evidence.items()}

    adj = defaultdict(dict)  # adj[a][b] = (recent_small_w, dois, works)

    for a, b, w, dois, wk in paged("MATCH (a:Person)-[c:COAUTH_B]->(b:Person) RETURN a.oa_id, b.oa_id, "

                                   "c.recent_small_w, c.recent_small_dois, c.recent_small_works", "a.oa_id, b.oa_id"):

        adj[a][b] = (w, dois, wk)

    # distance (in COAUTH_B hops) from every person to the nearest Tier-A person

    dist = {p: 0 for p in tier_a}

    queue = deque(tier_a)

    while queue:

        x = queue.popleft()

        for y in adj.get(x, {}):

            if y not in dist:

                dist[y] = dist[x] + 1

                queue.append(y)

    _M.update(works=works, on_topic=on_topic, evidence=evidence, tier_a=tier_a, adj=adj, dist=dist,

              bottlers=bottlers)

    return _M





def shared_recent_small(pid, tier_a):

    """Independent from COAUTH: shared recent small works between pid and each Tier-A person."""

    rows = q("MATCH (e:Person) WHERE e.oa_id = $id MATCH (e)-[:AUTHORED]->(w:Work)<-[:AUTHORED]-(b:Person) "

             "WHERE w.year >= 2021 AND w.n_authors <= 25 AND w.n_authors > 1 AND b.oa_id IN $ids AND b <> e "

             "AND NOT (coalesce(w.doi,'') STARTS WITH '10.5256/' OR any(pre IN $junk WHERE toLower(coalesce(w.title,'')) STARTS WITH pre) "

             "OR toLower(coalesce(w.title,'')) IN ['index', 'front matter']) "

             "RETURN b.oa_id, count(DISTINCT w), sum(1.0 / (w.n_authors - 1))",

             {"id": pid, "ids": list(tier_a), "junk": list(JUNK_TITLES)})

    return {b: (n, w) for b, n, w in rows}





_EXP = {}





def expected(pid):

    """(tier, score) recomputed from the raw nodes: Python for Tier A and BFS, Cypher over AUTHORED for the rest."""

    if pid in _EXP:

        return _EXP[pid]

    m = model()

    shared = shared_recent_small(pid, m["tier_a"])

    if pid in m["tier_a"]:

        tier = m["tier_a"][pid]

    elif any(n >= 2 for n, _ in shared.values()):

        tier = "B"

    elif m["dist"].get(pid, 99) <= 3:

        tier = "C"

    else:

        tier = "NONE"

    score = {"A1": 100, "A2": 50}.get(tier, 0) + sum(w for _, w in shared.values())

    _EXP[pid] = (tier, score)

    return _EXP[pid]





def sample():

    """Seeded sample over all tiers, plus the awkward cases. Cached."""

    if "sample" in _M:

        return _M["sample"]

    m = model()

    rng = random.Random(SEED)

    a1 = sorted(p for p, t in m["tier_a"].items() if t == "A1")

    a2 = sorted(p for p, t in m["tier_a"].items() if t == "A2")

    bott = sorted(p for p, ev in m["evidence"].items() if any(k[0] == "AFFILIATED" and k[2] in m["bottlers"] for k in ev))

    big = sorted(p for p, ev in m["evidence"].items()

                 if p in m["tier_a"] and m["tier_a"][p] == "A2" and all(m["works"][k[1]][4] > SMALL for k in ev))

    non_a = [p for p in m["dist"] if p not in m["tier_a"]]

    b = sorted(p for p in non_a if m["dist"][p] == 1)

    c = sorted(p for p in non_a if 2 <= m["dist"][p] <= 3)

    all_people = [r[0] for r in q("MATCH (p:Person) RETURN p.oa_id LIMIT 8000")]

    none = sorted(p for p in all_people if p not in m["dist"])

    junk_people = sorted({r[0] for r in q("MATCH (p:Person)-[:AUTHORED]->(w:Work) WHERE w.doi STARTS WITH '10.5256/' "

                                          "OR toLower(w.title) STARTS WITH 'correction:' OR toLower(w.title) STARTS WITH 'erratum' "

                                          "OR toLower(w.title) STARTS WITH 'corrigendum' RETURN DISTINCT p.oa_id")})

    changed = sorted({r[0] for r in q("MATCH (p:Person)-[:AUTHORED]->(w:Work) WHERE toLower(w.title) IN ['index', 'front matter'] "
                                      "AND ((w)-[:FUNDED_BY]->(:Funder {target:true}) OR (p)-[:AFFILIATED]->(:Org {target:true})) "
                                      "RETURN DISTINCT p.oa_id")})
    out = (changed + rng.sample(junk_people, min(12, len(junk_people))) + rng.sample(a1, 10) + rng.sample(a2, 10) + bott + rng.sample(big, min(3, len(big)))

           + rng.sample(b, 12) + rng.sample(c, 12) + rng.sample(none, 12))

    _M["sample"] = list(dict.fromkeys(out))

    return _M["sample"]





# ---------- 1. the read-only guard ----------

def test_ro_query_rejects_write_and_writes_nothing():

    try:

        research.ro_query(research.GRAPH, "CREATE (:ReviewerShouldNotExist)")

    except research.ToolError as e:

        assert e.code == "READ_ONLY", e.code

    else:

        raise AssertionError("ro_query accepted a write")

    assert q("MATCH (n:ReviewerShouldNotExist) RETURN count(n)")[0][0] == 0





def test_ro_query_row_cap_and_timeout():

    try:

        research.ro_query(research.GRAPH, "MATCH (p:Person) RETURN p.oa_id LIMIT 50", row_cap=10)

    except research.ToolError as e:

        assert e.code == "ROW_LIMIT"

    else:

        raise AssertionError("row cap not enforced")

    try:

        research.ro_query(research.GRAPH, "MATCH (a:Person), (b:Person) RETURN count(*)", timeout_ms=50)

    except research.ToolError as e:

        assert e.code == "TIMEOUT", e.code

    else:

        raise AssertionError("timeout not enforced")





def test_source_has_no_direct_writes_or_string_built_user_input():

    src = (ROOT / "research.py").read_text(encoding="utf-8")

    assert not re.search(r"\.query\(", src.replace(".ro_query(", "")), "a non-RO .query( call exists"

    assert not re.search(r"\b(CREATE|MERGE|DELETE|DETACH)\b", src), "write keyword in research.py"

    assert "%s" not in src and ".format(" not in src

    # every f-string that holds Cypher may only interpolate module constants / validated locals

    allowed = {"NOT_JUNK", "pattern", "where", "strength", "mid_cols", "edge_cols"}

    for m in re.finditer(r'f"[^"\n]*\{([^}]*)\}', src):

        for name in re.findall(r"\{([A-Za-z_][A-Za-z_0-9]*)", m.group(0)):

            if "MATCH" in m.group(0) or "WHERE" in m.group(0) or "RETURN" in m.group(0):

                assert name in allowed, f"unexpected interpolation {name} in {m.group(0)[:80]}"





def test_find_experts_bool_and_junk_args_return_envelope_not_exception():

    for call in (lambda: research.find_experts("obesity", True), lambda: research.check_no_tie("A1", True),

                 lambda: research.bridges("A1", 2, True), lambda: research.resolve_experts("Blair", "obesity")):

        out = call()  # must not raise

        assert out["ok"] is False and out["error"]["code"] in ("INVALID_ARG", "NOT_FOUND"), out





# ---------- 2. envelope ----------

def check_envelope(out):

    assert isinstance(out, dict) and out.get("ok") in (True, False)

    assert isinstance(out["cypher"], list)

    for e in out["cypher"]:

        assert set(e) >= {"graph", "query", "params"} and e["graph"] == research.GRAPH

    if out["ok"]:

        assert set(out) >= {"ok", "data", "cypher", "scope", "verdicts_applied", "warnings"}

        assert out["cypher"], "success without any executed Cypher"

    else:

        assert set(out["error"]) == {"code", "message"}

        assert out["error"]["code"] in ("INVALID_ARG", "NOT_FOUND", "AMBIGUOUS", "TIMEOUT", "ROW_LIMIT",

                                       "VERIFIER_REJECT", "NOT_APPROVED", "READ_ONLY")





def test_envelope_on_every_tool_success_and_error():

    pid = sample()[0]

    outs = [research.find_experts("obesity", 3), research.find_experts("T10010", 3), research.find_experts("", 3),

            research.tie_report(pid), research.tie_report("A0"), research.check_no_tie(pid),

            research.check_no_tie(pid, 9), research.bridges(pid), research.bridges(pid, 3), research.bridges(pid, 9),

            research.resolve_experts(["Steven N. Blair", "Nobody Atall"], "obesity"), research.resolve_experts([], "x")]

    for out in outs:

        check_envelope(out)

    assert outs[4]["error"]["code"] == "NOT_FOUND" and outs[6]["error"]["code"] == "INVALID_ARG"

    # the logged Cypher uses $parameters, not pasted values

    for out in outs:

        for e in out["cypher"]:

            assert pid not in e["query"] and "Blair" not in e["query"]





def test_check_no_tie_scope_block_and_query_name():

    pid = [p for p in sample() if expected(p)[0] == "NONE"][0]

    out = research.check_no_tie(pid, 3)

    assert out["data"]["result"] == "NO_TIE_FOUND" and out["data"]["query_name"] == "q3_bfs_v1"

    s = out["scope"]

    assert s["hops"] == 3 and s["years"] == "2021-2026" and 0 < s["coverage"] <= 1

    assert s["caveats"] and "BFS" not in json.dumps(out["data"])

    assert any("algo.BFS" in e["query"] for e in out["cypher"]), "Q3 must use algo.BFS"

    assert any("COAUTH_B" in e["query"] for e in out["cypher"])





# ---------- 3. tiers and scores recomputed independently ----------

def test_tier_a_set_equals_independent_recomputation():

    m = model()

    got = research._tier_a([])

    assert got == m["tier_a"], (len(got), len(m["tier_a"]),

                                list(set(got.items()) ^ set(m["tier_a"].items()))[:5])





def test_tier_and_score_for_sampled_experts():

    bad = []

    for pid in sample():

        tier, score = expected(pid)

        out = research.tie_report(pid)

        d = out["data"]

        if d["tier"] != tier or abs(d["score"] - score) > 1e-3:

            bad.append((pid, tier, round(score, 4), d["tier"], d["score"]))

    assert len(sample()) >= 60

    assert not bad, f"{len(bad)} of {len(sample())} differ, first: {bad[:5]}"





def test_sample_spans_all_tiers():

    seen = {expected(p)[0] for p in sample()}

    assert seen == {"A1", "A2", "B", "C", "NONE"}, seen





def test_evidence_matches_independent_and_has_labels():

    m = model()

    for pid in sample():

        out = research.tie_report(pid)

        ev = out["data"]["evidence"]

        want = m["evidence"].get(pid, {})

        got = {(e["kind"], e["work_id"], e["tier"], e["topic_label"] == "on-topic") for e in ev}

        exp = {(k[0], k[1], v[0], v[1]) for k, v in want.items()}

        assert got == exp, (pid, got ^ exp)

        for e in ev:

            assert e["topic_label"] in ("on-topic", "off-topic")

            assert not is_junk(e["doi"], m["works"][e["work_id"]][2])

            assert e["tier"] in ("A1", "A2") and e["reason"]





def test_bottler_is_a2_and_large_paper_is_a2():

    m = model()

    bott = [p for p, ev in m["evidence"].items()

            if any(k[0] == "AFFILIATED" and k[2] in m["bottlers"] for k in ev)]

    assert bott, "no bottler affiliation in the graph?"

    for pid in bott:

        out = research.tie_report(pid)["data"]

        assert all(e["tier"] == "A2" for e in out["evidence"] if "bottler" in e["reason"])

        assert any(e["reason"] == "bottler affiliation" for e in out["evidence"])





def test_tier_b_needs_two_small_recent_papers_and_dois_are_those_papers():

    m = model()

    checked = 0

    for pid in sample():

        d = research.tie_report(pid)["data"]

        if d["tier"] != "B":

            continue

        for t in d["coauth_ties"]:

            assert t["n_papers"] >= 2 and t["dois"]

            works = m["adj"][pid][t["to_expert_id"]][2]

            for wid in works:

                _, doi, _, year, n = m["works"][wid]

                assert year >= RECENT and n <= SMALL, (pid, wid, year, n)

            want = {m["works"][w][1] for w in works if m["works"][w][1]}

            assert set(t["dois"]) == want, (pid, t["dois"], want)

            checked += 1

    assert checked >= 5





def test_tier_c_uses_coauth_b_only_and_nearest_hops_exact():

    m = model()

    for pid in [p for p in sample() if expected(p)[0] == "C"]:

        d = research.tie_report(pid)["data"]

        assert d["nearest"]["hops"] == m["dist"][pid], (pid, d["nearest"], m["dist"][pid])





def test_score_formula_is_fixed_in_source():

    assert research.TIER_WEIGHT == {"A1": 100, "A2": 50}





# ---------- 4. Q3 against an independent breadth-first search ----------

def test_check_no_tie_matches_python_bfs_for_all_k():

    m = model()

    wrong = []

    for pid in sample():

        for k in (1, 2, 3):

            out = research.check_no_tie(pid, k)

            dist = m["dist"].get(pid)

            found = dist is not None and dist <= k

            if (out["data"]["result"] == "TIE_FOUND") != found:

                wrong.append((pid, k, dist, out["data"]["result"]))

            elif found and out["data"]["nearest"]["hops"] != dist:

                wrong.append((pid, k, dist, out["data"]["nearest"]))

    assert not wrong, f"{len(wrong)} mismatches: {wrong[:5]}"





def test_check_no_tie_extra_people_beyond_sample():

    """More NONE/C people, to be sure a 'no tie' answer is never wrong."""

    m = model()

    rng = random.Random(SEED + 1)

    people = [r[0] for r in q("MATCH (p:Person) RETURN p.oa_id SKIP 20000 LIMIT 3000")]

    for pid in rng.sample(people, 40):

        out = research.check_no_tie(pid, 3)

        dist = m["dist"].get(pid)

        assert (out["data"]["result"] == "TIE_FOUND") == (dist is not None and dist <= 3), (pid, dist)





def test_no_tie_wording_carries_scope_and_no_banned_words():

    pid = [p for p in sample() if expected(p)[0] == "NONE"][0]

    out = research.check_no_tie(pid, 2)

    w = out["data"]["wording"]

    assert "2 co-author hops" in w and "2021-2026" in w and "%" in w and "not proof" in w





# ---------- 5. bridges ----------

def bridge_count(pid, depth, tier_a, adj):

    n = 0

    for m1 in adj.get(pid, {}):

        for x in adj.get(m1, {}):

            if depth == 2:

                n += x in tier_a and x != pid

            else:

                if x == pid:

                    continue

                for a in adj.get(x, {}):

                    n += a in tier_a and a != pid and a != m1

    return n





def test_bridges_depth2_count_paths_dois():

    m = model()

    for pid in sample():

        out = research.bridges(pid, 2, 50)

        d = out["data"]

        assert d["bridge_count"] == bridge_count(pid, 2, m["tier_a"], m["adj"]), pid

        assert d["max_flow"] is None

        for p in d["paths"]:

            ids = p["node_ids"]

            assert ids[0] == pid and len(ids) == 3 and ids[2] in m["tier_a"] and ids[2] != pid

            assert p["anchor_tier"] == m["tier_a"][ids[2]]

            for i, e in enumerate(p["edges"]):

                w, dois, works = m["adj"][ids[i]][ids[i + 1]]

                assert set(e["dois"]) == {d_ for d_ in dois if d_}, (pid, e)

                assert abs(e["w"] - w) < 1e-3

                for wid in works:

                    assert m["works"][wid][3] >= RECENT and m["works"][wid][4] <= SMALL

            ws = [m["adj"][ids[i]][ids[i + 1]][0] for i in range(2)]

            assert abs(p["strength"] - min(ws)) < 1e-3

        strengths = [p["strength"] for p in d["paths"]]

        assert strengths == sorted(strengths, reverse=True)





def test_bridges_depth3_count_matches_python_on_people_with_bridges():

    m = model()

    checked = 0

    for pid in sample():

        want = bridge_count(pid, 3, m["tier_a"], m["adj"])

        if want == 0 and checked > 6:

            continue

        out = research.bridges(pid, 3, 50)

        assert out["ok"], out

        assert out["data"]["bridge_count"] == want, (pid, out["data"]["bridge_count"], want)

        for p in out["data"]["paths"]:

            ids = p["node_ids"]

            assert len(ids) == 4 and len(set(ids)) == 4 and ids[3] in m["tier_a"]

            for i in range(3):

                assert ids[i + 1] in m["adj"][ids[i]]

        checked += 1

    assert checked >= 10





# ---------- 6. junk handling ----------

def test_junk_titles_and_doi_prefix_never_appear_as_evidence():

    m = model()

    junk_ids = {w for w, r in m["works"].items() if is_junk(r[1], r[2])}

    assert len(junk_ids) >= 5

    funded_junk = {r[0] for r in q("MATCH (p:Person)-[:AUTHORED]->(w:Work)-[:FUNDED_BY]->(:Funder {target:true}) "

                                   "WHERE w.doi STARTS WITH '10.5256/' OR toLower(w.title) STARTS WITH 'correction:' "

                                   "OR toLower(w.title) STARTS WITH 'peer review report' RETURN DISTINCT p.oa_id")}

    for pid in sorted(funded_junk)[:20]:

        for e in research.tie_report(pid)["data"]["evidence"]:

            assert e["work_id"] not in junk_ids





def test_tier_b_not_built_on_junk_works():

    """A correction notice repeats the authors of the paper it corrects, so it must not count as a

    second paper. COAUTH_B (loader) currently includes them: reported by the Reviewer."""

    m = model()

    junk_ids = {w for w, r in m["works"].items() if is_junk(r[1], r[2])}

    bad = [(a, b) for a in m["adj"] for b, (w, dois, works) in m["adj"][a].items()

           if a < b and set(works) & junk_ids]

    assert not bad, f"{len(bad)} COAUTH_B pairs include a correction/peer-review record, e.g. {bad[:2]}"





def test_index_front_matter_excluded_and_real_untitled_papers_kept_as_evidence():

    """Index / Front Matter records are not papers: never Tier A evidence. An empty OpenAlex title is NOT a

    reason to drop a real paper: the two Coca-Cola-affiliated untitled papers must be Tier A evidence."""

    m = model()

    evid_works = {k[1] for ev in m["evidence"].values() for k in ev}

    nonpapers = {w for w, r in m["works"].items() if (r[2] or "").strip().lower() in ("index", "front matter")}

    assert len(nonpapers) >= 4, "the Index / Front Matter records must stay in the graph (flagged), not be dropped"

    assert not (nonpapers & evid_works), "Index/Front Matter counted as evidence in the model"

    for pid in {p for p, ev in m["evidence"].items() if any(k[1] in nonpapers for k in ev)}:

        pass  # (model already excludes them; the next loop checks the tool)

    for doi in ("10.1271/nogeikagaku1924.48.561", "10.13040/ijpsr.0975-8232.17(9).2652-59"):

        wid = [w for w, r in m["works"].items() if r[1] == doi]

        assert len(wid) == 1, doi

        assert wid[0] in evid_works, f"untitled real paper {doi} is not Tier A evidence"

        owners = [p for p, ev in m["evidence"].items() if any(k[1] == wid[0] for k in ev)]

        d = research.tie_report(owners[0])["data"]

        hit = [e for e in d["evidence"] if e["doi"] == doi]

        assert hit and d["tier"] in ("A1", "A2"), (doi, d["tier"])

        assert any(e.get("untitled") for e in hit), f"{doi}: evidence is not flagged untitled"

    # no tool output may use an Index / Front Matter record as evidence

    bad = [p for p in sample() for e in research.tie_report(p)["data"]["evidence"] if e["work_id"] in nonpapers]

    assert not bad, bad

    for w in nonpapers:

        n = q("MATCH (w:Work) WHERE w.oa_id = $w RETURN w.non_paper", {"w": w})[0][0]

        assert n is True, (w, n)

    assert q("MATCH (w:Work) WHERE w.non_paper = true RETURN count(w)")[0][0] == len(nonpapers)





# ---------- 7. coverage and caveats ----------

def test_coverage_matches_independent_recomputation():

    m = model()

    topic = m["on_topic"]

    funded = {r[0] for r in paged("MATCH (w:Work)-[:FUNDED_BY]->(:Funder) RETURN DISTINCT w.oa_id", "w.oa_id")}

    aff = {r[0] for r in paged("MATCH (:Person)-[a:AFFILIATED]->(:Org) RETURN DISTINCT a.work", "a.work")}

    out = research.check_no_tie(sample()[0])

    s = out["scope"]

    assert s["coverage"] == round(len(topic & (funded | aff)) / len(topic), 3)

    assert s["coverage_funder_only"] == round(len(topic & funded) / len(topic), 3)





def test_duplicate_caveat_uses_the_loader_title_normalisation():

    """The loader merges records on normalised title (lower case, punctuation stripped) + year +

    author set. A leftover-duplicate count must group by the same normalised title, or it misses some."""

    import loader

    m = model()

    groups = defaultdict(list)

    for wid, r in m["works"].items():

        t = loader.norm_title(r[2])

        if t:

            groups[(t, r[3])].append(wid)

    ids = {w for v in groups.values() if len(v) > 1 for w in v}

    n_groups = sum(1 for v in groups.values() if len(v) > 1)

    out = research.check_no_tie(sample()[0])

    text = out["scope"]["caveats"][0]

    assert text.startswith(f"{n_groups} title+year groups ({len(ids)} work records)"), (text, n_groups, len(ids))





def test_caveats_present_in_both_tools():

    for out in (research.check_no_tie(sample()[0]), research.tie_report(sample()[0])):

        c = " ".join(out["scope"]["caveats"])

        assert "duplicate" in c and "100 authors" in c and "other works are not loaded" in c





# ---------- 8. anonymity and banned words ----------

def all_outputs(ids):

    outs = [research.find_experts("obesity", 30), research.find_experts("T10352", 30)]

    for pid in ids:

        outs += [research.tie_report(pid), research.check_no_tie(pid, 3), research.bridges(pid, 2),

                 research.bridges(pid, 3, 5)]

    outs += [research.resolve_experts(["Steven N. Blair", "Maria Garcia", "Nobody Atall"], "obesity"),

             research.tie_report("A0"), research.check_no_tie(ids[0], 7), research.bridges(ids[0], 9),

             research.find_experts("", 3), research.find_experts("obesity", 0)]

    return outs





def test_no_banned_words_in_any_output():

    ids = sample()

    assert len(ids) >= 60

    for out in all_outputs(ids):

        text = json.dumps(out, ensure_ascii=False)

        assert not BANNED.search(text), BANNED.search(text).group(0)

        assert "independen" not in text.lower() and "conflicted" not in text.lower()





def test_no_person_names_unless_public_case():

    ids = sample()

    texts = [json.dumps(o, ensure_ascii=False) for o in all_outputs(ids)]

    blob = " ".join(texts)

    # every person id that appears in any output (experts, co-authors on paths, Tier-A people)

    shown = sorted(set(re.findall(r"A\d{6,}", blob)) | set(ids))

    assert len(shown) >= 60, f"only {len(shown)} person ids found in outputs"

    names = {}

    for i in range(0, len(shown), 1000):

        names.update({r[0]: r[1] for r in q("MATCH (p:Person) WHERE p.oa_id IN $ids RETURN p.oa_id, p.name",

                                           {"ids": shown[i:i + 1000]})})

    leaked = [(pid, n) for pid, n in names.items() if n and len(n) > 5 and n in blob]

    assert not leaked, f"{len(leaked)} names leaked, e.g. {leaked[:3]}"

    for d in research.find_experts("obesity", 30)["data"]["experts"]:

        assert re.fullmatch(r"Expert #\d+", d["label"])





def test_labels_are_numbered_by_default_and_names_only_when_public():

    pid = sample()[0]

    name = q("MATCH (p:Person) WHERE p.oa_id = $id RETURN p.name", {"id": pid})[0][0]

    for out in (research.tie_report(pid), research.check_no_tie(pid), research.bridges(pid)):

        assert re.fullmatch(r"Expert #\d+", out["data"]["label"]), out["data"]["label"]

    pub = research.tie_report(pid, public_case=True)

    assert pub["data"]["label"] == name

    assert research.tie_report(pid, label="Expert #7")["data"]["label"] == "Expert #7"





# ---------- 9. name lookup ----------

def test_resolve_experts_statuses():

    out = research.resolve_experts(["Steven N. Blair", "steven n. blair", "Nobody Atall", "Maria Garcia"], "obesity")

    st = {m["name_in_input"]: m for m in out["data"]["matches"]}

    assert st["Steven N. Blair"]["status"] == "MATCHED" and st["Steven N. Blair"]["expert_id"] == "A5105003630"

    assert st["steven n. blair"]["expert_id"] == "A5105003630"

    assert st["Nobody Atall"]["status"] == "NOT_FOUND" and st["Nobody Atall"]["expert_id"] is None

    assert st["Maria Garcia"]["status"] == "AMBIGUOUS" and st["Maria Garcia"]["expert_id"] is None

    assert len(st["Maria Garcia"]["candidates"]) >= 2





def test_resolve_experts_accents_fold():

    out = research.resolve_experts(["Jose Ramirez", "Angel Gutierrez"], "obesity")

    got = {m["name_in_input"]: m["status"] for m in out["data"]["matches"]}

    assert got["Angel Gutierrez"] != "NOT_FOUND" and got["Jose Ramirez"] != "NOT_FOUND", got





def test_resolve_experts_loose_match_is_not_reported_as_a_sure_match():

    """A name matched only on first+last word, to someone with no work on the topic, must not be MATCHED."""

    out = research.resolve_experts(["José Ramírez"], "obesity")

    m = out["data"]["matches"][0]

    assert not (m["status"] == "MATCHED" and m["candidates"][0]["works_on_topic"] == 0), m





def test_resolve_experts_first_last_match_uses_whole_words():

    """'Al Kim' must not be matched to 'Alon Eliakim' (STARTS WITH / ENDS WITH on raw text, not words)."""

    out = research.resolve_experts(["Al Kim"], "obesity")

    m = out["data"]["matches"][0]

    names = {r[0]: r[1] for r in q("MATCH (p:Person) WHERE p.oa_id IN $ids RETURN p.oa_id, p.name",

                                   {"ids": [c["expert_id"] for c in m["candidates"]] or ["x"]})}

    assert not any(n.lower() == "alon eliakim" for n in names.values()), (m["status"], names)





def test_resolve_experts_rejects_a_plain_string():

    out = research.resolve_experts("Blair", "obesity")

    assert out["ok"] is False and out["error"]["code"] == "INVALID_ARG"





# ---------- 10. verifier ----------

def claim(**k):

    base = {"expert_id": "A1", "text": "OpenAlex records Expert #1 as author of a work funded by X.", "kind": "tie",

            "evidence_refs": ["10.1/abc"]}

    base.update(k)

    return base





STORED = {"A1": ["10.1/abc", "10.2/def"]}





def reasons(c):

    return verifier.verify_claim(c, STORED["A1"])





def test_verifier_accepts_valid_tie_and_no_tie_claims():

    assert reasons(claim()) == []

    assert reasons(claim(evidence_refs=["https://doi.org/10.1/ABC"])) == []

    nt = {"expert_id": "A1", "kind": "no_tie", "text": "No tie found.", "query_name": "q3_bfs_v1",

          "scope": {"hops": 3, "years": "2021-2026", "coverage": 0.966, "funder_coverage": 0.563}}

    assert reasons(nt) == []

    assert reasons({k: v for k, v in nt.items() if k != "kind"}) == []





def test_verifier_rejects_unstored_or_missing_doi():

    assert reasons(claim(evidence_refs=["10.9/zzz"]))

    assert reasons(claim(evidence_refs=[]))

    assert reasons(claim(evidence_refs=None))

    assert reasons(claim(evidence_refs=["10.1/abc", "10.9/zzz"]))  # one bad ref spoils the claim

    assert reasons(claim(evidence_refs=["10.1/abc "])) == []  # whitespace tolerated

    r = verifier.verify_claims([claim(expert_id="A2")], STORED)  # DOIs stored for another expert

    assert r["status"] == "rejected" and r["rejections"][0]["code"] == "VERIFIER_REJECT"

    assert verifier.verify_claims([claim(), claim(evidence_refs=["10.9/x"])], STORED)["rejections"][0]["claim_index"] == 1





def test_verifier_no_tie_needs_query_name_and_full_scope():

    base = {"expert_id": "A1", "kind": "no_tie", "text": "No tie found.", "query_name": "q3_bfs_v1",

            "scope": {"hops": 3, "years": "2021-2026", "coverage": 0.5, "funder_coverage": 0.3}}

    assert reasons(base) == []

    for drop in ("query_name", "scope"):

        assert reasons({k: v for k, v in base.items() if k != drop}), drop

    for field in ("hops", "years", "coverage", "funder_coverage"):  # funder_coverage required since Oct 16

        assert reasons({**base, "scope": {k: v for k, v in base["scope"].items() if k != field}}), field

    assert reasons({**base, "query_name": "made_up"})

    assert reasons({**base, "scope": "everywhere"})

    assert reasons({**base, "scope": {"hops": 3, "years": "2021-2026", "coverage": None, "funder_coverage": 0.3}})
    assert reasons({**base, "scope": {"hops": 3, "years": "2021-2026", "coverage": 0.5, "funder_coverage": None}})
    assert reasons({**base, "scope": {**base["scope"], "funder_coverage": 0}}) == []  # 0% is a value, not missing

    assert reasons({"expert_id": "A1", "kind": "no_tie", "text": "No tie."}), "bare no_tie claim"

    # a no-tie claim must not slip through as a tie claim by dropping its kind and fields

    assert reasons({"expert_id": "A1", "text": "No tie found within 3 hops."}), "claim with no refs and no scope"





def test_verifier_banned_words_whole_word_case_insensitive():

    for t in ("This expert is independent.", "INDEPENDENT", "He is Conflicted", "not conflicted", "independent,"):

        assert any("banned" in r for r in reasons(claim(text=t))), t

    for t in ("independently", "interdependent", "unconflicted", "dependent"):

        assert not any("banned" in r for r in reasons(claim(text=t))), t





def test_verifier_does_not_crash_on_odd_input():

    assert reasons({"expert_id": "A1"})

    assert reasons(claim(text=None)) == []  # no text: nothing to ban, refs are fine

    assert verifier.verify_claims([], {})["status"] == "verified"





def test_verifier_known_query_name_is_the_one_check_no_tie_emits():

    pid = sample()[0]

    assert research.check_no_tie(pid)["data"]["query_name"] in verifier.KNOWN_QUERIES





def test_verifier_accepts_the_servers_own_positive_evidence():

    pid = [p for p in sample() if expected(p)[0] == "A1"][0]

    d = research.tie_report(pid)["data"]

    dois = [e["doi"] for e in d["evidence"] if e["doi"]]

    assert dois

    assert verifier.verify_claim(claim(evidence_refs=dois[:1], text=d["wording"]), dois) == []





# ---------- 11. loader changes ----------

def test_recent_small_dois_match_work_nodes_for_a_sample():

    m = model()

    rng = random.Random(SEED + 2)

    pairs = [(a, b) for a in m["adj"] for b in m["adj"][a] if a < b]

    for a, b in rng.sample(pairs, 150):

        w, dois, works = m["adj"][a][b]

        rows = q("MATCH (x:Person) WHERE x.oa_id = $a MATCH (x)-[:AUTHORED]->(w:Work)<-[:AUTHORED]-(y:Person) "

                 "WHERE y.oa_id = $b AND w.year >= 2021 AND w.n_authors <= 25 RETURN w.oa_id, w.doi, w.n_authors",

                 {"a": a, "b": b})

        assert sorted(r[0] for r in rows) == sorted(works), (a, b)

        assert sorted((r[1] or "") for r in rows) == sorted(dois), (a, b)

        assert abs(w - sum(1 / (r[2] - 1) for r in rows)) < 1e-3

        assert len(works) >= 2

        assert m["adj"][b][a][2] == works, "both directions must carry the same list"





def test_loader_unit_dedupe_prefers_record_with_doi_and_coauth_b_lists():

    import loader



    def w(wid, doi, year=2022):

        return {"id": f"https://openalex.org/{wid}", "doi": doi, "title": "Same Title", "publication_year": year,

                "authorships": [{"author": {"id": f"https://openalex.org/{a}", "display_name": a, "orcid": None},

                                 "author_position": "middle", "institutions": []} for a in ("A1", "A2")]}

    works = {"W1": w("W1", None), "W2": w("W2", "https://doi.org/10.1/X")}  # W1 has the lower id but no DOI

    loader.dedupe(works, {"W1": "topic", "W2": "topic"})

    assert list(works) == ["W2"], list(works)

    # recent_small_dois / recent_small_works hold only recent small papers

    old, new1, new2, big = w("W3", "https://doi.org/10.1/old", 2015), w("W4", "https://doi.org/10.1/n1"),         w("W5", "https://doi.org/10.1/n2"), w("W6", "https://doi.org/10.1/big")

    for i in range(30):

        big["authorships"].append({"author": {"id": f"https://openalex.org/Z{i}", "display_name": "z", "orcid": None},

                                   "author_position": "middle", "institutions": []})

    allw = {"W3": old, "W4": new1, "W5": new2, "W6": big}

    ids = {k: loader.author_slots(v) for k, v in allw.items()}

    rows = {(r["a"], r["b"]): r for r in loader.coauth_rows(allw, ids)}

    e = rows[("A1", "A2")]

    assert sorted(e["recent_small_dois"]) == ["10.1/n1", "10.1/n2"] and sorted(e["recent_small_works"]) == ["W4", "W5"]

    assert e["recent_small_papers"] == 2 and rows[("A2", "A1")]["recent_small_works"] == e["recent_small_works"]





def test_round2_real_untitled_papers_with_company_affiliation_are_kept():

    """A paper whose OpenAlex title is empty is still a paper (Coca-Cola Japan affiliation, DOI 10.1271/...).

    Dropping it deletes true Tier A evidence; flag it instead."""

    rows = q("MATCH (w:Work) WHERE w.doi IN ['10.1271/nogeikagaku1924.48.561', '10.13040/ijpsr.0975-8232.17(9).2652-59'] "

             "RETURN w.doi")

    assert len(rows) == 2, rows





def test_round2_wording_states_both_coverages_and_topic_label():

    pid = [p for p in sample() if expected(p)[0] == "NONE"][0]

    w = research.check_no_tie(pid, 3)["data"]["wording"]

    assert "56" in w and "97" in w, w

    a1 = [p for p in sample() if expected(p)[0] in ("A1", "A2")][:10]

    for p in a1:

        assert "topic" in research.tie_report(p)["data"]["wording"], p





def test_round2_anchor_count_is_the_true_count():

    m = model()

    for pid in [p for p in sample() if expected(p)[0] == "C"][:8]:

        d = research.tie_report(pid)["data"]["nearest"]

        seen, frontier = {pid}, {pid}

        for _ in range(d["hops"]):

            frontier = {y for x in frontier for y in m["adj"].get(x, {})} - seen

            seen |= frontier

        assert d["anchor_count"] == len(seen & set(m["tier_a"])), (pid, d, len(seen & set(m["tier_a"])))





def test_round2_verifier_words_and_doi_forms():

    assert any("independen" in r for r in reasons(claim(text="He acts independently.")))

    assert any("independen" in r for r in reasons(claim(text="Their independence is clear.")))

    assert reasons(claim(text="an interdependent network")) == []

    for ref in ("https://doi.org/10.1/abc", "http://doi.org/10.1/abc", "https://dx.doi.org/10.1/ABC", "doi:10.1/abc"):

        assert reasons(claim(evidence_refs=[ref])) == [], ref





def test_round2_db_error_and_arg_types():

    assert research.tie_report(123)["error"]["code"] == "INVALID_ARG"

    assert research.find_experts("obesity", True)["error"]["code"] == "INVALID_ARG"

    assert research.bridges("A1", 2, True)["error"]["code"] == "INVALID_ARG"

    out = research.resolve_experts("Blair", "obesity")

    assert out["error"]["code"] == "INVALID_ARG"

    try:

        research.ro_query(research.GRAPH, "THIS IS NOT CYPHER")

    except research.ToolError as e:

        assert e.code == "DB_ERROR", e.code





def test_round2_limit_append_does_not_break_aggregate_or_order_queries():

    r = research.ro_query(research.GRAPH, "MATCH (p:Person) RETURN count(p)")

    assert r[0][0] > 80000

    r = research.ro_query(research.GRAPH, "MATCH (p:Person) RETURN p.oa_id ORDER BY p.oa_id", row_cap=5) if False else None

    try:

        research.ro_query(research.GRAPH, "MATCH (p:Person) RETURN p.oa_id ORDER BY p.oa_id", row_cap=5)

    except research.ToolError as e:

        assert e.code == "ROW_LIMIT"

    else:

        raise AssertionError("row cap missing")





def test_round2_name_norm_collisions_and_initials():

    assert q("MATCH (p:Person) WHERE p.name_norm IS NULL RETURN count(p)")[0][0] == 0

    out = research.resolve_experts(["S. N. Blair", "Jose Ramirez", "Al Kim", "Steven N. Blair", "Maria Garcia"], "obesity")

    st = {m["name_in_input"]: m for m in out["data"]["matches"]}

    assert st["Steven N. Blair"]["status"] == "MATCHED" and st["Steven N. Blair"]["expert_id"] == "A5105003630"

    assert st["Jose Ramirez"]["status"] != "NOT_FOUND" and st["Jose Ramirez"]["status"] != "MATCHED"

    assert st["Al Kim"]["status"] in ("NOT_FOUND", "AMBIGUOUS") and st["Al Kim"]["expert_id"] is None

    assert st["Maria Garcia"]["expert_id"] is None





# ---------- 12. speed ----------

def test_each_tool_under_3_seconds_cold_and_warm():

    pid = [p for p in sample() if expected(p)[0] == "A2"][0]

    hub = q("MATCH (a:Person)-[:COAUTH_B]->(b) WITH a, count(b) AS d ORDER BY d DESC LIMIT 1 RETURN a.oa_id")[0][0]

    calls = {"find_experts": "research.find_experts('obesity')", "tie_report": f"research.tie_report('{pid}')",

             "tie_report_hub": f"research.tie_report('{hub}')", "check_no_tie": f"research.check_no_tie('{pid}')",

             "bridges2_hub": f"research.bridges('{hub}', 2, 50)", "bridges3_hub": f"research.bridges('{hub}', 3, 50)",

             "resolve": "research.resolve_experts(['Steven N. Blair'], 'obesity')"}

    slow = []

    for name, expr in calls.items():

        code = (f"import sys,time; sys.path.insert(0, r'{ROOT}'); import research; t=time.time(); o={expr}; "

                f"c=time.time()-t; t=time.time(); o={expr}; w=time.time()-t; print(o['ok'], c, w)")

        res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=os.environ)

        ok, cold, warm = res.stdout.split()[-3:]

        print(f"      {name}: cold {float(cold):.2f}s warm {float(warm):.2f}s")

        if ok != "True" or float(cold) >= 3 or float(warm) >= 3:

            slow.append((name, ok, cold, warm))

    assert not slow, slow





def test_explain_shows_index_use_for_per_expert_queries():

    """Each per-expert query must start from the Person index, never from a label scan of all works."""

    bad = []

    # checkpoint 7 (spec 9f): the per-expert queries take $ids (the expert plus merged profiles) and $x_works
    for name, query, params in (("COAUTH_TIES_Q", research.COAUTH_TIES_Q, {"ids": ["A1"], "tier_a": ["A2"]}),

                                ("FUNDED_EVIDENCE_Q", research.FUNDED_EVIDENCE_Q, {"ids": ["A1"], "x_works": ["W1"], **research.JUNK_PARAMS}),

                                ("AFFIL_EVIDENCE_Q", research.AFFIL_EVIDENCE_Q, {"ids": ["A1"], "x_works": ["W1"], **research.JUNK_PARAMS}),

                                ("EXPERT_FUNDER_Q", research.EXPERT_FUNDER_Q, {"ids": ["A1", "A2"], "x_works": ["W1"]})):

        plan = str(G.explain(query, params))

        if "Node By Label Scan" in plan or "Index Scan" not in plan:

            bad.append(name)

    assert not bad, f"label scan / no index on: {bad}"


def test_bridges_tier_a_filter_is_a_hash_join_not_a_per_path_list_scan():
    """Reviewer, Oct 17: the 3-s speed gate does not catch a revert to `a.oa_id IN $tier_a` (cold 1.3 s), so pin
    the plan: both bridges queries (paths and count) join the Tier-A people on id(a) with a Value Hash Join."""
    hub = q("MATCH (a:Person)-[:COAUTH_B]->(b) WITH a, count(b) AS d ORDER BY d DESC LIMIT 1 RETURN a.oa_id")[0][0]
    for depth in (2, 3):
        out = research.bridges(hub, depth, 5)
        assert out["ok"], out
        qs = [c for c in out["cypher"] if "COAUTH_B" in c["query"] and "$tier_a" in c["query"]]
        assert len(qs) == 2, [c["query"] for c in qs]  # paths + count
        for c in qs:
            assert "a.oa_id IN $tier_a" not in c["query"], c["query"]
            plan = str(G.explain(c["query"], c["params"]))
            assert "Value Hash Join" in plan, (depth, plan)


def test_bridges_order_is_total_so_the_limit_keeps_the_same_paths():
    """Polish, Oct 17: (strength, Tier-A id) ties in hubs' top 50, so the order also uses the co-authors' ids;
    the result must be sorted by that full key and the same on a second call."""
    hub = q("MATCH (a:Person)-[:COAUTH_B]->(b) WITH a, count(b) AS d ORDER BY d DESC LIMIT 1 RETURN a.oa_id")[0][0]
    for depth in (2, 3):
        out = research.bridges(hub, depth, 50)
        assert out["ok"], out
        paths = out["data"]["paths"]
        keys = [(-p["strength"], p["node_ids"][-1], *p["node_ids"][1:-1]) for p in paths]
        assert keys == sorted(keys) and len(set(keys)) == len(keys), (depth, keys[:5])
        tied = len(keys) - len({k[:2] for k in keys})
        assert depth == 2 or tied > 0, "fixture: the biggest hub should have (strength, Tier-A) ties at depth 3"
        assert research.bridges(hub, depth, 50)["data"]["paths"] == paths, depth





# ---------- 12. hardening round (Aaryan, Oct 16): funder-only coverage, shared BFS ----------

def test_hardening_no_tie_scope_has_graph_and_expert_funder_coverage():
    """funder_coverage = topic works with any funder / topic works (graph level, never hard-coded);
    expert_funder_coverage = the expert's non-junk works with any funder / the expert's non-junk works."""
    m = model()
    topic = m["on_topic"]
    funded = {r[0] for r in paged("MATCH (w:Work)-[:FUNDED_BY]->(:Funder) RETURN DISTINCT w.oa_id", "w.oa_id")}
    graph_cov = round(len(topic & funded) / len(topic), 3)
    picks = [p for p in sample() if expected(p)[0] == "NONE"][:3] + [p for p in sample() if expected(p)[0] == "A1"][:2]
    for pid in picks:
        out = research.check_no_tie(pid)
        s = out["scope"]
        assert s["funder_coverage"] == s["coverage_funder_only"] == graph_cov, (s["funder_coverage"], graph_cov)
        assert s["coverage"] != s["funder_coverage"]  # the old figure also counts affiliations
        works = {r[0] for r in q("MATCH (p:Person)-[:AUTHORED]->(w:Work) WHERE p.oa_id = $id AND w.junk = false "
                                 "RETURN DISTINCT w.oa_id", {"id": pid})}
        assert s["expert_works"] == len(works), (pid, s["expert_works"], len(works))
        assert s["expert_funder_coverage"] == (round(len(works & funded) / len(works), 3) if works else None), pid
        if out["data"]["result"] == "NO_TIE_FOUND":
            w = out["data"]["wording"]
            mine = f"{len(works & funded)} of " if works else "has no loaded paper"
            assert f"only {round(100 * graph_cov)}% of topic works" in w and mine in w, w
            claim = {"expert_id": pid, "kind": "no_tie", "text": w, "query_name": out["data"]["query_name"],
                     "scope": s}
            assert verifier.verify_claim(claim, []) == [], "the server's own no-tie scope must pass the Verifier"


def test_hardening_searched_hops_skips_only_the_repeated_bfs():
    nones = [p for p in sample() if expected(p)[0] == "NONE"][:3]
    for pid in nones:
        full, shared = research.check_no_tie(pid), research.check_no_tie(pid, searched_hops=3)
        assert (full["data"], full["scope"]) == (shared["data"], shared["scope"]), pid
        assert sum("algo.BFS" in c["query"] for c in full["cypher"]) == 3
        assert not any("algo.BFS" in c["query"] for c in shared["cypher"])
    cs = [p for p in sample() if expected(p)[0] == "C"][:2]
    for pid in cs:  # searched_hops below k still searches
        assert research.check_no_tie(pid, searched_hops=1)["data"]["result"] == "TIE_FOUND", pid
        assert research.check_no_tie(pid)["data"]["result"] == "TIE_FOUND", pid


def test_hardening_tier_basis_and_score_basis_labels():
    for pid in sample()[:60]:
        d = research.tie_report(pid)["data"]
        assert d["tier_basis"] == research.TIER_BASIS[d["tier"]] and d["score_basis"] == research.SCORE_BASIS
        if d["tier"] in ("A1", "A2"):
            assert d["tier_basis"] == "direct evidence"
        if d["tier"] in ("B", "C"):
            assert "not validated" in d["tier_basis"] and "not validated" in d["wording"], d["wording"]
        assert not BANNED.search(d["wording"] + d["tier_basis"] + d["score_basis"])


# ---------- runner ----------

if __name__ == "__main__":

    only = sys.argv[1:]  # optional: names (substrings) of the tests to run

    tests = [(n, f) for n, f in list(globals().items()) if n.startswith("test_") and callable(f)

             and (not only or any(o in n for o in only))]

    failed = 0

    for name, fn in tests:

        t0 = time.time()

        try:

            fn()

            print(f"PASS  {name}  ({time.time() - t0:.1f}s)")

        except Exception as e:

            failed += 1

            msg = str(e) or type(e).__name__

            line = [f.lineno for f in traceback.extract_tb(e.__traceback__) if f.filename == __file__][-1]

            print(f"FAIL  {name}  (line {line}): {msg[:400]}")

    print(f"\n{len(tests) - failed}/{len(tests)} passed")

    sys.exit(1 if failed else 0)

