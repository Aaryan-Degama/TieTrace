"""E8 golden tests (checkpoint 8): about 15 fixed checks. Written by Abdul's Builder, Oct 16.

Run:  FALKORDB_PORT=6381 uv run python tests/test_golden.py [name-substring ...]
Every expected value below was READ from the live research:obesity-coke graph (data_version openalex@2026-10-15)
and then pinned. If the graph is rebuilt and a number moves, the test fails on purpose: look at why, never edit
the number to make it pass. Scratch newsrooms newsroom:zz-test-golden* are deleted at the end.
"""
import os
import re
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("FALKORDB_PORT", "6379")
os.environ["TIETRACE_NEWSROOM"] = "zz-test-golden"  # never the real alpha newsroom
import memory  # noqa: E402
import research  # noqa: E402
import server  # noqa: E402
import verifier  # noqa: E402

DB = research._conn()
HILL, BLAIR, HAND, PETERS = "A5031318756", "A5105003630", "A5046953968", "A5045252457"  # the four public GEBN leaders
S_N_BLAIR = "A5087575650"   # split profile of Blair, no evidence of its own
NONE_EXPERT = "A5067322664"  # no Tier A/B/C tie
X1 = "A5071304044"           # only Tier A evidence is the 13-author paper below
BIG_DOI = "10.1016/j.foodhyd.2022.107657"
BANNED = re.compile(r"\b(independent|conflicted)\b", re.I)
# id: (tier, score, a DOI that must be in its evidence, number of evidence items)
GEBN = {HILL: ("A2", 50.1111, "10.1017/s0007114520001798", 7), BLAIR: ("A1", 105.05, "10.1016/j.pmedr.2018.07.013", 71),
        HAND: ("A1", 101.0, "10.1016/j.pmedr.2018.07.013", 26), PETERS: ("A2", 50, "10.1002/oby.20737", 1)}


def ok(out):
    assert out["ok"] is True, out.get("error")
    assert set(out) >= {"ok", "data", "cypher", "scope", "verdicts_applied", "warnings"}
    return out["data"]


def code(out):
    assert out["ok"] is False, out
    return out["error"]["code"]


def fresh():
    """An empty scratch newsroom (verdicts are newsroom-wide, so each test starts clean)."""
    g = "newsroom:zz-test-golden"
    if g in DB.list_graphs():
        DB.select_graph(g).delete()
    memory.use_newsroom("zz-test-golden")
    memory.init()


# 1-4. The four publicly named GEBN leaders are Tier A, each with its DOI (the redesign says "five"; only four are public).
def test_golden_gebn_leaders_are_tier_a_with_dois():
    for eid, (tier, score, doi, n_ev) in GEBN.items():
        d = ok(research.tie_report(eid, "Expert #1"))
        assert (d["tier"], d["score"], d["tier_basis"]) == (tier, score, "direct evidence"), (eid, d["tier"], d["score"])
        dois = [e["doi"] for e in d["evidence"]]
        assert len(dois) == n_ev and doi in dois, eid  # every evidence item has a DOI the note can cite


# 5. Pinned score formula: Blair = tier weight 100 + co-author weight 5.05 (redesign section 4, never tuned).
def test_golden_score_formula_value_is_fixed():
    d = ok(research.tie_report(BLAIR, "Expert #1"))
    assert d["score_parts"]["tier_weight"] == 100 and d["score_parts"]["coauth_w"] == 5.05
    assert d["score"] == d["score_parts"]["tier_weight"] + d["score_parts"]["coauth_w"] == 105.05


# 6. Blair's split profile merges after a PROFILE_MERGE verdict: NONE -> A1.
def test_golden_profile_merge_turns_none_into_a1():
    fresh()
    assert ok(server.tie_report(S_N_BLAIR))["tier"] == "NONE"
    cid = ok(server.open_case("Golden merge", "Coca-Cola", "obesity", [S_N_BLAIR]))["case_id"]
    ok(server.check_case(case_id=cid))
    d = ok(server.record_verdict("PROFILE_MERGE", [BLAIR, S_N_BLAIR], "same person (split profile)", "Abdul"))
    assert [(x["tier_before"], x["tier_after"]) for x in d["impact"]["details"]] == [("NONE", "A1")]
    assert ok(server.tie_report(S_N_BLAIR))["tier"] == "A1"


