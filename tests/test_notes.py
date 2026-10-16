"""Tests for checkpoint 7: verdicts, sourcing notes, the Writer (record_verdict, draft_note, approve_note).

Run:  FALKORDB_PORT=6379 uv run python tests/test_notes.py [name-substring ...]
Builder's tests (Aaryan, Oct 16; the Reviewer adds more). Scratch graphs newsroom:zz-test-notes* are deleted at
the end; the research graphs are only read. Expected values come from direct Cypher on the research graph,
not from research.py. No API calls.
"""
import json
import os
import re
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["TIETRACE_NEWSROOM"] = "zz-test-notes"  # never the real alpha newsroom
import memory  # noqa: E402
import obsidian_export  # noqa: E402
import research  # noqa: E402
import server  # noqa: E402
import verifier  # noqa: E402

DB = research._conn()
NR, RG = "newsroom:zz-test-notes", "research:obesity-coke"
HILL, BLAIR, NONE_EXPERT = "A5031318756", "A5105003630", "A5067322664"
S_N_BLAIR = "A5087575650"  # "S. N. Blair": a split profile of Steven N. Blair with no evidence of its own
# A 13-author 2022 paper funded by Coca-Cola; it is the only Tier A evidence of each of its authors.
DOI = "10.1016/j.foodhyd.2022.107657"
WORK = "W4220716768"
X1, X2 = "A5071304044", "A5089714758"  # two of its authors
BANNED = re.compile(r"\b(independent|conflicted|unbiased|clean sources?)\b", re.I)


def q(graph, query, params=None):
    return DB.select_graph(graph).query(query, params or {}).result_set


def ok(out):
    assert out["ok"] is True, out.get("error")
    assert set(out) >= {"ok", "data", "cypher", "scope", "verdicts_applied", "warnings"}
    return out["data"]


def code(out):
    assert out["ok"] is False, out
    return out["error"]["code"]


def fresh(name="zz-test-notes"):
    """An empty scratch newsroom (verdicts are newsroom-wide, so each test starts clean)."""
    g = f"newsroom:{name}"
    if g in DB.list_graphs():
        DB.select_graph(g).delete()
    memory.use_newsroom(name)
    memory.init()


def target_works(pid):
    """Independent: the person's non-junk works funded by a target funder or carrying a target affiliation."""
    funded = q(RG, "MATCH (p:Person {oa_id: $id})-[:AUTHORED]->(w:Work)-[:FUNDED_BY]->(:Funder {target: true}) "
                   "WHERE NOT w.junk RETURN DISTINCT w.oa_id", {"id": pid})
    affil = q(RG, "MATCH (p:Person {oa_id: $id})-[a:AFFILIATED]->(:Org {target: true}) WITH a.work AS wid "
                  "MATCH (w:Work {oa_id: wid}) WHERE NOT w.junk RETURN DISTINCT w.oa_id", {"id": pid})
    return {r[0] for r in funded + affil}


# ---------- the gate: input, check, verdict, note, approval ----------

def test_gate_full_demo_run():
    fresh()
    assert target_works(X1) == {WORK} and target_works(X2) == {WORK}, "fixture: the paper is their only evidence"
    c = ok(server.open_case("Gate: sugar and sleep", "Coca-Cola", "obesity", [X1, X2, HILL, NONE_EXPERT]))
    cid = c["case_id"]
    res = ok(server.check_case(case_id=cid))
    before = {r["expert_id"]: (r["tier"], r["score"], r["check_id"]) for r in res["results"]}
    print(f"      check_case: {[(r['label'], r['tier'], r['score']) for r in res['results']]}")
    assert before[X1][0] == before[X2][0] == "A1" and before[HILL][0] == "A2" and before[NONE_EXPERT][0] == "NONE"

    v = server.record_verdict("PAPER_EXCLUDED", "https://doi.org/" + DOI.upper(), "funder misattributed in OpenAlex",
                              "Aaryan")
    d = ok(v)
    vid, imp = d["verdict_id"], d["impact"]
    print(f"      record_verdict: {vid}, checks_changed {imp['checks_changed']}, experts_changed "
          f"{imp['experts_changed']}, rechecked {imp['checks_rechecked']}")
    for x in imp["details"]:
        print(f"        {x['label']}: {x['tier_before']} {x['score_before']} -> {x['tier_after']} {x['score_after']}")
    assert v["verdicts_applied"] == [vid] and d["target"] == DOI
    assert imp["checks_rechecked"] == 4 and imp["not_rechecked"] == []
    changed = {x["expert_id"]: x for x in imp["details"]}
    assert set(changed) == {X1, X2}, changed  # Hill and the NONE expert do not change
    for e in (X1, X2):
        assert changed[e]["tier_before"] == "A1" and changed[e]["tier_after"] != "A1"
        assert changed[e]["score_after"] < changed[e]["score_before"] - 99
    # AFFECTS edges carry before -> after; each changed Check is superseded by one made with the verdict
    rows = q(NR, "MATCH (v:Verdict {id: $v})-[a:AFFECTS]->(k:Check) RETURN k.id, a.tier_before, a.tier_after, "
                 "a.score_before, a.score_after, a.new_check_id", {"v": vid})
    assert {r[0] for r in rows} == {before[X1][2], before[X2][2]}
    for kid, tb, ta, sb, sa, new in rows:
        x = next(x for x in imp["details"] if x["check_id"] == kid)
        assert (tb, ta, sb, sa, new) == (x["tier_before"], x["tier_after"], x["score_before"], x["score_after"],
                                         x["new_check_id"])
        diff, verdicts = q(NR, "MATCH (n:Check {id: $n})-[s:SUPERSEDES]->(:Check {id: $o}) RETURN s.diff_json, n.verdicts",
                           {"n": new, "o": kid})[0]
        assert json.loads(diff)["cause"] == "verdict" and verdicts == [vid]
    vnode = q(NR, "MATCH (v:Verdict {id: $v}) RETURN v.type, v.doi, v.reason, v.reviewer, v.checks_changed, "
                  "v.expert_ids", {"v": vid})[0]
    assert vnode == ["PAPER_EXCLUDED", DOI, "funder misattributed in OpenAlex", "Aaryan", 2, sorted([X1, X2])]

    # the Writer's first draft: one claim cites the excluded paper, one says "clean source"
    hill = ok(server.tie_report(HILL, expert_number=3))
    hill_dois = [e["doi"] for e in hill["evidence"] if e["doi"]][:2]
    claims = [{"expert_id": HILL, "text": "Ties found in the research record: OpenAlex records Expert #3 as author "
                                          "of works funded by the Coca-Cola Foundation.", "evidence_refs": hill_dois},
              {"expert_id": X1, "text": "OpenAlex records Expert #1 as author of a Coca-Cola funded work.",
               "evidence_refs": [DOI]},
              {"expert_id": NONE_EXPERT, "text": "No tie found in the research record for Expert #4 within the "
                                                 "stated scope; a clean source.", "evidence_refs": ["q3_bfs_v1"]}]
    d1 = ok(server.draft_note(cid, claims, agent="writer"))
    print(f"      draft_note 1: {d1['status']}, rejections {[(r['claim_index'], r['reason']) for r in d1['rejections']]}")
    assert d1["status"] == "rejected" and {r["claim_index"] for r in d1["rejections"]} == {1, 2}
    assert code(server.approve_note(d1["note_id"], "Aaryan")) == "NOT_APPROVED"

    claims = [claims[0], {"expert_id": NONE_EXPERT, "text": "No tie found in the research record for Expert #4 "
                                                            "within the stated scope.", "evidence_refs": ["q3_bfs_v1"]}]
    d2 = ok(server.draft_note(cid, claims, agent="writer"))
    print(f"      draft_note 2: {d2['status']} ({d2['note_id']})")
    assert d2["status"] == "verified" and d2["rejections"] == []
    a = ok(server.approve_note(d2["note_id"], "Aaryan"))
    md = a["markdown"]
    print("      approve_note markdown:\n" + "\n".join("        | " + line for line in md.splitlines()))
    assert a["status"] == "approved" and a["approved_by"] == "Aaryan"
    for doi in hill_dois:
        assert f"(https://doi.org/{doi})" in md, doi
    # since the checkpoint-7 review: every expert of the case has a section and a disclosure line
    assert all(f"## Expert #{i}" in md for i in (1, 2, 3, 4)) and md.count("Disclosure:") == 4
    assert "q3_bfs_v1" in md and "co-author hops" in md and "% of topic works list a funder" in md
    assert vid in md and not BANNED.search(md)
    one = md.split("## Expert #1")[1].split("## ")[0]  # Expert #1 lost its only evidence to the verdict
    assert "No claim in this note" in one and "no tie found in the research record" in one, one
    names = research.names_of([], [HILL, NONE_EXPERT])  # the note must name nobody (not a public case)
    for n in names.values():
        assert n not in md
    status = q(NR, "MATCH (:Case {id: $c})-[:HAS_NOTE]->(n:Note {id: $n}) RETURN n.status, n.approved_by",
               {"c": cid, "n": d2["note_id"]})[0]
    assert status == ["approved", "Aaryan"]
    kinds = [t["kind"] for t in ok(server.recall(case_id=cid, limit=100))["timeline"]]
    assert {"check", "event", "verdict", "note"} <= set(kinds), kinds


