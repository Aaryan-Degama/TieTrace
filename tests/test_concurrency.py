"""Last updated by Abdul, Oct 16 2026: wrote E9.
E9 concurrency (checkpoint 8, spec section 7): two reporter sessions on the SAME newsroom.

Run:  FALKORDB_PORT=6381 uv run python tests/test_concurrency.py
Two real server processes (like two Claude Code windows) share newsroom zz-test-conc. Each opens a case of 10
experts and runs check_case at the same time, then each records a verdict at the same time. The two verdicts
overlap on purpose: a PROFILE_MERGE of S. N. Blair into Blair (session B) and a PAPER_EXCLUDED on one of Blair's
papers (session A) both change Blair's and S. N. Blair's score, and both sessions have Checks of them.
Pass (spec s7): every Check and Verdict is stored, none lost, and each current Check equals a fresh rerun with all
verdicts in force. The scratch newsroom graph is deleted at the end.
"""
import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("FALKORDB_PORT", "6379")

import memory  # noqa: E402
import research  # noqa: E402
import test_server as ts  # noqa: E402  (its Client spawns the real server)

NAME = "zz-test-conc"
GRAPH = f"newsroom:{NAME}"
DB = research._conn()
HILL, BLAIR, HAND, PETERS, S_N_BLAIR = "A5031318756", "A5105003630", "A5046953968", "A5045252457", "A5087575650"
X1, X2, NONE_EXPERT = "A5071304044", "A5089714758", "A5067322664"
BLAIR_PAPER = "10.1016/j.mhpa.2017.12.001"  # one of Blair's A1 evidence papers (checkpoint 7 gate)


def q(query, params=None):
    return DB.select_graph(GRAPH).query(query, params or {}).result_set


def env(res):
    assert not res.is_error, res.content[0].text[:300]
    return res.structured_content


def new_client():
    ts.NEWSROOM = NAME  # test_server.server_env reads this when the process is spawned
    return ts.Client()


