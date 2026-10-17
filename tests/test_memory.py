"""Tests for checkpoint 6: the newsroom memory graph (memory.py, the Track 2 tools in server.py, staged load guards).

Run:  FALKORDB_PORT=6379 uv run python tests/test_memory.py [name-substring ...]
Builder's tests (the Reviewer adds more). Scratch graphs newsroom:zz-test-mem* are deleted at the end.
Checks are run on the real research:obesity-coke graph (read-only). No API calls.
"""
import json
import os
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["TIETRACE_NEWSROOM"] = "zz-test-mem"  # never the real alpha newsroom
import loader  # noqa: E402
import memory  # noqa: E402
import research  # noqa: E402
import server  # noqa: E402

DB = research._conn()
NR, NR2 = "newsroom:zz-test-mem", "newsroom:zz-test-mem2"
BLAIR, HILL, HAND = "A5105003630", "A5031318756", "A5046953968"  # documented GEBN case (public_ids)


def q(graph, query, params=None):
    return DB.select_graph(graph).query(query, params or {}).result_set


def ok(out):
    assert out["ok"] is True, out.get("error")
    assert set(out) >= {"ok", "data", "cypher", "scope", "verdicts_applied", "warnings"}
    return out["data"]


def test_newsroom_from_env_and_strict_id():
    assert memory.GRAPH == NR
    for bad in ("", "Alpha", "../x", "a b", "-x", "x" * 41, "alpha\n", None):
        try:
            memory.use_newsroom(bad)
            raise AssertionError(f"accepted {bad!r}")
        except ValueError:
            pass
    assert memory.GRAPH == NR


def test_every_tool_call_logs_one_event_under_the_session():
    before = q(NR, "MATCH (e:Event) RETURN count(e)")[0][0] if NR in DB.list_graphs() else 0
    server.find_experts("obesity", 2, True, agent="writer")
    server.tie_report("A0")  # an error is logged too
    rows = q(NR, "MATCH (s:Session {id: $sid})-[:HAS_EVENT]->(e:Event) RETURN e.tool, e.ok, e.error_code, e.agent, "
                 "e.args_json, e.result_digest ORDER BY e.ts DESC LIMIT 2", {"sid": memory.SESSION_ID})
    assert q(NR, "MATCH (e:Event) RETURN count(e)")[0][0] == before + 2
    (t2, ok2, code2, ag2, _, d2), (t1, ok1, _, ag1, args1, d1) = rows
    assert (t1, ok1, ag1) == ("find_experts", True, "writer") and json.loads(args1)["topic"] == "obesity"
    assert (t2, ok2, code2, ag2) == ("tie_report", False, "NOT_FOUND", None) and len(d1) == len(d2) == 16


def test_a_logging_failure_is_a_warning_not_an_error():
    real = memory.write

    def broken(query, params, log):
        if query == memory.LOG_EVENT_Q:
            raise research.ToolError("DB_ERROR", "simulated")
        return real(query, params, log)
    memory.write = broken
    try:
        out = server.find_experts("obesity", 2, True)
    finally:
        memory.write = real
    ok(out)
    assert any("not saved to the memory log" in w for w in out["warnings"]), out["warnings"]


def test_open_case_check_case_and_prior_memory_across_sessions():
    a = ok(server.open_case("Story A", "Coca-Cola", "obesity", [HILL, BLAIR]))
    assert [e["label"] for e in a["experts"]] == ["Expert #1", "Expert #2"]
    res = ok(server.check_case(case_id=a["case_id"]))
    assert all(r["check_id"] and r["check_id"].startswith("chk_") for r in res["results"]), res["results"]
    assert res["case_id"] == a["case_id"]
    saved = q(NR, "MATCH (:Case {id: $c})-[:HAS_CHECK]->(k:Check) RETURN k.expert_id, k.tier, k.evidence_dois, k.graph",
              {"c": a["case_id"]})
    assert {r[0] for r in saved} == {HILL, BLAIR} and all(r[2] and r[3] == "research:obesity-coke" for r in saved)
    old_session = memory.SESSION_ID
    memory.use_newsroom("zz-test-mem")  # a new server process: a new Session, same newsroom
    b = ok(server.open_case("Story B", "Coca-Cola", "obesity", [BLAIR, HAND]))
    prior = b["prior_memory"]
    assert prior and {p["expert_id"] for p in prior} == {BLAIR}, prior  # E11: not empty
    assert prior[0]["label"] == "Expert #1" and "Story A" in prior[0]["summary"]
    assert old_session != memory.SESSION_ID