# ---------- verdicts change every research answer, as documented ----------

def test_paper_excluded_applies_to_every_research_tool_and_is_listed():
    fresh()
    base = ok(server.tie_report(X1))
    assert base["tier"] == "A1" and WORK in {e["work_id"] for e in base["evidence"]}
    vid = ok(server.record_verdict("PAPER_EXCLUDED", DOI, "test", "Aaryan"))["verdict_id"]
    out = server.tie_report(X1)
    d = ok(out)
    assert out["verdicts_applied"] == [vid] and WORK not in {e["work_id"] for e in d["evidence"]}
    assert d["tier"] != "A1" and out["cypher"][0]["graph"] == NR and "Verdict" in out["cypher"][0]["query"]
    for tool, args in (("check_no_tie", (X1,)), ("bridges", (BLAIR,)), ("check_case", ([X1, HILL],))):
        assert getattr(server, tool)(*args)["verdicts_applied"] == [vid], tool
    for out in (server.find_experts("obesity", 3, True), server.resolve_experts(["Steven N. Blair"], "obesity")):
        assert ok(out) is not None and out["verdicts_applied"] == []  # these do not judge ties
    # the excluded paper's other authors left the Tier-A set, so they are not Tier-A co-authors any more
    with research.on_graph(RG):
        assert X2 in research._tier_a([])  # no verdict context: unchanged
        with research.with_verdicts({"ids": [vid], "dois": [DOI], "merges": []}):
            assert X1 not in research._tier_a([]) and X2 not in research._tier_a([])
        assert X2 in research._tier_a([])


def test_paper_exclusion_takes_exactly_the_paper_out_of_the_score():
    """Independent model: the score drop equals the excluded paper's share 1/(n-1) for each Tier-A co-author
    on it (a paper with no target funder or affiliation, so the Tier-A set itself does not change)."""
    rows = q(RG, "MATCH (h:Person {oa_id: $id})-[:AUTHORED]->(w:Work)<-[:AUTHORED]-(b:Person) "
                 "WHERE b <> h AND NOT w.junk AND w.year >= 2021 AND w.n_authors <= 25 AND w.n_authors > 1 AND w.doi IS NOT NULL "
                 "AND NOT (w)-[:FUNDED_BY]->(:Funder {target: true}) "
                 "RETURN w.oa_id, w.doi, w.n_authors, collect(DISTINCT b.oa_id)", {"id": BLAIR})
    with research.on_graph(RG):
        tier_a = research._tier_a([])
        pick = next((r for r in rows if set(r[3]) & set(tier_a)
                     and not q(RG, "MATCH (:Person)-[a:AFFILIATED]->(:Org {target: true}) WHERE a.work = $w "
                                   "RETURN count(a)", {"w": r[0]})[0][0]), None)
        assert pick, "fixture: a recent small co-authored paper with no company link"
        wid, doi, n, coauthors = pick
        before = research.tie_report(BLAIR)["data"]
        with research.with_verdicts({"ids": ["vrd_test"], "dois": [doi], "merges": []}):
            after = research.tie_report(BLAIR)
    expected = before["score"] - sum(1 / (n - 1) for b in coauthors if b in tier_a)
    assert abs(after["data"]["score"] - expected) < 1e-3, (before["score"], after["data"]["score"], expected)
    assert after["verdicts_applied"] == ["vrd_test"] and after["data"]["tier"] == "A1"
    for t in after["data"]["coauth_ties"]:
        assert doi not in t["dois"]