# 7. A PAPER_EXCLUDED verdict changes a known score: X1 is A1 / 101.6 on one 13-author paper; without it, not A1.
def test_golden_paper_excluded_changes_a_known_score():
    fresh()
    cid = ok(server.open_case("Golden exclude", "Coca-Cola", "obesity", [X1, HILL]))["case_id"]
    res = {r["expert_id"]: (r["tier"], r["score"]) for r in ok(server.check_case(case_id=cid))["results"]}
    assert res[X1] == ("A1", 101.6) and res[HILL] == ("A2", 50.1111)
    d = ok(server.record_verdict("PAPER_EXCLUDED", BIG_DOI, "golden test", "Abdul"))
    det = {x["expert_id"]: x for x in d["impact"]["details"]}
    assert X1 in det and HILL not in det, det  # X1 moves; Hill (other papers) does not
    assert det[X1]["tier_before"] == "A1" and det[X1]["tier_after"] != "A1"
    assert det[X1]["score_before"] == 101.6 and det[X1]["score_after"] < det[X1]["score_before"] - 99


# 8. The Verifier rejects a positive claim with no stored DOI (none given, or one the expert does not have).
def test_golden_verifier_rejects_positive_claim_without_stored_doi():
    stored = ["10.1/real"]
    base = {"expert_id": "A1", "text": "Expert #1 has ties found in the research record.", "kind": "tie"}
    assert verifier.verify_claim({**base, "evidence_refs": []}, stored)
    assert verifier.verify_claim({**base, "evidence_refs": ["10.9/made.up"]}, stored)
    assert verifier.verify_claim({**base, "evidence_refs": ["https://doi.org/10.1/REAL"]}, stored) == []


# 9. The Verifier rejects a no-tie claim without its scope (or with an unknown query name).
def test_golden_verifier_rejects_no_tie_without_scope():
    claim = {"expert_id": "A1", "text": "No tie found in the research record.", "kind": "no_tie", "query_name": "q3_bfs_v1"}
    assert any("scope" in r for r in verifier.verify_claim(claim, []))
    full = {"hops": 3, "years": "2021-2026", "coverage": 0.966, "funder_coverage": 0.563}
    assert verifier.verify_claim({**claim, "scope": full}, []) == []
    assert verifier.verify_claim({**claim, "scope": {**full, "hops": None}}, [])
    assert verifier.verify_claim({**claim, "scope": full, "query_name": "q_unknown"}, [])


# 10. The Verifier rejects the banned words, also hidden behind a look-alike letter (a Cyrillic i).
def test_golden_verifier_rejects_banned_words():
    for text in ("Expert #1 is independent.", "Expert #1 is not conflicted.", "Expert #1 is іndependent."):
        assert verifier.text_problems(text), text
    assert verifier.text_problems("Ties found in the research record for Expert #1.") == []


# 11. GRAPH.RO_QUERY refuses a write with READ_ONLY (the research graph cannot be changed through any read path).
def test_golden_ro_query_rejects_a_write():
    try:
        research.ro_query(research.GRAPH, "CREATE (:Golden {x: 1})")
    except research.ToolError as e:
        assert e.code == "READ_ONLY", e.code
    else:
        raise AssertionError("a write was accepted")
    assert research.ro_query(research.GRAPH, "MATCH (n:Golden) RETURN count(n)")[0][0] == 0


# 12. Name resolution: a unique full name is MATCHED, an unknown one NOT_FOUND, a common one AMBIGUOUS (never guessed).
def test_golden_name_resolution_matched_not_found_ambiguous():
    d = ok(research.resolve_experts(["James O. Hill", "Zzzxq Nobody", "Jing Wang"], "obesity"))
    by = {m["name_in_input"]: m for m in d["matches"]}
    assert (by["James O. Hill"]["status"], by["James O. Hill"]["expert_id"]) == ("MATCHED", HILL)
    assert by["Zzzxq Nobody"]["status"] == "NOT_FOUND" and by["Zzzxq Nobody"]["expert_id"] is None
    assert by["Jing Wang"]["status"] == "AMBIGUOUS" and by["Jing Wang"]["expert_id"] is None
    assert len(by["Jing Wang"]["candidates"]) == 10