def test_case_labels_public_case_and_case_mismatch():
    c = ok(server.open_case("Public", "Coca-Cola", "obesity", [HILL, BLAIR], True))
    assert c["public_case"] is True
    t = ok(server.tie_report(BLAIR, case_id=c["case_id"]))
    assert t["label"] != "Expert #2" and t["check_id"]  # the name, only in the documented public case
    p = server.open_case("Not public", "Coca-Cola", "obesity", [HILL, "A5000000001"], True)
    assert ok(p)["public_case"] is False and p["warnings"]
    t = ok(server.tie_report(HILL, case_id=p["data"]["case_id"]))
    assert t["label"] == "Expert #1"
    assert server.tie_report(BLAIR, case_id=p["data"]["case_id"])["error"]["code"] == "INVALID_ARG"  # not in case
    assert server.tie_report(HILL, case="philip-morris-lung", case_id=p["data"]["case_id"])["error"]["code"] == "INVALID_ARG"
    assert server.check_no_tie(HILL, case_id=p["data"]["case_id"])["data"]["label"] == "Expert #1"


def test_bad_arguments_give_envelopes():
    bad = [server.open_case("", "c", "t", [HILL]), server.open_case("s", "c", "t", []),
           server.open_case("s", "c", "t", ["bob"]), server.open_case("s", "c", "t", [HILL, HILL]),
           server.open_case("s", "c", "t", [HILL], case="no-such-case"),
           server.check_case(), server.check_case([HILL], case_id="case_" + "0" * 32),
           server.recall(), server.recall(expert_id=HILL, case_id="case_" + "0" * 32), server.recall(expert_id=HILL, limit=0),
           server.recheck("chk_bad"), server.recheck("chk_" + "0" * 32), server.tie_report(HILL, agent="Dr Who")]
    codes = [b["error"]["code"] for b in bad]
    assert codes == ["INVALID_ARG"] * 4 + ["NO_GRAPH", "INVALID_ARG", "INVALID_ARG", "INVALID_ARG", "INVALID_ARG",
                                           "INVALID_ARG", "INVALID_ARG", "NOT_FOUND", "INVALID_ARG"], codes


def test_recall_by_expert_case_and_session():
    c = ok(server.open_case("Recall", "Coca-Cola", "obesity", [HAND]))
    chk = ok(server.tie_report(HAND, case_id=c["case_id"]))["check_id"]
    by_case = ok(server.recall(case_id=c["case_id"]))["timeline"]
    assert [t["kind"] for t in by_case] == ["event", "check", "event"], by_case  # open_case, the check, tie_report
    assert by_case[1]["ref_id"] == chk and [t["ts"] for t in by_case] == sorted(t["ts"] for t in by_case)
    by_expert = ok(server.recall(expert_id=HAND))["timeline"]
    assert chk in [t["ref_id"] for t in by_expert] and all(t["kind"] == "check" for t in by_expert)
    by_session = ok(server.recall(session_id=memory.SESSION_ID, limit=3))["timeline"]
    assert len(by_session) == 3


def test_recheck_writes_supersedes_and_names_the_cause():
    c = ok(server.open_case("Recheck", "Coca-Cola", "obesity", [BLAIR]))
    chk = ok(server.tie_report(BLAIR, case_id=c["case_id"]))["check_id"]
    same = ok(server.recheck(chk))
    assert same["diff"]["cause"] is None and same["diff"]["changed"] is False and same["check_id"]
    head = same["check_id"]
    # pretend the current check was made on an older build with one piece of evidence fewer
    snap = json.loads(q(NR, "MATCH (k:Check {id: $id}) RETURN k.snapshot_json", {"id": head})[0][0])
    dropped = snap["evidence"].pop(0)
    q(NR, "MATCH (k:Check {id: $id}) SET k.built_at = 'older', k.snapshot_json = $s", {"id": head, "s": json.dumps(snap)})
    d = ok(server.recheck(head))
    assert d["diff"]["cause"] == "new_data" and d["diff"]["rebuilt"] and d["diff"]["new_evidence"] == [dropped]
    assert not d["diff"]["removed_evidence"]
    # spec 9d (changed after review): rechecking a superseded check rechecks the newest one, so the chain never forks
    again = server.recheck(chk)
    assert any("already superseded" in w for w in again["warnings"]) and again["data"]["before"]["check_id"] == d["check_id"]
    n = q(NR, "MATCH (:Case {id: $c})-[:HAS_CHECK]->(:Check)-[s:SUPERSEDES]->(:Check) RETURN count(s)", {"c": c["case_id"]})[0][0]
    assert n == 3 and q(NR, "MATCH ()-[s:SUPERSEDES]->(:Check {id: $id}) RETURN count(s)", {"id": chk})[0][0] == 1
    prior = ok(server.open_case("After", "Coca-Cola", "obesity", [BLAIR]))["prior_memory"]
    assert chk not in [p["check_id"] for p in prior]  # superseded checks are not offered as prior memory