def test_bridges_drop_a_link_that_rested_on_the_excluded_paper():
    with research.on_graph(RG):
        rows = research.ro_query(RG, "MATCH (a:Person)-[c:COAUTH_B]->(b:Person) WHERE size(c.recent_small_works) = 2 "
                                     "AND all(d IN c.recent_small_dois WHERE d <> '') RETURN a.oa_id LIMIT 300",
                                 row_cap=400)
        for (pid,) in rows:
            out = research.bridges(pid, 2, 5)
            two = [p for p in out["data"]["paths"] if len(p["edges"][0]["dois"]) == 2] if out["ok"] else []
            if two:
                break
        assert two, "fixture: a bridge path whose first link has exactly 2 papers"
        doi = two[0]["edges"][0]["dois"][0]
        with research.with_verdicts({"ids": ["vrd_test"], "dois": [doi], "merges": []}):
            after = research.bridges(pid, 2, 5)
    assert all(doi not in e["dois"] for p in after["data"]["paths"] for e in p["edges"])
    assert any("left out" in w for w in after["warnings"]), after["warnings"]


def test_profile_merge_combines_evidence_for_either_id():
    fresh()
    assert ok(server.tie_report(S_N_BLAIR))["tier"] == "NONE"
    c = ok(server.open_case("Merge", "Coca-Cola", "obesity", [S_N_BLAIR]))
    ok(server.check_case(case_id=c["case_id"]))
    d = ok(server.record_verdict("PROFILE_MERGE", [BLAIR, S_N_BLAIR], "same person (split profile)", "Aaryan"))
    assert d["target"] == ",".join(sorted([BLAIR, S_N_BLAIR]))
    assert [(x["tier_before"], x["tier_after"]) for x in d["impact"]["details"]] == [("NONE", "A1")]
    a, b = ok(server.tie_report(BLAIR)), ok(server.tie_report(S_N_BLAIR))
    assert a["tier"] == b["tier"] == "A1" and a["score"] == b["score"]
    blair_works = target_works(BLAIR) | target_works(S_N_BLAIR)
    assert {e["work_id"] for e in b["evidence"]} == blair_works
    nt = ok(server.check_no_tie(S_N_BLAIR))
    assert nt["result"] == "TIE_FOUND" and nt["nearest"]["hops"] == 0
    assert code(server.record_verdict("PROFILE_MERGE", [S_N_BLAIR, BLAIR], "again", "Aaryan")) == "INVALID_ARG"


def test_record_verdict_validates_strictly():
    fresh()
    bad = [server.record_verdict("PAPER_DELETED", DOI, "r", "x"), server.record_verdict("PAPER_EXCLUDED", "abc", "r", "x"),
           server.record_verdict("PAPER_EXCLUDED", [DOI], "r", "x"), server.record_verdict("PAPER_EXCLUDED", DOI, " ", "x"),
           server.record_verdict("PAPER_EXCLUDED", DOI, "r", ""), server.record_verdict("PROFILE_MERGE", [BLAIR], "r", "x"),
           server.record_verdict("PROFILE_MERGE", [BLAIR, BLAIR], "r", "x"),
           server.record_verdict("PROFILE_MERGE", [BLAIR, "bob"], "r", "x"),
           server.record_verdict("PAPER_EXCLUDED", "10.9999/not-in-graph", "r", "x"),
           server.record_verdict("PROFILE_MERGE", [BLAIR, "A99999999999"], "r", "x"),
           server.record_verdict("PAPER_EXCLUDED", DOI, "r", "x", case="no-such-case")]
    assert [code(b) for b in bad] == ["INVALID_ARG"] * 8 + ["NOT_FOUND", "NOT_FOUND", "NO_GRAPH"]
    assert "Verdict" not in str(q(NR, "MATCH (n) RETURN DISTINCT labels(n)"))  # nothing written


def test_recheck_names_verdict_as_the_cause():
    """A verdict recorded without re-running a check (as when the time budget runs out): recheck says why."""
    fresh()
    c = ok(server.open_case("Recheck cause", "Coca-Cola", "obesity", [X1]))
    chk = ok(server.tie_report(X1, case_id=c["case_id"]))["check_id"]
    memory.save_verdict([], memory.new_id("vrd"), "PAPER_EXCLUDED", DOI, DOI, [], "r", "Aaryan", [], 0,
                        "obesity-coke", None)
    d = ok(server.recheck(chk))
    assert d["diff"]["cause"] == "verdict" and d["diff"]["changed"] and not d["diff"]["rebuilt"]
    assert d["before"]["verdicts"] == [] and len(d["after"]["verdicts"]) == 1
    assert "context only" in d["diff"]["score_basis"]
    assert not any("server code change" in w for w in server.recheck(d["check_id"])["warnings"])


# ---------- draft_note: the Verifier is enforced ----------

def test_draft_note_rejects_every_rule_break():
    fresh()
    c = ok(server.open_case("Rules", "Coca-Cola", "obesity", [HILL, NONE_EXPERT, BLAIR]))
    cid = c["case_id"]
    hill = ok(server.tie_report(HILL))
    good = [e["doi"] for e in hill["evidence"] if e["doi"]][:1]
    blair = ok(server.tie_report(BLAIR))
    tier_b_doi = next((d for t in blair["coauth_ties"] for d in t["dois"]
                       if d not in {e["doi"] for e in blair["evidence"]}), None)
    name = research.names_of([], [HILL])[HILL]
    with research.on_graph(RG):
        last = name.split()[-1]
    cases = [
        ({"expert_id": HILL, "text": f"{name} has ties found in the research record.", "evidence_refs": good}, "names"),
        ({"expert_id": HILL, "text": f"Dr {last} has ties found in the research record.", "evidence_refs": good}, "names"),
        ({"expert_id": HILL, "text": "Expert #2 has ties found in the research record.", "evidence_refs": good}, "mentions"),
        ({"expert_id": HILL, "text": "Expert #1 is unbiased.", "evidence_refs": good}, "banned"),
        ({"expert_id": HILL, "text": "Expert #1 is not conflicted.", "evidence_refs": good}, "banned"),
        ({"expert_id": HILL, "text": "Expert #1: ties found.", "evidence_refs": ["10.1234/made.up"]}, "not a stored"),
        ({"expert_id": HILL, "text": "Expert #1: ties found.", "evidence_refs": []}, "no evidence_refs"),
        ({"expert_id": HILL, "text": "No tie found for Expert #1.", "evidence_refs": ["q3_bfs_v1"]}, "found a tie"),
        ({"expert_id": NONE_EXPERT, "text": "No tie found for Expert #2.", "evidence_refs": ["q3_bfs_v1", good[0]]},
         "cites only its query_name"),
        ({"expert_id": NONE_EXPERT, "text": "No tie found for Expert #2.", "evidence_refs": ["q3_bfs_v9"]}, "not a stored"),
    ]
    assert tier_b_doi, "fixture: a Tier B DOI of Blair that is not also his Tier A evidence"
    cases.append(({"expert_id": BLAIR, "text": "Expert #3: ties found.", "evidence_refs": [tier_b_doi]}, "not a stored"))
    d = ok(server.draft_note(cid, [cl for cl, _ in cases]))
    assert d["status"] == "rejected"
    by_index = {}
    for r in d["rejections"]:
        by_index.setdefault(r["claim_index"], []).append(r["reason"])
    for i, (_, why) in enumerate(cases):
        assert any(why in r for r in by_index.get(i, [])), (i, why, by_index.get(i))
    assert name not in json.dumps(d) and last not in json.dumps(d["rejections"])  # reasons never echo the name
    stored = q(NR, "MATCH (:Case {id: $c})-[:HAS_NOTE]->(n:Note {id: $n}) RETURN n.status, n.rejections_json",
               {"c": cid, "n": d["note_id"]})[0]
    assert stored[0] == "rejected" and len(json.loads(stored[1])) == len(d["rejections"])
    # shape errors are INVALID_ARG; an unknown case NOT_FOUND
    for bad in ([], [{"expert_id": HILL, "text": "x"}], [{"expert_id": HILL, "text": "", "evidence_refs": []}],
                [{"expert_id": "A5000000001", "text": "x", "evidence_refs": []}],
                [{"expert_id": HILL, "text": "x", "evidence_refs": [], "scope": {"hops": 3}}]):
        assert code(server.draft_note(cid, bad)) == "INVALID_ARG", bad
    assert code(server.draft_note("case_" + "0" * 32, [cases[0][0]])) == "NOT_FOUND"


