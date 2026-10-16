"""Last updated by Abdul, Oct 16 2026: wrote E10.
E10 tenant isolation (checkpoint 8, spec section 7): a server with newsroom zz-test-iso-a cannot read newsroom
zz-test-iso-b's Checks, Events, Notes or Verdicts through ANY tool.

Run:  FALKORDB_PORT=6381 uv run python tests/test_isolation.py
Two real server processes. B builds a full history (case, Checks, a Verdict, a Note, Events). A then calls every
tool with B's ids, and every read tool for B's experts. Pass: 0 rows / NOT_FOUND, A's own graph holds nothing of
B's, and B's graph is not changed by A. The test lists the server's tools and fails if one is not covered here.
"""
import json
import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("FALKORDB_PORT", "6379")

import research  # noqa: E402
import test_server as ts  # noqa: E402

A, B = "zz-test-iso-a", "zz-test-iso-b"
DB = research._conn()
HILL, BLAIR, S_N_BLAIR, NONE_EXPERT = "A5031318756", "A5105003630", "A5087575650", "A5067322664"
# Every tool of server.py, and where this file calls it with B's data.
COVERED = {"resolve_experts", "find_experts", "list_cases",             # research only: no memory in the answer
           "tie_report", "check_no_tie", "bridges", "check_case",       # with B's case_id
           "open_case", "recall", "recheck",                            # B's prior checks, events, check ids
           "record_verdict", "draft_note", "approve_note"}              # B's verdict, case id, note id


def env(res):
    assert not res.is_error, res.content[0].text[:300]
    return res.structured_content


def new_client(newsroom):
    ts.NEWSROOM = newsroom
    return ts.Client()


def graph_counts(newsroom):
    g = DB.select_graph(f"newsroom:{newsroom}")
    return g.query("MATCH (n) RETURN labels(n)[0], count(n) ORDER BY labels(n)[0]").result_set


def not_found(out, what):
    assert out["ok"] is False and out["error"]["code"] == "NOT_FOUND", (what, out.get("error"), out.get("data"))