def test_other_newsroom_sees_nothing():
    ok(server.open_case("Alpha only", "Coca-Cola", "obesity", [HILL]))
    memory.use_newsroom("zz-test-mem2")
    try:
        assert ok(server.recall(expert_id=HILL))["timeline"] == []
        assert ok(server.open_case("Beta", "Coca-Cola", "obesity", [HILL]))["prior_memory"] == []
        any_chk = q(NR, "MATCH (k:Check) RETURN k.id LIMIT 1")[0][0]
        assert server.recheck(any_chk)["error"]["code"] == "NOT_FOUND"
    finally:
        memory.use_newsroom("zz-test-mem")


def test_memory_reads_use_ro_query_and_writes_are_fixed():
    src = (ROOT / "memory.py").read_text(encoding="utf-8")
    assert src.count(".ro_query(") == 1 and src.count(".query(") == 1
    assert ".format(" not in src and "%s" not in src
    for line in src.splitlines():  # no Cypher text is built from a variable at call time
        if ("MATCH" in line or "CREATE" in line) and line.lstrip().startswith(("f\"", "f'")):
            raise AssertionError(line)


def test_until_year_only_on_a_demo_copy():
    for args in (["--case", "obesity-coke", "--until-year", "2023"],
                 ["--case", "obesity-coke-staged", "--until-year", "2026"],
                 ["--case", "obesity-coke-staged", "--refresh"]):
        p = subprocess.run(["uv", "run", "python", "loader.py", *args], cwd=ROOT, capture_output=True, text=True, timeout=120)
        assert p.returncode != 0 and "Traceback" not in p.stderr, (args, p.stderr[-300:])
    cfg = loader.load_config("obesity-coke-staged")
    base = loader.load_config("obesity-coke")
    same = {k for k in base if k not in ("slug", "notes", "max_api_calls", "decided_by")}
    assert all(cfg[k] == base[k] for k in same) and cfg["cache_from"] == "obesity-coke"
    for bad in ("obesity-coke-staged", "../x", 5):
        try:
            loader.check_config({**cfg, "cache_from": bad}, "obesity-coke-staged")
            raise AssertionError(bad)
        except ValueError:
            pass


# ---------- Reviewer tests (checkpoint 6, Oct 16) ----------

NONE_EXPERT = "A5067322664"  # tier NONE in research:obesity-coke (openalex@2026-10-15)


def test_reviewer_every_saved_none_check_keeps_its_no_tie_scope():
    # Rule: a "no tie" claim needs query_name + scope. A Check with tier NONE is such a claim, however it was saved.
    c = ok(server.open_case("None scope", "Coca-Cola", "obesity", [NONE_EXPERT]))
    via_tie_report = ok(server.tie_report(NONE_EXPERT, case_id=c["case_id"]))
    assert via_tie_report["tier"] == "NONE", "fixture drifted: pick another tier-NONE expert"
    via_check_case = ok(server.check_case(case_id=c["case_id"]))["results"][0]["check_id"]
    for chk in (via_tie_report["check_id"], via_check_case):
        no_tie, snap = q(NR, "MATCH (k:Check {id: $id}) RETURN k.no_tie, k.snapshot_json", {"id": chk})[0]
        nt = json.loads(snap)["no_tie"]
        assert no_tie in ("NO_TIE_FOUND", "TIE_FOUND") and nt and nt["query_name"], (chk, no_tie, nt)
        assert nt["hops"] and nt["years"] and nt["coverage"] is not None, nt


def test_reviewer_public_case_names_only_the_public_ids():
    # A name appears only for the case's approved public_ids; co-authors and Tier-A people stay anonymous.
    public = [HILL, BLAIR, HAND, "A5045252457"]
    names = {r["label"] for r in ok(server.check_case(public, True))["results"]}
    c = ok(server.open_case("Public names", "Coca-Cola", "obesity", public, True))
    seen = set()
    for eid in public:
        t, b, n = (server.tie_report(eid, case_id=c["case_id"]), server.bridges(eid, 2, 5, case_id=c["case_id"]),
                   server.check_no_tie(eid, case_id=c["case_id"]))
        seen |= {x["to_label"] for x in ok(t)["coauth_ties"]}
        seen |= {node for p in ok(b).get("paths", []) for node in p["nodes"]}
        seen |= {ok(n)["nearest"]["tier_a_label"]} if ok(n)["nearest"] else set()
    leaked = sorted(s for s in seen if s not in names and not s.startswith(("Expert #", "Co-author #", "Tier-A #")))
    assert not leaked, f"names outside public_ids: {leaked}"