def test_public_case_note_may_use_the_name():
    fresh()
    c = ok(server.open_case("Public GEBN", "Coca-Cola", "obesity", [HILL, BLAIR], True))
    hill = ok(server.tie_report(HILL, case_id=c["case_id"]))
    claim = {"expert_id": HILL, "text": f"{hill['label']}: ties found in the research record.",
             "evidence_refs": [hill["evidence"][0]["doi"]]}
    assert ok(server.draft_note(c["case_id"], [claim]))["status"] == "verified"


def test_approve_note_rules():
    fresh()
    c = ok(server.open_case("Approve", "Coca-Cola", "obesity", [HILL]))
    hill = ok(server.tie_report(HILL))
    claim = {"expert_id": HILL, "text": "Expert #1: ties found in the research record.",
             "evidence_refs": [hill["evidence"][0]["doi"]]}
    n1 = ok(server.draft_note(c["case_id"], [claim]))["note_id"]
    n2 = ok(server.draft_note(c["case_id"], [claim]))["note_id"]
    a = ok(server.approve_note(n1, "Aaryan"))
    again = server.approve_note(n1, "Someone else")
    assert ok(again)["markdown"] == a["markdown"] and again["data"]["approved_by"] == "Aaryan"
    assert any("already approved" in w for w in again["warnings"])
    ok(server.record_verdict("PAPER_EXCLUDED", DOI, "r", "Aaryan"))  # recorded after n2 was drafted
    assert code(server.approve_note(n2, "Aaryan")) == "NOT_APPROVED"
    assert code(server.approve_note("note_" + "0" * 32, "Aaryan")) == "NOT_FOUND"
    assert code(server.approve_note("chk_" + "0" * 32, "Aaryan")) == "INVALID_ARG"
    assert code(server.approve_note(n1, " ")) == "INVALID_ARG"


def test_human_only_tools_are_marked_and_never_allowlisted():
    for fn in (server.record_verdict, server.approve_note):
        assert fn.__doc__.lstrip().startswith("HUMAN ONLY"), fn.__name__
    assert server.HUMAN_VERDICT.destructive_hint is True and server.HUMAN_APPROVE.read_only_hint is False
    for f in list((ROOT / ".claude").glob("settings*.json")) + [Path.home() / ".claude" / "settings.json"]:
        if f.exists():  # "ask" (a permission prompt every time) is fine; "allow" would skip the human gate
            allow = json.dumps((json.loads(f.read_text(encoding="utf-8")).get("permissions") or {}).get("allow", []))
            assert not re.search(r"record_verdict|approve_note|set_policy|tietrace-[a-z]+__\*|\"mcp__tietrace-[a-z]+\"",
                                 allow), f
    writer = (ROOT / ".claude" / "agents" / "writer.md").read_text(encoding="utf-8")
    head = writer.split("---")[1]
    tools_line = next(line for line in head.splitlines() if line.startswith("tools:"))
    listed = {t.strip() for t in tools_line.split(":", 1)[1].split(",")}
    assert listed == {f"mcp__tietrace-alpha__{t}" for t in ("recall", "tie_report", "check_no_tie", "draft_note")}
    assert "name: writer" in head and "description:" in head


# ---------- memory summaries say what a tier rests on (E11 follow-up) and label the score ----------

def test_prior_memory_and_recall_say_what_a_tier_rests_on():
    fresh()
    a = ok(server.open_case("First", "Coca-Cola", "obesity", [HILL, NONE_EXPERT]))
    ok(server.check_case(case_id=a["case_id"]))
    vid = ok(server.record_verdict("PROFILE_MERGE", [NONE_EXPERT, S_N_BLAIR], "test", "Aaryan"))["verdict_id"]
    prior = ok(server.open_case("Second", "Coca-Cola", "obesity", [HILL, NONE_EXPERT]))["prior_memory"]
    hill = next(p for p in prior if p.get("expert_id") == HILL)
    assert "Coca-Cola Foundation funding (FUNDED_BY)" in hill["tier_basis"] and "bottler" not in hill["summary"]
    assert "context only" in hill["score_basis"]
    assert any(p["kind"] == "verdict" and p["verdict_id"] == vid for p in prior), prior
    items = ok(server.recall(expert_id=HILL))["timeline"]
    assert all("(context only" in t["summary"] and "FUNDED_BY" in t["summary"] for t in items if t["kind"] == "check")
    assert items[-1]["expert_id"] == HILL and items[-1]["label"]
    assert any(t["kind"] == "verdict" for t in ok(server.recall(expert_id=NONE_EXPERT))["timeline"])