def test_alpha_cannot_see_beta():
    for n in (A, B):
        if f"newsroom:{n}" in DB.list_graphs():
            DB.select_graph(f"newsroom:{n}").delete()
    a, b = new_client(A), new_client(B)
    try:
        listed = {t.name for t in a.tools()}
        assert listed == COVERED, f"tool list changed; cover the new tools in this test: {listed ^ COVERED}"

        # --- B's history: a case, Checks, a Verdict (a merge), a verified Note, and the Events of all of it
        experts = [HILL, BLAIR, S_N_BLAIR, NONE_EXPERT]
        case = env(b.raw("open_case", {"story": "B secret story", "company": "Coca-Cola", "topic": "obesity",
                                       "expert_ids": experts}))["data"]["case_id"]
        checks = env(b.raw("check_case", {"case_id": case}))["data"]["results"]
        check_ids = [r["check_id"] for r in checks]
        assert all(check_ids)
        verdict = env(b.raw("record_verdict", {"type": "PROFILE_MERGE", "target": [BLAIR, S_N_BLAIR],
                                               "reason": "B only", "reviewer": "Abdul"}))["data"]["verdict_id"]
        hill_doi = env(b.raw("tie_report", {"expert_id": HILL}))["data"]["evidence"][0]["doi"]
        note = env(b.raw("draft_note", {"case_id": case, "claims": [
            {"expert_id": HILL, "text": "Expert #1: ties found in the research record.", "evidence_refs": [hill_doi]}]}))["data"]
        assert note["status"] == "verified"
        b_session = env(b.raw("recall", {"case_id": case}))["data"]["session_id"]
        b_events = env(b.raw("recall", {"session_id": b_session, "limit": 100}))["data"]
        assert b_events["timeline"], "fixture: B has events"
        before = graph_counts(B)
        secrets = [case, verdict, note["note_id"], b_session, "B secret story", "B only"] + check_ids

        # --- A calls every tool with B's ids (A has its own Session/Events only)
        not_found(env(a.raw("tie_report", {"expert_id": HILL, "case_id": case})), "tie_report case_id")
        not_found(env(a.raw("check_no_tie", {"expert_id": NONE_EXPERT, "case_id": case})), "check_no_tie case_id")
        not_found(env(a.raw("bridges", {"expert_id": HILL, "case_id": case})), "bridges case_id")
        not_found(env(a.raw("check_case", {"case_id": case})), "check_case case_id")
        for cid in check_ids:
            not_found(env(a.raw("recheck", {"check_id": cid})), "recheck")
        not_found(env(a.raw("draft_note", {"case_id": case, "claims": [
            {"expert_id": HILL, "text": "Expert #1: ties found.", "evidence_refs": [hill_doi]}]})), "draft_note case_id")
        not_found(env(a.raw("approve_note", {"note_id": note["note_id"], "reviewer": "Abdul"})), "approve_note")

        for what, args in (("case_id", {"case_id": case}), ("session_id", {"session_id": b_session})):
            out = env(a.raw("recall", args))
            if what == "case_id":
                not_found(out, "recall case_id")
            else:  # a session id of another newsroom: no rows, or NOT_FOUND
                assert (not out["ok"] and out["error"]["code"] == "NOT_FOUND") or out["data"]["timeline"] == [], out
        for eid in experts:  # B has Checks of every one of these experts; A has none
            out = env(a.raw("recall", {"expert_id": eid}))
            assert out["ok"] and out["data"]["timeline"] == [], (eid, out["data"])

        # open_case in A on B's experts: no prior_memory (B's checks are invisible)
        oc = env(a.raw("open_case", {"story": "A story", "company": "Coca-Cola", "topic": "obesity",
                                     "expert_ids": experts}))
        assert oc["ok"] and not oc["data"]["prior_memory"], oc["data"]["prior_memory"]
        a_case = oc["data"]["case_id"]
        # B's merge verdict is not in force in A: S. N. Blair is still NONE, no verdict is listed
        tr = env(a.raw("tie_report", {"expert_id": S_N_BLAIR}))
        assert tr["data"]["tier"] == "NONE" and tr["verdicts_applied"] == [], (tr["data"]["tier"], tr["verdicts_applied"])
        # A records its own verdict: its impact covers only A's checks (none yet), never B's
        ok_v = env(a.raw("check_case", {"case_id": a_case}))
        assert ok_v["ok"]
        rv = env(a.raw("record_verdict", {"type": "PAPER_EXCLUDED", "target": hill_doi, "reason": "A only",
                                          "reviewer": "Abdul"}))
        assert rv["ok"] and rv["data"]["impact"]["checks_rechecked"] == len(experts), rv["data"]["impact"]
        assert all(d["case_id"] == a_case for d in rv["data"]["impact"]["details"])
        # the research-only tools carry no memory
        reads = [env(a.raw("resolve_experts", {"names": ["James O. Hill"], "topic": "obesity"})),
                 env(a.raw("find_experts", {"topic": "obesity", "n": 5})), env(a.raw("list_cases", {}))]

        # --- nothing of B's anywhere in what A saw, A's graph holds nothing of B's, B's graph is unchanged
        seen = json.dumps([oc, tr, rv, ok_v, reads, env(a.raw("recall", {"case_id": a_case}))])
        for s in secrets:
            assert s not in seen, f"A's answers contain B's {s[:20]}"
        # An Event keeps A's own call arguments (A typed B's ids), so Events are checked for B's content, not for ids.
        a_graph = json.dumps(DB.select_graph(f"newsroom:{A}").query(
            "MATCH (n) WHERE NOT n:Event RETURN properties(n)").result_set)
        a_events = json.dumps(DB.select_graph(f"newsroom:{A}").query("MATCH (e:Event) RETURN e.result_digest").result_set)
        assert "B secret story" not in a_events and "B only" not in a_events
        for s in secrets:
            assert s not in a_graph, f"A's graph contains B's {s[:20]}"
        assert graph_counts(B) == before, "A changed B's graph"
        b_sessions = DB.select_graph(f"newsroom:{B}").query("MATCH (s:Session) RETURN count(s)").result_set[0][0]
        assert b_sessions == 1  # A's process never wrote a Session or Event into B's graph
    finally:
        a.close()
        b.close()


def cleanup():
    for n in (A, B):
        if f"newsroom:{n}" in DB.list_graphs():
            DB.select_graph(f"newsroom:{n}").delete()


if __name__ == "__main__":
    t0, failed = time.time(), 0
    try:
        test_alpha_cannot_see_beta()
        print(f"PASS  test_alpha_cannot_see_beta  ({time.time() - t0:.1f}s)")
    except Exception as e:
        failed = 1
        traceback.print_exc()
        print(f"FAIL  test_alpha_cannot_see_beta: {str(e)[:800]}")
    finally:
        cleanup()
    print(f"\n{1 - failed}/1 passed")
    sys.exit(failed)