def test_reviewer_recheck_keeps_one_current_check_per_case_and_expert():
    # Rechecking twice (or two agents at once) must not leave two "latest" Checks for the same case and expert.
    c = ok(server.open_case("Fork", "Coca-Cola", "obesity", [HAND]))
    chk = ok(server.tie_report(HAND, case_id=c["case_id"]))["check_id"]
    server.recheck(chk)
    server.recheck(chk)  # chk is already superseded here
    heads = q(NR, "MATCH (:Case {id: $c})-[:HAS_CHECK]->(k:Check) WHERE NOT ()-[:SUPERSEDES]->(k) RETURN count(k)",
              {"c": c["case_id"]})[0][0]
    assert heads == 1, f"{heads} current checks for one expert in one case"


def test_reviewer_unknown_case_id_in_recall_is_not_found():
    # spec 9d: "A check, case or session id from another newsroom gives NOT_FOUND" (as tie_report/check_case do).
    ghost = "case_" + "ab" * 16
    server.tie_report(HILL, case_id=ghost)  # a failed call that names the ghost case
    out = server.recall(case_id=ghost)
    assert out["ok"] is False and out["error"]["code"] == "NOT_FOUND", out


def test_reviewer_two_processes_lose_no_events():
    code = ("import os,sys; sys.path.insert(0, %r); os.environ['TIETRACE_NEWSROOM']='zz-test-mem'; import server, memory\n"
            "for i in range(50):\n"
            "    out = server.open_case(f'p{i}', 'c', 't', [%r]) if i %% 2 else server.recall(expert_id=%r)\n"
            "    assert out['ok'] and not out['warnings'], out\n"
            "print(memory.SESSION_ID)") % (str(ROOT), HILL, HILL)
    env = dict(os.environ, TIETRACE_NEWSROOM="zz-test-mem")
    procs = [subprocess.Popen([sys.executable, "-c", code], cwd=ROOT, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, text=True) for _ in range(2)]
    sids = [p.communicate(timeout=120)[0].strip() for p in procs]
    assert all(p.returncode == 0 for p in procs) and len(set(sids)) == 2, sids
    for sid in sids:
        n = q(NR, "MATCH (:Session {id: $s})-[:HAS_EVENT]->(e:Event) RETURN count(e), count(DISTINCT e.id)", {"s": sid})[0]
        assert n == [50, 50], (sid, n)


def test_reviewer_event_logging_is_cheap():
    t0 = time.time()
    for _ in range(50):
        assert memory.log_event("list_cases", {"agent": None}, {"ok": True, "data": {}}) is None
    per_call_ms = (time.time() - t0) * 1000 / 50
    assert per_call_ms < 20, f"{per_call_ms:.1f} ms per Event write"


def test_reviewer_story_text_is_data_and_bad_agent_is_not_stored():
    evil = "x'}) MATCH (n) DETACH DELETE n // $id \" \\"
    n0 = q(NR, "MATCH (n) RETURN count(n)")[0][0]
    c = ok(server.open_case(evil, evil, evil, [HILL]))
    assert q(NR, "MATCH (c:Case {id: $id}) RETURN c.story", {"id": c["case_id"]})[0][0] == evil.strip()
    assert q(NR, "MATCH (n) RETURN count(n)")[0][0] == n0 + 2  # the Case and its Event, nothing deleted
    server.resolve_experts(["Some Long Name"] * 300, "obesity", agent="Dr Who")
    agent, args = q(NR, "MATCH (s:Session {id: $s})-[:HAS_EVENT]->(e:Event) RETURN e.agent, e.args_json "
                        "ORDER BY e.ts DESC LIMIT 1", {"s": memory.SESSION_ID})[0]
    assert agent is None and len(args) <= memory.MAX_ARGS_JSON and "Dr Who" not in args


def test_reviewer_failed_rows_and_missing_graph_save_nothing():
    c = ok(server.open_case("Partial", "Coca-Cola", "obesity", [HILL, "A5000000001"]))
    res = ok(server.check_case(case_id=c["case_id"]))["results"]
    assert [r["check_id"] is None for r in res] == [False, True], res  # the unknown id ranks last, no Check
    assert q(NR, "MATCH (:Case {id: $c})-[:HAS_CHECK]->(k) RETURN count(k)", {"c": c["case_id"]})[0][0] == 1
    chk = res[0]["check_id"]
    # the case's research graph is gone (a deleted staged demo graph): NO_GRAPH, and nothing is written
    q(NR, "MATCH (c:Case {id: $c}) SET c.research_case = 'zz-gone-case'", {"c": c["case_id"]})
    real = research.known_cases
    research.known_cases = lambda: real() + ["zz-gone-case"]
    real_cfg = loader.load_config
    loader.load_config = lambda slug: {**real_cfg("obesity-coke"), "slug": slug} if slug == "zz-gone-case" else real_cfg(slug)
    try:
        n0 = q(NR, "MATCH (k:Check) RETURN count(k)")[0][0]
        assert server.recheck(chk)["error"]["code"] == "NO_GRAPH"
        assert server.check_case(case_id=c["case_id"])["error"]["code"] == "NO_GRAPH"
        assert q(NR, "MATCH (k:Check) RETURN count(k)")[0][0] == n0
    finally:
        research.known_cases, loader.load_config = real, real_cfg