def test_no_tie_wording_fixes():
    junk_only = q(RG, "MATCH (p:Person)-[:AUTHORED]->(w:Work) WITH p, sum(CASE WHEN w.junk THEN 0 ELSE 1 END) AS good "
                      "WHERE good = 0 RETURN p.oa_id LIMIT 1")[0][0]
    with research.on_graph(RG):
        span = server._span()
        w = research.check_no_tie(junk_only, 3, span)["data"]["wording"]
        assert "no loaded paper (junk records such as corrections are left out)" in w, w
        assert "Funding records are thin" in research.check_no_tie(NONE_EXPERT, 3, span)["data"]["wording"]  # 56%
        old, research.THIN_FUNDER = research.THIN_FUNDER, 0.5
        try:
            w = research.check_no_tie(NONE_EXPERT, 3, span)["data"]["wording"]
        finally:
            research.THIN_FUNDER = old
        assert "Funding records are thin" not in w and "56% of topic works" in w, w


def test_obsidian_never_prints_none_share_and_labels_the_score():
    fresh()
    c = ok(server.open_case("Obsidian", "Coca-Cola", "obesity", [NONE_EXPERT, HILL]))
    ok(server.check_case(case_id=c["case_id"]))
    # an old-style NONE check (saved before Oct 16): no expert share in its snapshot
    snap = json.loads(q(NR, "MATCH (k:Check {expert_id: $e}) RETURN k.snapshot_json", {"e": NONE_EXPERT})[0][0])
    snap["no_tie"]["expert_funder_coverage"] = None
    q(NR, "MATCH (k:Check {expert_id: $e}) SET k.snapshot_json = $s", {"e": NONE_EXPERT, "s": json.dumps(snap)})
    text = "\n".join(obsidian_export.build([]).values())
    assert "None" not in text and "(context only" in text


def test_reads_are_ro_and_writes_fixed_in_new_code():
    src = (ROOT / "memory.py").read_text(encoding="utf-8")
    for name in ("SAVE_VERDICT_Q", "AFFECTS_Q", "SAVE_NOTE_Q", "APPROVE_NOTE_Q"):
        assert re.search(rf"^{name} = \(?\s*\"", src, re.M), name  # a fixed string, never built at call time
    assert src.count(".query(") == 1 and src.count(".ro_query(") == 1
    srv = (ROOT / "server.py").read_text(encoding="utf-8")
    assert "verifier.verify_claims(" in srv  # the Verifier really runs in draft_note (external review finding)


# ---------- Reviewer's tests (checkpoint 7 review, Oct 16; written by the Reviewer agent for Aaryan) ----------

def _hill_case(story="Reviewer probe", experts=None):
    fresh()
    c = ok(server.open_case(story, "Coca-Cola", "obesity", experts or [HILL, NONE_EXPERT]))
    hill = ok(server.tie_report(HILL))
    return c["case_id"], [e["doi"] for e in hill["evidence"] if e["doi"]]


def test_reviewer_tie_claim_needs_that_experts_tier_a_doi():
    """Gap: a valid DOI of ANOTHER expert, or a Tier B co-author DOI, never backs a tie claim."""
    cid, hd = _hill_case(experts=[HILL, BLAIR])
    blair = ok(server.tie_report(BLAIR))
    other = next(e["doi"] for e in blair["evidence"] if e["doi"] and e["doi"] not in hd)
    d = ok(server.draft_note(cid, [{"expert_id": HILL, "text": "Expert #1: ties found in the research record.",
                                    "evidence_refs": [other]}]))
    assert d["status"] == "rejected" and "not a stored DOI" in d["rejections"][0]["reason"]


def test_reviewer_excluded_doi_cannot_back_a_claim_and_draft_lists_the_verdict():
    fresh()
    c = ok(server.open_case("Excluded", "Coca-Cola", "obesity", [X1]))
    vid = ok(server.record_verdict("PAPER_EXCLUDED", DOI, "r", "Reviewer"))["verdict_id"]
    out = server.draft_note(c["case_id"], [{"expert_id": X1, "text": "Expert #1: ties found in the research record.",
                                            "evidence_refs": [DOI]}])
    assert ok(out)["status"] == "rejected" and out["verdicts_applied"] == [vid]
    note = q(NR, "MATCH (n:Note {id: $n}) RETURN n.verdicts", {"n": out["data"]["note_id"]})[0][0]
    assert note == [vid]


def test_reviewer_rejected_note_cannot_be_approved_even_by_the_memory_write():
    """The status guard lives in the write itself (APPROVE_NOTE_Q), not only in approve_note's Python check."""
    cid, hd = _hill_case()
    nid = ok(server.draft_note(cid, [{"expert_id": HILL, "text": "Expert #1 is unbiased.", "evidence_refs": hd[:1]}]))["note_id"]
    assert memory.approve(nid, "Reviewer", []) is None
    assert q(NR, "MATCH (n:Note {id: $n}) RETURN n.status", {"n": nid})[0][0] == "rejected"


def test_reviewer_writer_cannot_bring_its_own_scope_or_kind():
    cid, hd = _hill_case()
    for extra in ({"kind": "no_tie"}, {"query_name": "q3_bfs_v1"}, {"result": "NO_TIE_FOUND"}, {"label": "Expert #9"}):
        claim = {"expert_id": HILL, "text": "No tie found.", "evidence_refs": ["q3_bfs_v1"], **extra}
        assert code(server.draft_note(cid, [claim])) == "INVALID_ARG", extra


def test_reviewer_no_tie_claim_text_cannot_assert_a_tie_and_tie_claim_cannot_deny_one():
    """MUST-FIX 2: the claim kind comes from evidence_refs, so the TEXT can claim the opposite. A no-tie claim that
    says 'received Coca-Cola funding' has no DOI behind it (CLAUDE.md: a positive claim needs a stored DOI)."""
    cid, hd = _hill_case()
    d = ok(server.draft_note(cid, [
        {"expert_id": NONE_EXPERT, "text": "Expert #2 received Coca-Cola funding.", "evidence_refs": ["q3_bfs_v1"]},
        {"expert_id": HILL, "text": "No tie found in the research record for Expert #1.", "evidence_refs": hd[:1]}]))
    assert d["status"] == "rejected" and {r["claim_index"] for r in d["rejections"]} == {0, 1}, d


def test_reviewer_banned_words_cannot_hide_behind_unicode_or_hyphens():
    """SHOULD-FIX 3 (kept here as a regression guard once fixed): accents, full-width letters, zero-width
    spaces and a hyphen in 'clean-source' slip past the plain regex."""
    for text in ("Expert #1 is ｉｎｄｅｐｅｎｄｅｎｔ.", "Expert #1 is inde​pendent.", "Expert #1 is a clean-source.",
                 "Expert #1 is Indépendent."):
        assert verifier.verify_claim({"text": text, "kind": "tie", "evidence_refs": ["10.1/a"]}, ["10.1/a"]), text