# 13. A no-tie result states its hops, years and coverage (the scope), and says absence is not proof.
def test_golden_no_tie_states_its_scope():
    r = research.check_no_tie(NONE_EXPERT, 3, 5, "Expert #1")
    d, s = ok(r), r["scope"]
    assert d["result"] == "NO_TIE_FOUND" and d["query_name"] == "q3_bfs_v1"
    assert (s["hops"], s["years"], s["coverage"], s["funder_coverage"]) == (3, "2021-2026", 0.966, 0.563)
    assert "not proof" in d["wording"] and not BANNED.search(d["wording"])


# 14. Non-public experts are "Expert #n": no name in any output, and public_case is ignored for a non-GEBN list.
def test_golden_non_public_experts_are_expert_numbers():
    out = server.tie_report(HILL, expert_number=3)
    assert ok(out)["label"] == "Expert #3"
    names = research.names_of([], [HILL, X1])
    assert len(names) == 2, names  # both names found, so the 'no name in the output' checks below mean something
    assert not any(n in str(out) for n in names.values())
    res = server.check_case(expert_ids=[HILL, X1], public_case=True)  # X1 is not in the public list
    assert [r["label"] for r in ok(res)["results"]] == ["Expert #1", "Expert #2"] and res["warnings"]
    assert not any(n in str(res) for n in names.values())
    gebn = ok(server.check_case(expert_ids=list(GEBN), public_case=True))  # the documented public case: names allowed
    assert all(not r["label"].startswith("Expert #") for r in gebn["results"])


# 15. approve_note is blocked unless the note is verified (a rejected note, a missing note), and a verified one carries its DOI.
def test_golden_approve_note_blocked_unless_verified():
    fresh()
    cid = ok(server.open_case("Golden approve", "Coca-Cola", "obesity", [HILL]))["case_id"]
    bad = {"expert_id": HILL, "text": "Expert #1: ties found.", "evidence_refs": ["10.1234/made.up"]}
    d = ok(server.draft_note(cid, [bad]))
    assert d["status"] == "rejected"
    assert code(server.approve_note(d["note_id"], "Abdul")) == "NOT_APPROVED"
    assert code(server.approve_note("note_" + "0" * 32, "Abdul")) == "NOT_FOUND"
    good = {"expert_id": HILL, "text": "Expert #1: ties found.", "evidence_refs": [GEBN[HILL][2]]}
    n = ok(server.draft_note(cid, [good]))
    assert n["status"] == "verified"
    md = ok(server.approve_note(n["note_id"], "Abdul"))["markdown"]
    assert GEBN[HILL][2] in md and not BANNED.search(md)


# 16. An unknown or malformed expert id is NOT_FOUND (the tools never guess an expert).
def test_golden_unknown_expert_is_not_found():
    assert code(research.tie_report("A5000000001", "Expert #1")) == "NOT_FOUND"
    assert code(research.tie_report("not-an-id", "Expert #1")) == "NOT_FOUND"


# 17. A verdict on a DOI that is not in the graph is refused (NOT_FOUND), so a typo cannot change any answer.
def test_golden_verdict_on_unknown_doi_is_refused():
    fresh()
    assert code(server.record_verdict("PAPER_EXCLUDED", "10.1234/made.up", "typo", "Abdul")) == "NOT_FOUND"
    assert code(server.record_verdict("PAPER_EXCLUDED", BIG_DOI, "", "Abdul")) == "INVALID_ARG"  # a reason is required


# 18. A claim that names a case expert, or another expert's number, is rejected by the Verifier.
def test_golden_verifier_rejects_names_and_wrong_numbers():
    name = research.names_of([], [HILL])[HILL]
    base = {"expert_id": HILL, "kind": "tie", "label": "Expert #1", "evidence_refs": [GEBN[HILL][2]],
            "forbidden_names": [name]}
    assert verifier.verify_claim({**base, "text": f"{name}: ties found."}, [GEBN[HILL][2]])
    assert verifier.verify_claim({**base, "text": "Expert #2: ties found."}, [GEBN[HILL][2]])
    assert verifier.verify_claim({**base, "text": "Expert #1: ties found."}, [GEBN[HILL][2]]) == []


def cleanup():
    for g in DB.list_graphs():
        if g.startswith("newsroom:zz-test-golden"):
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