def test_reviewer_prior_memory_newest_first_with_new_labels():
    a = ok(server.open_case("Old story", "Coca-Cola", "obesity", [HILL, BLAIR]))
    ok(server.check_case(case_id=a["case_id"]))
    b = ok(server.open_case("Newer story", "Coca-Cola", "obesity", [BLAIR]))
    ok(server.check_case(case_id=b["case_id"]))
    prior = ok(server.open_case("Third", "Coca-Cola", "obesity", [HAND, BLAIR, HILL]))["prior_memory"]
    ts = [p["ts"] for p in prior]
    assert ts == sorted(ts, reverse=True), ts
    labels = {p["expert_id"]: p["label"] for p in prior}
    assert labels.get(BLAIR) == "Expert #2" and labels.get(HILL) == "Expert #3", labels  # labels of the NEW case
    assert all(BANNED not in p["summary"].lower() for p in prior for BANNED in ("independent", "conflicted"))


# ---------- Reviewer tests, round 2 (guards the round-1 fixes rely on) ----------

def test_reviewer_r2_none_check_not_saved_when_check_no_tie_fails():
    c = ok(server.open_case("None fails", "Coca-Cola", "obesity", [NONE_EXPERT]))
    real = research.check_no_tie
    research.check_no_tie = lambda *a, **k: {"ok": False, "error": {"code": "TIMEOUT", "message": "simulated"},
                                             "cypher": []}
    try:
        t = ok(server.tie_report(NONE_EXPERT, case_id=c["case_id"]))
        rows = ok(server.check_case(case_id=c["case_id"]))["results"]
    finally:
        research.check_no_tie = real
    assert t["tier"] == "NONE" and t["check_id"] is None, t["check_id"]
    assert rows[0]["check_id"] is None, rows
    assert q(NR, "MATCH (:Case {id: $c})-[:HAS_CHECK]->(k) RETURN count(k)", {"c": c["case_id"]})[0][0] == 0


def test_reviewer_r2_superseded_check_cannot_be_superseded_again():
    # The write itself refuses (what makes a real race safe), not only the head_of redirect before it.
    c = ok(server.open_case("Guard", "Coca-Cola", "obesity", [BLAIR]))
    chk = ok(server.tie_report(BLAIR, case_id=c["case_id"]))["check_id"]
    ok(server.recheck(chk))
    with research.on_graph("research:obesity-coke"):
        rep = research.tie_report(BLAIR, "Expert #1", False)
    try:
        memory.save_check([], c["case_id"], rep, old_id=chk, diff={})
        raise AssertionError("a second SUPERSEDES into a superseded check was written")
    except research.ToolError as e:
        assert e.code == "NOT_FOUND", e.code
    assert q(NR, "MATCH ()-[s:SUPERSEDES]->(:Check {id: $id}) RETURN count(s)", {"id": chk})[0][0] == 1


def test_reviewer_r2_ghost_ids_leave_no_trace_and_recall_skips_itself():
    ghost = "case_" + "cd" * 16
    server.tie_report(HILL, case_id=ghost)
    assert q(NR, "MATCH (e:Event) WHERE e.case_id = $g RETURN count(e)", {"g": ghost})[0][0] == 0
    assert server.recall(session_id="ses_" + "cd" * 16)["error"]["code"] == "NOT_FOUND"
    assert ok(server.recall(session_id=memory.SESSION_ID))  # the current session is always allowed
    c = ok(server.open_case("Quiet", "Coca-Cola", "obesity", [HILL]))
    server.recall(case_id=c["case_id"])
    kinds = [t["summary"].split()[0] for t in ok(server.recall(case_id=c["case_id"]))["timeline"]]
    assert kinds == ["open_case"], kinds