def test_reviewer_approved_markdown_never_echoes_names_or_banned_words_from_the_story():
    """MUST-FIX 1: the note title is the case's story verbatim, so a story that names an expert or says
    'independent' puts both into the approved note of a case that is not public."""
    name = research.names_of([], [HILL])[HILL]
    cid, hd = _hill_case(story=f"Is {name} independent of soda money?")
    nid = ok(server.draft_note(cid, [{"expert_id": HILL, "text": "Expert #1: ties found in the research record.",
                                      "evidence_refs": hd[:1]}]))["note_id"]
    out = server.approve_note(nid, "Reviewer")
    if out["ok"]:  # either refuse, or print a note without them
        md = out["data"]["markdown"]
        assert name not in md and name.split()[-1] not in md and not BANNED.search(md), md[:300]


def test_reviewer_note_sources_say_kind_and_topic_label_per_doi():
    """MUST-FIX 4: CLAUDE.md: every piece of Tier A evidence is labelled on-topic or off-topic. The approved note
    lists bare DOIs, so 'funded by Coca-Cola' on a foundation-only DOI reads as supported."""
    cid, hd = _hill_case(experts=[HILL])
    hill = ok(server.tie_report(HILL))
    ev = next(e for e in hill["evidence"] if e["doi"])
    nid = ok(server.draft_note(cid, [{"expert_id": HILL, "text": "Expert #1: ties found in the research record.",
                                      "evidence_refs": [ev["doi"]]}]))["note_id"]
    md = ok(server.approve_note(nid, "Reviewer"))["markdown"]
    line = next(line for line in md.splitlines() if ev["doi"] in line)
    assert ev["topic_label"] in line and ev["via"] in line, line


def test_reviewer_every_case_expert_appears_in_the_note():
    """MUST-FIX 5: writer.md says for Tier B/C 'The note says so', but a B/C expert can never have a verified
    claim, so it never gets a section; any expert without a claim is silently left out of the approved note."""
    cid, hd = _hill_case(experts=[HILL, NONE_EXPERT])
    nid = ok(server.draft_note(cid, [{"expert_id": HILL, "text": "Expert #1: ties found in the research record.",
                                      "evidence_refs": hd[:1]}]))["note_id"]
    md = ok(server.approve_note(nid, "Reviewer"))["markdown"]
    assert "Expert #2" in md, md


def test_reviewer_tier_c_resting_on_an_excluded_paper_is_flagged():
    """MUST-FIX 3: algo.BFS still walks a COAUTH_B link that rests on an excluded paper (spec 9f). bridges then
    shows no path, yet tie_report still says Tier C with no warning. The answer must say so."""
    C = "A5005721369"  # Tier C, 2 hops, every bridge path's first link has 2 papers sharing one DOI
    with research.on_graph(RG):
        t = research.tie_report(C)["data"]
        assert t["tier"] == "C", "fixture"
        b = research.bridges(C, t["nearest"]["hops"], 50)["data"]
        dois = sorted({p["edges"][0]["dois"][0] for p in b["paths"] if len(p["edges"][0]["dois"]) == 2})
        assert dois, "fixture"
        with research.with_verdicts({"ids": ["vrd_test"], "dois": dois, "merges": []}):
            assert research.bridges(C, t["nearest"]["hops"], 50)["data"]["paths"] == []
            after = research.tie_report(C)
    if after["data"]["tier"] == "C":
        assert any("excluded" in w for w in after["warnings"]), after["warnings"]


def test_reviewer_record_verdict_reads_heads_under_the_lock():
    """SHOULD-FIX: heads are read before the server lock; a recheck that wins the lock in between leaves the
    current Check computed without the verdict and the AFFECTS edge with new_check_id null."""
    fresh()
    c = ok(server.open_case("Race", "Coca-Cola", "obesity", [X1]))
    chk = ok(server.tie_report(X1, case_id=c["case_id"]))["check_id"]
    orig = memory.current_checks

    def racing(log):
        heads = orig(log)
        server.recheck(chk)  # another call takes the lock first
        return heads
    memory.current_checks = racing
    try:
        d = ok(server.record_verdict("PAPER_EXCLUDED", DOI, "race", "Reviewer"))
    finally:
        memory.current_checks = orig
    assert all(x["new_check_id"] for x in d["impact"]["details"]), d["impact"]["details"]
    assert all(h["verdicts"] for h in memory.current_checks([])), "the current Check ignores the verdict"


def test_reviewer_new_tools_log_one_event_each():
    cid, hd = _hill_case(experts=[HILL])
    nid = ok(server.draft_note(cid, [{"expert_id": HILL, "text": "Expert #1: ties found in the research record.",
                                      "evidence_refs": hd[:1]}], agent="writer"))["note_id"]
    ok(server.approve_note(nid, "Reviewer"))
    ok(server.record_verdict("PAPER_EXCLUDED", DOI, "r", "Reviewer"))
    rows = q(NR, "MATCH (e:Event) WHERE e.tool IN ['draft_note', 'approve_note', 'record_verdict'] "
                 "RETURN e.tool, e.ok, e.case_id, e.agent")
    tools = sorted(r[0] for r in rows)
    assert tools == ["approve_note", "draft_note", "record_verdict"], rows
    assert next(r for r in rows if r[0] == "draft_note")[1:] == [True, cid, "writer"]


# ---------- Builder, review round 1 should-fixes (Aaryan, Oct 16) ----------

def test_r1_name_check_catches_short_spaced_and_look_alike_names():
    names = ["Wei Li", "James O. Hill"]
    for text in ("Wei Li said so.", "W. Li said so.", "James Hill said so.", "J Hill said so.", "H I L L said so.",
                 "Dr H\u0456ll said so.", "Dr Hi\u200bll said so."):  # Cyrillic i, zero-width space
        assert verifier.text_problems(text, names), text
    for text in ("Li-ion batteries", "a hilltop", "Expert #1 said so."):  # no false positive on ordinary words
        assert not verifier.text_problems(text, names), text
    r = verifier.verify_claim({"text": "Expert 2 and Expert no. 3 said so.", "label": "Expert #1", "kind": "tie",
                               "evidence_refs": ["10.1/a"]}, ["10.1/a"])
    assert any("Expert #2, Expert #3" in x for x in r), r


def test_r1_a_failed_check_no_tie_is_named_in_the_rejection():
    cid, _ = _hill_case()
    real = research.check_no_tie
    research.check_no_tie = lambda *a, **k: {"ok": False, "error": {"code": "TIMEOUT", "message": "slow"}, "cypher": []}
    try:
        d = ok(server.draft_note(cid, [{"expert_id": NONE_EXPERT, "text": "No tie found in the research record "
                                        "for Expert #2.", "evidence_refs": ["q3_bfs_v1"]}]))
    finally:
        research.check_no_tie = real
    assert d["status"] == "rejected" and "check_no_tie failed (TIMEOUT)" in d["rejections"][0]["reason"], d
    assert "found a tie" not in d["rejections"][0]["reason"]