def run_together(calls):
    """calls = [(client, tool, args), ...]. Each runs in its own thread, released at the same moment. Returns
    (the envelopes, whether the wall-clock intervals of the calls overlap)."""
    gate, out = threading.Barrier(len(calls)), [None] * len(calls)

    def go(i, client, tool, args):
        gate.wait()
        t0 = time.perf_counter()
        res = client.raw(tool, args)
        out[i] = (env(res), t0, time.perf_counter())

    threads = [threading.Thread(target=go, args=(i, *c)) for i, c in enumerate(calls)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    overlap = max(o[1] for o in out) < min(o[2] for o in out)
    return [o[0] for o in out], overlap


def more_experts(n, skip):
    """Real authors on the topic from the live graph (read only), not already in `skip`."""
    got = research.find_experts("obesity", 40, True)["data"]
    return [e["expert_id"] for e in got["experts"] if e["expert_id"] not in skip][:n]


def test_two_sessions_lose_nothing():
    if GRAPH in DB.list_graphs():
        DB.select_graph(GRAPH).delete()
    fixed_a = [X1, X2, HILL, BLAIR, S_N_BLAIR, HAND]
    fixed_b = [S_N_BLAIR, BLAIR, X1, NONE_EXPERT, PETERS]
    extra = more_experts(9, set(fixed_a + fixed_b))
    experts_a = fixed_a + extra[:4]
    experts_b = fixed_b + extra[4:9]
    assert len(experts_a) == len(experts_b) == 10 and len(set(experts_a)) == len(set(experts_b)) == 10

    a, b = new_client(), new_client()
    try:
        ca = env(a.raw("open_case", {"story": "Conc A", "company": "Coca-Cola", "topic": "obesity", "expert_ids": experts_a}))
        cb = env(b.raw("open_case", {"story": "Conc B", "company": "Coca-Cola", "topic": "obesity", "expert_ids": experts_b}))
        case_a, case_b = ca["data"]["case_id"], cb["data"]["case_id"]
        assert ca["ok"] and cb["ok"]

        # at the same time: each session checks its 10 experts
        (ra, rb), overlapped = run_together([(a, "check_case", {"case_id": case_a}), (b, "check_case", {"case_id": case_b})])
        assert overlapped, "E9 not proven: the two check_case calls did not run at the same time"
        assert ra["ok"] and rb["ok"], (ra.get("error"), rb.get("error"))
        for r in (ra, rb):
            assert len(r["data"]["results"]) == 10 and all(x["check_id"] for x in r["data"]["results"])
        first_ids = {x["check_id"] for r in (ra, rb) for x in r["data"]["results"]}
        assert len(first_ids) == 20

        # at the same time: each session records a verdict (overlapping on Blair and S. N. Blair)
        (va, vb), overlapped = run_together([
            (a, "record_verdict", {"type": "PAPER_EXCLUDED", "target": BLAIR_PAPER, "reason": "E9 test", "reviewer": "Abdul"}),
            (b, "record_verdict", {"type": "PROFILE_MERGE", "target": [BLAIR, S_N_BLAIR], "reason": "E9 test",
                                   "reviewer": "Abdul"})])
        assert overlapped, "E9 not proven: the two record_verdict calls did not run at the same time"
        assert va["ok"], va.get("error")
        assert vb["ok"], vb.get("error")
        print(f"      verdict A: {va['data']['impact']['checks_changed']} checks changed; "
              f"verdict B: {vb['data']['impact']['checks_changed']} changed")
        print(f"      warnings A: {va['warnings']}; B: {vb['warnings']}")
    finally:
        a.close()
        b.close()

    # nothing lost: both cases, 20 first Checks, 2 Verdicts, a Session per process
    assert q("MATCH (c:Case) RETURN count(c)")[0][0] == 2
    assert q("MATCH (v:Verdict) RETURN count(v)")[0][0] == 2
    assert q("MATCH (s:Session) RETURN count(s)")[0][0] == 2
    stored = {r[0] for r in q("MATCH (k:Check) RETURN k.id")}
    assert first_ids <= stored, "a first Check was lost"
    for r, which in ((va, "A"), (vb, "B")):
        for d in r["data"]["impact"]["details"]:  # an impact report names real Checks, with the numbers stored on them
            assert d["new_check_id"] in stored, (which, d)
            tier, score = q("MATCH (k:Check {id: $i}) RETURN k.tier, k.score", {"i": d["new_check_id"]})[0]
            assert (tier, score) == (d["tier_after"], d["score_after"]), (which, d, tier, score)
    # one current Check per expert per case (no expert has two current Checks, none has zero)
    heads = q("MATCH (c:Case)-[:HAS_CHECK]->(k:Check) WHERE NOT ()-[:SUPERSEDES]->(k) "
              "RETURN c.id, k.expert_id, k.tier, k.score, k.verdicts")
    for case_id, experts in ((case_a, experts_a), (case_b, experts_b)):
        mine = [h for h in heads if h[0] == case_id]
        assert sorted(h[1] for h in mine) == sorted(experts), f"{case_id}: current Checks {len(mine)} for 10 experts"

    # each current Check equals a fresh rerun with both verdicts in force
    memory.use_newsroom(NAME)
    both = memory.active_verdicts([])
    assert both and len(both["ids"]) == 2
    stale = []
    with research.with_verdicts(both):
        for case_id, eid, tier, score, verdicts in heads:
            fresh = research.tie_report(eid, "Expert #1")["data"]
            if (fresh["tier"], fresh["score"]) != (tier, score):  # unchanged Checks keep their older verdict list, so compare numbers
                stale.append((eid, tier, score, "fresh:", fresh["tier"], fresh["score"]))
    assert not stale, f"{len(stale)} current Check(s) do not match a fresh rerun with both verdicts: {stale}"


def cleanup():
    if GRAPH in DB.list_graphs():
        DB.select_graph(GRAPH).delete()


if __name__ == "__main__":
    t0, failed = time.time(), 0
    try:
        test_two_sessions_lose_nothing()
        print(f"PASS  test_two_sessions_lose_nothing  ({time.time() - t0:.1f}s)")
    except Exception as e:
        failed = 1
        traceback.print_exc()
        print(f"FAIL  test_two_sessions_lose_nothing: {str(e)[:800]}")
    finally:
        cleanup()
    print(f"\n{1 - failed}/1 passed")
    sys.exit(failed)