def test_reviewer_r2_nearest_tier_a_person_stays_anonymous_in_public_mode():
    # The GEBN experts are all Tier A (nearest = themselves), so test the hop>0 path on a Tier C expert directly.
    with research.on_graph("research:obesity-coke"):
        out = research.check_no_tie("A5070828386", 3, server._span(), "Expert #1", True)
    nearest = ok(out)["nearest"]
    assert nearest and nearest["hops"] > 0, "fixture drifted: pick another Tier C expert"
    assert nearest["tier_a_label"] == "Tier-A #1", nearest["tier_a_label"]


# ---------- hardening round (Aaryan, Oct 16) ----------

def test_hardening_uuid7_fallback_is_time_ordered_v7():
    """Python 3.13 has no uuid.uuid7: force the stdlib fallback and check layout, uniqueness and order."""
    import uuid
    real = getattr(uuid, "uuid7", None)
    if real is not None:
        del uuid.uuid7
    try:
        assert not hasattr(uuid, "uuid7")
        ids = []
        for _ in range(20):
            ids.append(memory.new_id("evt"))
            time.sleep(0.002)  # next millisecond: ids from different ms must sort by time
        burst = [memory.new_id("chk") for _ in range(2000)]  # same ms: still unique
    finally:
        if real is not None:
            uuid.uuid7 = real
    assert ids == sorted(ids) and len(set(burst)) == len(burst)
    for i in ids + burst[:50]:
        assert memory.ID_RE.fullmatch(i), i
        u = uuid.UUID(i.split("_", 1)[1])
        assert u.version == 7 and u.variant == uuid.RFC_4122, i
    ms = int(ids[0][4:16], 16)  # first 48 bits: Unix time in ms
    assert abs(ms - time.time() * 1000) < 60_000, ms
    if real is not None:  # the real uuid7 and the fallback sort together
        assert memory.new_id("evt") > ids[-1]


def test_hardening_every_event_has_duration_ms():
    server.find_experts("obesity", 2, True)
    server.tie_report("A0")  # an error call is timed too
    rows = q(NR, "MATCH (s:Session {id: $sid})-[:HAS_EVENT]->(e:Event) RETURN e.tool, e.duration_ms "
                 "ORDER BY e.ts DESC LIMIT 2", {"sid": memory.SESSION_ID})
    assert [r[0] for r in rows] == ["tie_report", "find_experts"], rows
    for tool, ms in rows:
        assert isinstance(ms, float) and 0 < ms < 60_000, (tool, ms)
    assert memory.log_event("list_cases", {"agent": None}, {"ok": True, "data": {}}) is None  # old callers still fine


def test_hardening_report_runs_the_none_bfs_once_and_saves_funder_coverage():
    with research.on_graph("research:obesity-coke"):
        rep, nt = server._report(NONE_EXPERT, "Expert #1", False)
        alone = research.check_no_tie(NONE_EXPERT, 3, server._span(), "Expert #1", False)
    assert rep["data"]["tier"] == "NONE" and nt["data"]["result"] == "NO_TIE_FOUND"
    assert sum("algo.BFS" in c["query"] for c in rep["cypher"]) == 3, "the BFS ran twice"
    assert (nt["data"], nt["scope"]) == (alone["data"], alone["scope"]), "sharing the BFS changed the answer"
    snap = memory.snapshot(rep, nt)["no_tie"]
    assert snap["funder_coverage"] == nt["scope"]["funder_coverage"] and 0 < snap["funder_coverage"] < 1
    assert snap["expert_funder_coverage"] == nt["scope"]["expert_funder_coverage"]
    c = ok(server.open_case("Funder scope", "Coca-Cola", "obesity", [NONE_EXPERT]))
    chk = ok(server.tie_report(NONE_EXPERT, case_id=c["case_id"]))["check_id"]
    saved = json.loads(q(NR, "MATCH (k:Check {id: $id}) RETURN k.snapshot_json", {"id": chk})[0][0])["no_tie"]
    assert saved["funder_coverage"] == snap["funder_coverage"], saved


def test_hardening_check_case_puts_tier_a_first_then_input_order():
    ids = [NONE_EXPERT, HILL, BLAIR]  # NONE, A2, A1 in input order
    res = ok(server.check_case(expert_ids=ids))
    assert [r["expert_id"] for r in res["results"]] == [BLAIR, HILL, NONE_EXPERT]
    assert [r["tier_basis"] for r in res["results"][:2]] == ["direct evidence", "direct evidence"]
    assert "not ranked by score" in res["order"].lower()


# ---------- Reviewer, hardening round (Oct 16) ----------