def test_r1_approve_refuses_a_note_drafted_on_an_older_build():
    cid, hd = _hill_case(experts=[HILL])
    nid = ok(server.draft_note(cid, [{"expert_id": HILL, "text": "Expert #1: ties found in the research record.",
                                      "evidence_refs": hd[:1]}]))["note_id"]
    q(NR, "MATCH (n:Note {id: $n}) SET n.built_at = 'an older build'", {"n": nid})
    out = server.approve_note(nid, "Aaryan")
    assert code(out) == "NOT_APPROVED" and "rebuilt" in out["error"]["message"]
    assert q(NR, "MATCH (n:Note {id: $n}) RETURN n.status", {"n": nid})[0][0] == "verified"


def test_r1_duplicate_verdict_is_refused_by_the_write_itself():
    fresh()
    args = (memory.new_id("vrd"), "PAPER_EXCLUDED", DOI, DOI, [], "r", "Aaryan", [], 0, "obesity-coke", None)
    assert memory.save_verdict([], *args)
    assert not memory.save_verdict([], memory.new_id("vrd"), *args[1:])  # another process's second write
    assert q(NR, "MATCH (v:Verdict) RETURN count(v)")[0][0] == 1


# ---------- Reviewer's tests, round 2 (checkpoint 7 review, Oct 16; Reviewer agent, for Aaryan) ----------

def test_reviewer2_no_tie_text_rules_each_hold_alone():
    """Each half of the no-tie text rule is needed: 'no tie found' must be said, and tie words are refused even
    when it is said. Tie claims must not deny a tie."""
    base = {"kind": "no_tie", "query_name": "q3_bfs_v1", "evidence_refs": ["q3_bfs_v1"], "result": "NO_TIE_FOUND",
            "scope": {"hops": 3, "years": "2021-2026", "coverage": 0.9, "funder_coverage": 0.5}}
    assert not verifier.verify_claim({**base, "text": "No tie found in the research record for Expert #2."}, [])
    assert verifier.verify_claim({**base, "text": "Expert #2 is fine."}, [])  # does not say 'no tie found'
    assert verifier.verify_claim({**base, "text": "No tie found, but Expert #2 was paid by the company."}, [])
    tie = {"kind": "tie", "result": None, "evidence_refs": ["10.1/a"]}
    assert verifier.verify_claim({**tie, "text": "Expert #1 has no ties."}, ["10.1/a"])
    assert not verifier.verify_claim({**tie, "text": "Expert #1: ties found in the research record."}, ["10.1/a"])


def test_reviewer2_no_tie_claim_cannot_assert_funding_in_other_words():
    """MUST-FIX (round 2): the no-tie text check is a short word list, so the most natural phrasings of a
    positive claim still pass as a no-tie claim with no DOI behind them."""
    base = {"kind": "no_tie", "query_name": "q3_bfs_v1", "evidence_refs": ["q3_bfs_v1"], "result": "NO_TIE_FOUND",
            "scope": {"hops": 3, "years": "2021-2026", "coverage": 0.9, "funder_coverage": 0.5}}
    for text in ("No tie found in the research record, but Coca-Cola funding went to Expert #2.",
                 "No tie found for Expert #2, though Coca-Cola gave Expert #2 a grant.",
                 "No tie found? Wrong: Expert #2 works for Coca-Cola.",
                 "No tie found for Expert #2 except money from Coca-Cola.",
                 "No tie found for Expert #2; Expert #2 is a Coca-Cola advisor on its board.",
                 "Expert #2 is not 'no tie found': Coca-Cola financed Expert #2."):
        assert verifier.verify_claim({**base, "text": text}, []), text


def test_reviewer2_check_no_tie_warns_when_bfs_tie_may_rest_on_an_excluded_paper():
    C = "A5005721369"  # Tier C at 2 hops; see test_reviewer_tier_c_resting_on_an_excluded_paper_is_flagged
    with research.on_graph(RG):
        span = server._span()
        hops = research.tie_report(C)["data"]["nearest"]["hops"]
        b = research.bridges(C, hops, 50)["data"]
        dois = sorted({p["edges"][0]["dois"][0] for p in b["paths"] if len(p["edges"][0]["dois"]) == 2})
        plain = research.check_no_tie(C, 3, span)
        with research.with_verdicts({"ids": ["vrd_test"], "dois": dois, "merges": []}):
            nt = research.check_no_tie(C, 3, span)
    assert research.BFS_UNFILTERED not in plain["warnings"]
    assert nt["data"]["result"] == "TIE_FOUND" and research.BFS_UNFILTERED in nt["warnings"], nt["warnings"]


def test_reviewer2_no_settings_file_can_skip_the_human_gate():
    """Strengthens test_human_only_tools_...: any allow entry that would match a human-only tool fails, whatever
    its form (exact, server-wide, wildcard), in the repo's settings, the user's settings and ~/.claude.json."""
    import fnmatch
    human = [f"mcp__tietrace-{n}__{t}" for n in ("alpha", "beta") for t in ("record_verdict", "approve_note", "set_policy")]

    def skips_gate(rule):
        rule = rule.split("(")[0].strip()
        return any(rule == h or fnmatch.fnmatchcase(h, rule) or h.startswith(rule + "__") for h in human)
    assert skips_gate("mcp__tietrace-alpha") and skips_gate("mcp__tietrace-alpha__*") and skips_gate("mcp__*")
    assert not skips_gate("mcp__tietrace-alpha__tie_report")
    files = list((ROOT / ".claude").glob("settings*.json")) + [Path.home() / ".claude" / "settings.json",
                                                              Path.home() / ".claude" / "settings.local.json"]
    for f in files:
        if f.exists():
            perms = json.loads(f.read_text(encoding="utf-8")).get("permissions") or {}
            assert not [r for r in perms.get("allow", []) if skips_gate(r)], f
            assert perms.get("defaultMode") != "bypassPermissions", f
    cfg = Path.home() / ".claude.json"
    if cfg.exists():
        proj = (json.loads(cfg.read_text(encoding="utf-8")).get("projects") or {}).get(str(ROOT)) or {}
        assert not [r for r in proj.get("allowedTools", []) if skips_gate(r)], proj.get("allowedTools")
    ask = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))["permissions"]["ask"]
    assert {"mcp__tietrace-alpha__record_verdict", "mcp__tietrace-alpha__approve_note"} <= set(ask)