def test_reviewer_h_funder_coverage_is_per_graph_and_recounted_not_meta_copied():
    """Kills a hard-coded 0.563: on the second graph the share must be that graph's own, recounted by Cypher."""
    seen = {}
    for g in ("research:obesity-coke", "research:philip-morris-lung"):
        topic, funded = DB.select_graph(g).ro_query(
            "MATCH (w:Work)-[:ABOUT]->(:Topic) WITH DISTINCT w OPTIONAL MATCH (w)-[f:FUNDED_BY]->(:Funder) "
            "WITH w, count(f) AS nf RETURN count(w), sum(CASE WHEN nf > 0 THEN 1 ELSE 0 END)",
            timeout=60000).result_set[0]
        pid = q(g, "MATCH (p:Person)-[:AUTHORED]->(w:Work) WHERE NOT w.junk RETURN p.oa_id LIMIT 1")[0][0]
        with research.on_graph(g):
            out = research.check_no_tie(pid, 3, server._span())
        assert out["ok"], out.get("error")
        seen[g] = out["scope"]["funder_coverage"]
        assert seen[g] == round(int(funded) / int(topic), 3), (g, seen[g], funded, topic)
    assert seen["research:obesity-coke"] != seen["research:philip-morris-lung"], seen


def test_reviewer_h_expert_with_only_junk_works_gets_null_share_and_passes_verifier():
    import verifier
    pid = q("research:obesity-coke", "MATCH (p:Person)-[:AUTHORED]->(w:Work) WITH p, "
            "sum(CASE WHEN w.junk THEN 0 ELSE 1 END) AS good WHERE good = 0 RETURN p.oa_id LIMIT 1")[0][0]
    with research.on_graph("research:obesity-coke"):
        out = research.check_no_tie(pid, 3, server._span(), "Expert #1")
    s, d = out["scope"], out["data"]
    assert s["expert_works"] == 0 and s["expert_funder_coverage"] is None and 0 < s["funder_coverage"] < 1, s
    assert d["result"] == "NO_TIE_FOUND" and "has no loaded paper" in d["wording"], d["wording"]
    assert not re.search(r"\b(independent|conflicted)\b", d["wording"], re.I)
    claim = {"expert_id": pid, "kind": "no_tie", "text": d["wording"], "query_name": d["query_name"], "scope": s}
    assert verifier.verify_claim(claim, []) == []


def test_reviewer_h_searched_hops_never_hides_a_tier_a_expert():
    """searched_hops only skips the BFS; an expert with direct Tier A evidence is still TIE_FOUND at 0 hops."""
    with research.on_graph("research:obesity-coke"):
        for pid in (BLAIR, HILL):
            a, b = research.check_no_tie(pid, 3, server._span()), research.check_no_tie(pid, 3, server._span(), searched_hops=3)
            assert a["data"] == b["data"] and b["data"]["result"] == "TIE_FOUND" and b["data"]["nearest"]["hops"] == 0, b


def test_reviewer_h_check_case_by_case_id_orders_and_saves_funder_scope():
    ids = [NONE_EXPERT, HILL, BLAIR]
    c = ok(server.open_case("Order and scope", "Coca-Cola", "obesity", ids))
    res = ok(server.check_case(case_id=c["case_id"]))
    assert [r["expert_id"] for r in res["results"]] == [BLAIR, HILL, NONE_EXPERT], res["results"]
    none_row = res["results"][2]
    assert none_row["no_tie"] == "NO_TIE_FOUND" and none_row["check_id"]
    snap = json.loads(q(NR, "MATCH (k:Check {id: $id}) RETURN k.snapshot_json", {"id": none_row["check_id"]})[0][0])
    assert snap["no_tie"]["funder_coverage"] is not None and "expert_funder_coverage" in snap["no_tie"], snap["no_tie"]
    with research.on_graph("research:obesity-coke"):
        fresh = research.check_no_tie(NONE_EXPERT, 3, server._span(), none_row["label"])
    assert snap["no_tie"]["funder_coverage"] == fresh["scope"]["funder_coverage"]
    assert snap["no_tie"]["expert_funder_coverage"] == fresh["scope"]["expert_funder_coverage"]


# ---------- P2-4 (Aaryan, Oct 17): both funder shares in every no-tie path, on both cases ----------
SHARE_KEYS = ("funder_coverage", "expert_funder_coverage", "expert_works")


def rq(graph, query, params=None):
    """A read-only research-graph read with its own 60 s timeout (the server default is 1 s)."""
    return DB.select_graph(graph).ro_query(query, params or {}, timeout=60000).result_set