# ---------- Builder, review round 2 (Aaryan, Oct 16) ----------

def test_r2_the_note_prints_server_sentences_never_the_writers_text():
    cid, hd = _hill_case()
    denial = "Expert #1 is not connected to Coca-Cola in any way, zero links."
    d = ok(server.draft_note(cid, [{"expert_id": HILL, "text": denial, "evidence_refs": hd[:2]},
                                   {"expert_id": NONE_EXPERT, "text": "no tie found in the research record for "
                                    "Expert #2", "evidence_refs": ["q3_bfs_v1"]}]))
    assert d["status"] == "verified", d
    md = ok(server.approve_note(d["note_id"], "Aaryan"))["markdown"]
    assert denial not in md and "zero links" not in md
    assert "- Ties found in the research record: OpenAlex records Expert #1 as author of 2 cited work(s): " \
           "funded by Coca-Cola Foundation." in md, md
    assert "- No tie found in the research record for Expert #2 within the stated scope; absence is not proof." in md
    stored = json.loads(q(NR, "MATCH (n:Note {id: $n}) RETURN n.claims_json", {"n": d["note_id"]})[0][0])
    assert [c["writer_text_replaced"] for c in stored] == [True, True]


def test_r2_a_note_from_before_round_1_cannot_be_approved():
    cid, hd = _hill_case(experts=[HILL])
    nid = ok(server.draft_note(cid, [{"expert_id": HILL, "text": "Expert #1: ties found in the research record.",
                                      "evidence_refs": hd[:1]}]))["note_id"]
    q(NR, "MATCH (n:Note {id: $n}) REMOVE n.header_json, n.experts_json", {"n": nid})
    out = server.approve_note(nid, "Aaryan")
    assert code(out) == "NOT_APPROVED" and "draft_note again" in out["error"]["message"]
    assert verifier.norm_text("\u03bd") == "v"  # Greek nu looks like v


# ---------- Reviewer's tests, round 3 (checkpoint 7 review, Oct 16; Reviewer agent, for Aaryan) ----------

def test_reviewer3_tie_sentence_matches_each_dois_kind_and_counts_distinct_works():
    """An affiliation DOI is never printed as 'funded by'; citing one DOI three ways (bare, upper case, URL) is
    one work, not three (MUST-FIX round 3: the count and the Sources list are inflated by repeated refs)."""
    fresh()
    c = ok(server.open_case("Kinds", "Coca-Cola", "obesity", [BLAIR]))
    ev = ok(server.tie_report(BLAIR))["evidence"]
    aff = next(e for e in ev if e["kind"] == "AFFILIATED" and e["doi"]
               and not any(x["doi"] == e["doi"] and x["kind"] == "FUNDED_BY" for x in ev))
    d = ok(server.draft_note(c["case_id"], [{"expert_id": BLAIR, "text": "Expert #1: ties found in the research record.",
                                             "evidence_refs": [aff["doi"]]}]))
    md = ok(server.approve_note(d["note_id"], "Reviewer"))["markdown"]
    line = next(x for x in md.splitlines() if x.startswith("- Ties found"))
    assert "listing an affiliation with " + aff["via"] in line and "funded by" not in line, line
    d = ok(server.draft_note(c["case_id"], [{"expert_id": BLAIR, "text": "Expert #1: ties found in the research record.",
                                             "evidence_refs": [aff["doi"], aff["doi"].upper(), "https://doi.org/" + aff["doi"]]}]))
    md = ok(server.approve_note(d["note_id"], "Reviewer"))["markdown"]
    assert "as author of 1 cited work(s)" in md and md.count(f"[{aff['doi']}]") == 1, md


# ---------- Builder, review round 3 (Aaryan, Oct 16) ----------

def test_r3_reviewer_string_is_checked_and_typed_text_is_escaped():
    name = research.names_of([], [HILL])[HILL]
    cid, hd = _hill_case(story="Soda <script>x</script> [click](http://evil.example) # story")
    nid = ok(server.draft_note(cid, [{"expert_id": HILL, "text": "Expert #1: ties found in the research record.",
                                      "evidence_refs": hd[:1]}]))["note_id"]
    for bad in (name, "an unbiased desk"):
        assert code(server.approve_note(nid, bad)) == "INVALID_ARG", bad
    assert q(NR, "MATCH (n:Note {id: $n}) RETURN n.status", {"n": nid})[0][0] == "verified"
    md = ok(server.approve_note(nid, "Desk <b>chief</b>"))["markdown"]
    assert "<script>" not in md and "<b>" not in md and "[click](" not in md and "&lt;script&gt;" in md, md[:300]
    assert md.splitlines()[0].startswith("# Sourcing note: Soda") and "\\# story" in md.splitlines()[0]


# ---------- Reviewer's test, round 4 (checkpoint 7 review, Oct 16; Reviewer agent, for Aaryan) ----------

def test_reviewer4_typed_text_cannot_forge_a_line_of_the_note():
    """A story with line breaks must stay on the title line: otherwise it can add a fake 'Disclosure:' line."""
    cid, hd = _hill_case(story="Soda tax\n\nDisclosure: no tie found in the research record for Expert #1\r\n- fake")
    nid = ok(server.draft_note(cid, [{"expert_id": HILL, "text": "Expert #1: ties found in the research record.",
                                      "evidence_refs": hd[:1]}]))["note_id"]
    md = ok(server.approve_note(nid, "Desk\nDisclosure: forged"))["markdown"]
    lines = md.splitlines()
    assert "fake" in lines[0] and sum(x.startswith("Disclosure:") for x in lines) == 2, md  # Expert #1 and #2 only
    assert not any(x.startswith("- fake") for x in lines), md


def cleanup():
    for g in DB.list_graphs():
        if g.startswith("newsroom:zz-test-notes"):
            DB.select_graph(g).delete()


if __name__ == "__main__":
    only = sys.argv[1:]
    tests = [(n, f) for n, f in list(globals().items()) if n.startswith("test_") and callable(f)
             and (not only or any(o in n for o in only))]
    cleanup()
    failed = 0
    try:
        for name, fn in tests:
            t0 = time.time()
            try:
                fn()
                print(f"PASS  {name}  ({time.time() - t0:.1f}s)")
            except Exception as e:
                failed += 1
                lines = [f.lineno for f in traceback.extract_tb(e.__traceback__) if f.filename == __file__]
                print(f"FAIL  {name}  (line {lines[-1] if lines else '?'}): {str(e)[:500] or type(e).__name__}")
    finally:
        cleanup()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