def none_experts(slug, n=2):
    """n tier NONE experts of research:<slug> with DIFFERENT own funder shares, found by query: people with
    3+ non-junk works and no COAUTH_B link (so neither B nor C), then confirmed NONE by tie_report."""
    rows = rq(f"research:{slug}", "MATCH (p:Person)-[:AUTHORED]->(w:Work) WHERE NOT w.junk "
             "AND NOT (p)-[:COAUTH_B]->() WITH p, count(w) AS k WHERE k >= 3 "
             "RETURN p.oa_id ORDER BY p.oa_id LIMIT 60")
    picked, shares = [], set()
    with research.on_graph(f"research:{slug}"):
        for (pid,) in rows:
            rep = research.tie_report(pid)
            share = rep["scope"].get("expert_funder_coverage")
            if rep["ok"] and rep["data"]["tier"] == "NONE" and share not in shares:
                picked.append(pid)
                shares.add(share)
                if len(picked) == n:
                    return picked
    raise AssertionError(f"{slug}: found only {picked} as tier NONE with different shares")


def recount_share(slug, pid):
    """(works, share) recounted here with plain Cypher, not through research.py."""
    g = f"research:{slug}"
    works = {r[0] for r in rq(g, "MATCH (p:Person)-[:AUTHORED]->(w:Work) WHERE p.oa_id = $id AND NOT w.junk "
                                "RETURN DISTINCT w.oa_id", {"id": pid})}
    funded = {r[0] for r in rq(g, "MATCH (p:Person)-[:AUTHORED]->(w:Work)-[:FUNDED_BY]->(:Funder) WHERE p.oa_id = $id "
                                 "AND NOT w.junk RETURN DISTINCT w.oa_id", {"id": pid})}
    return len(works), (round(len(funded) / len(works), 3) if works else None)


def test_p24_every_no_tie_path_carries_both_funder_shares_on_both_cases():
    for slug, company, topic in (("obesity-coke", "Coca-Cola", "obesity"),
                                 ("philip-morris-lung", "Philip Morris International", "lung cancer")):
        pids = none_experts(slug)
        with research.on_graph(f"research:{slug}"):
            span = server._span()
        want = {}
        for pid in pids:
            nt = server.check_no_tie(pid, 3, span, case=slug)
            assert ok(nt)["result"] == "NO_TIE_FOUND", (slug, pid)
            s = nt["scope"]
            assert (s["expert_works"], s["expert_funder_coverage"]) == recount_share(slug, pid), (slug, pid, s)
            assert 0 < s["funder_coverage"] < 1 and s["graph"] == f"research:{slug}"
            want[pid] = {k: s[k] for k in SHARE_KEYS}
            tr = server.tie_report(pid, case=slug)
            assert ok(tr)["tier"] == "NONE" and {k: tr["scope"].get(k) for k in SHARE_KEYS} == want[pid], (slug, tr["scope"])
            for depth in (2, 3):
                br = server.bridges(pid, depth, case=slug)
                assert ok(br)["paths"] == [] and {k: br["scope"].get(k) for k in SHARE_KEYS} == want[pid], (slug, depth)
        assert len({w["expert_funder_coverage"] for w in want.values()}) == 2  # the two experts differ
        # check_case: each row keeps its own share; the batch scope never carries one expert's share
        for res in (server.check_case(expert_ids=pids, case=slug),
                    server.check_case(case_id=ok(server.open_case("P2-4 scope", company, topic, pids, case=slug))["case_id"])):
            rows = {r["expert_id"]: r for r in ok(res)["results"]}
            for pid in pids:
                r = rows[pid]
                assert r["no_tie"] == "NO_TIE_FOUND" and (r["expert_funder_coverage"], r["expert_works"]) == \
                    (want[pid]["expert_funder_coverage"], want[pid]["expert_works"]), (slug, r)
            assert "expert_funder_coverage" not in res["scope"] and "expert_works" not in res["scope"], res["scope"]
            assert res["scope"]["funder_coverage"] == want[pids[0]]["funder_coverage"]
        rc = server.recheck(rows[pids[1]]["check_id"])
        assert ok(rc)["after"]["no_tie"] == "NO_TIE_FOUND"
        assert {k: rc["scope"].get(k) for k in SHARE_KEYS} == want[pids[1]], (slug, rc["scope"])


def test_p24_check_case_rows_with_a_tie_carry_null_shares():
    res = ok(server.check_case(expert_ids=[BLAIR, "A0"]))
    rows = {r["expert_id"]: r for r in res["results"]}
    for pid in (BLAIR, "A0"):  # Tier A1 and an unknown id: no no-tie, so no expert share
        assert (rows[pid]["expert_funder_coverage"], rows[pid]["expert_works"]) == (None, None), rows[pid]


def cleanup():
    for g in (NR, NR2):
        if g in DB.list_graphs():
            DB.select_graph(g).delete()


if __name__ == "__main__":
    only = sys.argv[1:]
    tests = [(n, f) for n, f in list(globals().items()) if n.startswith("test_") and callable(f)
             and (not only or any(o in n for o in only))]
    cleanup()
    memory.init()
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
