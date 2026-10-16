"""Checkpoint 6: the newsroom memory graph `newsroom:<id>` (Track 2). See docs/mcp-spec.md sections 3-5 and 9d.

What lives here (all in ONE graph per newsroom; the id comes only from env TIETRACE_NEWSROOM):
    (:Session)-[:HAS_EVENT]->(:Event)        one Session per server process, one Event per tool call
    (:Case)-[:HAS_CHECK]->(:Check)           a story's experts, and each saved tie check
    (:Check)-[:SUPERSEDES {diff_json}]->(:Check)   written by recheck

Rules kept here: writes are the fixed, parameterised queries below (never built from input); every read
goes through GRAPH.RO_QUERY with a timeout and a row cap; the graph name is never a tool argument.
"""
import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone

from redis.exceptions import RedisError, ResponseError

import research
from research import ToolError

NEWSROOM_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
ID_RE = re.compile(r"(ses|case|chk|evt)_[0-9a-f]{32}")
EXPERT_RE = re.compile(r"A\d{1,15}")
MAX_EXPERTS = 25    # same as check_case
MAX_TEXT = 2000     # story; company and topic get 200
MAX_ARGS_JSON = 4000

GRAPH = None        # "newsroom:<id>", set by use_newsroom()
SESSION_ID = None   # this server process's Session


def _uuid7_hex():
    """Python 3.13 has no uuid.uuid7 (added in 3.14): the same layout by hand. 48-bit Unix time in ms,
    version 7, 12 random bits, variant 10, 62 random bits. Ids made in the same millisecond are not
    ordered among themselves (uuid.uuid7 adds a counter for that); nothing here relies on it."""
    ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10))  # 80 random bits
    value = (ms << 80) | (0x7 << 76) | ((rand >> 68) << 64) | (0b10 << 62) | (rand & ((1 << 62) - 1))
    return f"{value:032x}"


def new_id(prefix):
    """case_..., chk_..., evt_..., ses_...: a UUIDv7 (time-ordered, like the ULID the spec names)."""
    return f"{prefix}_{uuid.uuid7().hex if hasattr(uuid, 'uuid7') else _uuid7_hex()}"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")  # sorts as text


def use_newsroom(newsroom):
    """Pick the tenant graph. Called once at server start-up with TIETRACE_NEWSROOM."""
    global GRAPH, SESSION_ID
    if not isinstance(newsroom, str) or not NEWSROOM_RE.fullmatch(newsroom):
        raise ValueError("newsroom id: lower case letters, digits, - or _, 1 to 40 characters")
    GRAPH, SESSION_ID = f"newsroom:{newsroom}", new_id("ses")


# ---------- the two ways in: a guarded read and a fixed write ----------

def _graph():
    if not GRAPH or not GRAPH.startswith("newsroom:") or not NEWSROOM_RE.fullmatch(GRAPH[9:]):
        raise ToolError("INTERNAL", "no newsroom graph selected")
    return research._conn().select_graph(GRAPH)


def _db_error(e):
    msg = str(e)
    if "timed out" in msg.lower():
        return ToolError("TIMEOUT", "memory query took longer than 3000 ms")
    return ToolError("DB_ERROR", f"{type(e).__name__}: {msg[:200]}")


def ro(query, params, log, row_cap=500):
    """A read with GRAPH.RO_QUERY (timeout 3 s). A newsroom with no memory yet reads as 0 rows."""
    log.append({"graph": GRAPH, "query": query, "params": params})
    try:
        rows = _graph().ro_query(query, params, timeout=3000).result_set
    except ResponseError as e:
        if "empty key" in str(e).lower():  # the newsroom graph does not exist yet
            return []
        raise _db_error(e)
    except RedisError as e:
        raise _db_error(e)
    if len(rows) > row_cap:
        raise ToolError("ROW_LIMIT", f"memory query returned {len(rows)} rows (cap {row_cap})")
    return rows


def write(query, params, log):
    """One of the fixed write queries below, with parameters only."""
    if log is not None:
        log.append({"graph": GRAPH, "query": query, "params": params})
    try:
        return _graph().query(query, params, timeout=3000).result_set
    except RedisError as e:
        raise _db_error(e)


INDEXES = ["CREATE INDEX FOR (s:Session) ON (s.id)", "CREATE INDEX FOR (e:Event) ON (e.id)",
           "CREATE INDEX FOR (e:Event) ON (e.case_id)", "CREATE INDEX FOR (c:Case) ON (c.id)",
           "CREATE INDEX FOR (k:Check) ON (k.id)", "CREATE INDEX FOR (k:Check) ON (k.expert_id)",
           "CREATE INDEX FOR (k:Check) ON (k.session_id)"]


def init():
    """Create the indexes once (an existing index is fine). Creates the newsroom graph if it is new."""
    for q in INDEXES:
        try:
            write(q, {}, None)
        except ToolError as e:
            if "already indexed" not in e.message:
                raise


# ---------- the event log: one Event per tool call ----------

LOG_EVENT_Q = (
    "MERGE (s:Session {id: $sid}) ON CREATE SET s.started_at = $ts, s.pid = $pid "
    "CREATE (s)-[:HAS_EVENT]->(:Event {id: $id, tool: $tool, args_json: $args_json, result_digest: $digest, "
    "ok: $ok, error_code: $error_code, agent: $agent, case_id: $case_id, duration_ms: $duration_ms, ts: $ts})")


def _short(value):
    """Long strings and lists are cut field by field, so args_json always stays valid JSON."""
    if isinstance(value, str) and len(value) > 300:
        return value[:300] + f"... (+{len(value) - 300} characters)"
    if isinstance(value, list):
        return [_short(v) for v in value[:50]] + ([f"... (+{len(value) - 50} more)"] if len(value) > 50 else [])
    return value


def _args_json(args):
    text = json.dumps({k: _short(v) for k, v in args.items()}, default=str, sort_keys=True)
    if len(text) > MAX_ARGS_JSON:  # still too long: keep only which arguments were given
        text = json.dumps({"truncated": True, "arguments": sorted(args)})
    return text


def log_event(tool, args, result, duration_ms=None):
    """Write one Event under this process's Session. Never raises: returns None, or a warning to show.
    duration_ms: the tool's wall time in ms (server.py measures it), or None."""
    try:
        data = result.get("data") if isinstance(result, dict) and isinstance(result.get("data"), dict) else {}
        ok = bool(isinstance(result, dict) and result.get("ok"))
        # an Event names a case only when the call succeeded, so a mistyped or foreign case_id leaves no trace
        case_id = (args.get("case_id") or data.get("case_id")) if ok else None
        agent = args.get("agent")
        params = {
            "sid": SESSION_ID, "pid": os.getpid(), "id": new_id("evt"), "tool": tool, "ts": now(),
            "args_json": _args_json(args),
            "digest": hashlib.sha256(json.dumps(result, default=str, sort_keys=True).encode()).hexdigest()[:16],
            "ok": ok,
            "error_code": (result.get("error") or {}).get("code") if isinstance(result, dict) else "INTERNAL",
            "agent": agent if isinstance(agent, str) else None,
            "case_id": case_id if isinstance(case_id, str) and ID_RE.fullmatch(case_id) else None,
            "duration_ms": float(duration_ms) if isinstance(duration_ms, (int, float)) and not isinstance(duration_ms, bool) else None}
        write(LOG_EVENT_Q, params, None)
        return None
    except Exception as e:  # logging must never break a tool
        return f"this call was not saved to the memory log ({type(e).__name__})"


# ---------- cases ----------

OPEN_CASE_Q = (
    "CREATE (c:Case {id: $id, story: $story, company: $company, topic: $topic, expert_ids: $expert_ids, "
    "public_case: $public_case, research_case: $research_case, session_id: $sid, created_at: $ts}) RETURN c.id")

GET_CASE_Q = ("MATCH (c:Case) WHERE c.id = $id RETURN c.id, c.story, c.company, c.topic, c.expert_ids, "
              "c.public_case, c.research_case, c.created_at")

# Latest checks (not superseded) on these experts, from every case of this newsroom.
PRIOR_MEMORY_Q = (
    "MATCH (c:Case)-[:HAS_CHECK]->(k:Check) WHERE k.expert_id IN $ids AND NOT ()-[:SUPERSEDES]->(k) "
    "RETURN k.id, k.expert_id, k.tier, k.score, k.ts, k.data_version, k.graph, c.id, c.story, k.session_id, k.agent "
    "ORDER BY k.ts DESC LIMIT 100")


def _text(value, name, cap):
    if not isinstance(value, str) or not value.strip() or len(value) > cap:
        raise ToolError("INVALID_ARG", f"{name} must be a non-empty string of at most {cap} characters")
    return value.strip()


def check_id_arg(value, prefix, name):
    if not isinstance(value, str) or not ID_RE.fullmatch(value) or not value.startswith(prefix + "_"):
        raise ToolError("INVALID_ARG", f"{name} must look like {prefix}_<32 hex characters>")
    return value


def expert_ids_arg(ids):
    if (not isinstance(ids, list) or not 1 <= len(ids) <= MAX_EXPERTS or len(set(ids)) != len(ids)
            or not all(isinstance(i, str) and EXPERT_RE.fullmatch(i) for i in ids)):
        raise ToolError("INVALID_ARG", f"expert_ids must be 1 to {MAX_EXPERTS} distinct OpenAlex author ids (A...)")
    return ids


def label_in_case(case, expert_id):
    """'Expert #n' = the expert's place in the case's list; the name only in a public case (handled by research)."""
    if expert_id not in case["expert_ids"]:
        raise ToolError("INVALID_ARG", f"{expert_id} is not one of this case's experts; open a new case to add one")
    return f"Expert #{case['expert_ids'].index(expert_id) + 1}"


def open_case(log, story, company, topic, expert_ids, public_case, research_case):
    """Store a Case and return its id, labels and prior_memory (the read runs before the write)."""
    story, company, topic = _text(story, "story", MAX_TEXT), _text(company, "company", 200), _text(topic, "topic", 200)
    expert_ids_arg(expert_ids)
    labels = {e: f"Expert #{i}" for i, e in enumerate(expert_ids, 1)}
    prior = []
    for kid, eid, tier, score, ts, dv, graph, cid, cstory, sid, agent in ro(PRIOR_MEMORY_Q, {"ids": expert_ids}, log):
        who = f" by {agent}" if agent else ""
        when = "earlier in this session" if sid == SESSION_ID else f"in session {sid}"
        prior.append({"label": labels[eid], "expert_id": eid, "check_id": kid, "case_id": cid, "tier": tier,
                      "score": score, "ts": ts, "data_version": dv, "graph": graph, "session_id": sid, "agent": agent,
                      "summary": f"{labels[eid]} was checked {ts[:10]} {when}{who}, in case '{cstory[:80]}', tier {tier}"})
    case_id = new_id("case")
    write(OPEN_CASE_Q, {"id": case_id, "story": story, "company": company, "topic": topic, "expert_ids": expert_ids,
                        "public_case": public_case, "research_case": research_case, "sid": SESSION_ID, "ts": now()},
          log)
    return {"case_id": case_id, "research_case": research_case, "public_case": public_case,
            "experts": [{"expert_id": e, "label": labels[e]} for e in expert_ids], "prior_memory": prior}


def get_case(case_id, log):
    check_id_arg(case_id, "case", "case_id")
    rows = ro(GET_CASE_Q, {"id": case_id}, log)
    if not rows:
        raise ToolError("NOT_FOUND", f"no case {case_id} in this newsroom")
    keys = ("id", "story", "company", "topic", "expert_ids", "public_case", "research_case", "created_at")
    return dict(zip(keys, rows[0]))


# ---------- checks ----------

_CHECK_PROPS = ("{id: $id, expert_id: $expert_id, label: $label, tier: $tier, score: $score, no_tie: $no_tie, "
                "evidence_dois: $dois, snapshot_json: $snapshot, graph: $graph, data_version: $data_version, "
                "built_at: $built_at, session_id: $sid, agent: $agent, ts: $ts}")
SAVE_CHECK_Q = "MATCH (c:Case) WHERE c.id = $case_id CREATE (c)-[:HAS_CHECK]->(k:Check " + _CHECK_PROPS + ") RETURN k.id"
# Only the current head of a chain can be superseded, so the chain never forks (two rechecks at once:
# the second write finds old already superseded and writes nothing).
SAVE_RECHECK_Q = ("MATCH (c:Case) WHERE c.id = $case_id MATCH (old:Check) WHERE old.id = $old "
                  "AND NOT ()-[:SUPERSEDES]->(old) "
                  "CREATE (c)-[:HAS_CHECK]->(k:Check " + _CHECK_PROPS + ")-[:SUPERSEDES {diff_json: $diff}]->(old) "
                  "RETURN k.id")
# WITH pins the start at the indexed Check, then the chain is walked backwards to its head (GRAPH.EXPLAIN:
# Node By Index Scan, then the variable-length traverse).
HEAD_Q = ("MATCH (k:Check) WHERE k.id = $id WITH k MATCH (k)<-[:SUPERSEDES*1..100]-(h:Check) "
          "WHERE NOT ()-[:SUPERSEDES]->(h) RETURN h.id LIMIT 1")
GET_CHECK_Q = ("MATCH (c:Case)-[:HAS_CHECK]->(k:Check) WHERE k.id = $id "
               "OPTIONAL MATCH (newer:Check)-[:SUPERSEDES]->(k) "
               "RETURN k.id, k.expert_id, k.label, k.tier, k.score, k.no_tie, k.snapshot_json, k.graph, "
               "k.data_version, k.built_at, k.ts, c.id, collect(newer.id)")


def snapshot(report, no_tie=None):
    """What a Check keeps of a tie_report answer (and of check_no_tie for tier NONE): enough to diff it later."""
    d = report["data"]
    snap = {"evidence": [{k: e[k] for k in ("kind", "doi", "work_id", "year", "tier", "via", "topic_label")}
                         for e in d["evidence"]],
            "coauth_ties": [{"to_expert_id": t["to_expert_id"], "n_papers": t["n_papers"], "dois": t["dois"]}
                            for t in d["coauth_ties"]],
            "score_parts": d["score_parts"], "nearest": d["nearest"], "profile_flag": d["profile_flag"],
            "no_tie": None}
    if no_tie and no_tie.get("ok"):
        s = no_tie["scope"]
        snap["no_tie"] = {"result": no_tie["data"]["result"], "query_name": no_tie["data"]["query_name"],
                          "hops": s.get("hops"), "years": s.get("years"), "coverage": s.get("coverage"),
                          "funder_coverage": s.get("funder_coverage"),
                          "expert_funder_coverage": s.get("expert_funder_coverage")}
    return snap


def save_check(log, case_id, report, no_tie=None, built_at=None, old_id=None, diff=None, agent=None):
    """Store a Check for one tie_report answer; with old_id, also (new)-[:SUPERSEDES]->(old). Returns the id."""
    d, snap = report["data"], snapshot(report, no_tie)
    dois = sorted({e["doi"] for e in d["evidence"] if e["doi"]} | {x for t in d["coauth_ties"] for x in t["dois"]})
    params = {"case_id": case_id, "id": new_id("chk"), "expert_id": d["expert_id"], "label": d["label"],
              "tier": d["tier"], "score": d["score"], "no_tie": (snap["no_tie"] or {}).get("result"),
              "dois": dois, "snapshot": json.dumps(snap, sort_keys=True), "graph": report["scope"]["graph"],
              "data_version": report["scope"].get("data_version"), "built_at": built_at, "sid": SESSION_ID,
              "agent": agent, "ts": now()}
    if old_id:
        rows = write(SAVE_RECHECK_Q, {**params, "old": old_id, "diff": json.dumps(diff, sort_keys=True)}, log)
    else:
        rows = write(SAVE_CHECK_Q, params, log)
    if not rows:
        raise ToolError("NOT_FOUND", f"case {case_id} is gone, or the old check was superseded meanwhile; nothing saved")
    return rows[0][0]


def head_of(check_id, log):
    """The newest Check that (through SUPERSEDES) replaces check_id, or None if check_id is the head."""
    rows = ro(HEAD_Q, {"id": check_id}, log)
    return rows[0][0] if rows else None


def get_check(check_id, log):
    check_id_arg(check_id, "chk", "check_id")
    rows = ro(GET_CHECK_Q, {"id": check_id}, log)
    if not rows:
        raise ToolError("NOT_FOUND", f"no check {check_id} in this newsroom")
    keys = ("id", "expert_id", "label", "tier", "score", "no_tie", "snapshot_json", "graph", "data_version",
            "built_at", "ts", "case_id", "superseded_by")
    return dict(zip(keys, rows[0]))


def evidence_diff(before, after):
    """Evidence items in one snapshot and not the other. A Tier B tie counts once per DOI."""
    def items(snap):
        out = {}
        for e in snap["evidence"]:
            out[(e["kind"], e["work_id"], e["via"])] = e
        for t in snap["coauth_ties"]:
            for doi in t["dois"]:
                out[("COAUTH_B", doi, t["to_expert_id"])] = {"kind": "COAUTH_B", "doi": doi,
                                                            "to_expert_id": t["to_expert_id"]}
        return out
    b, a = items(before), items(after)
    return [a[k] for k in a if k not in b], [b[k] for k in b if k not in a]


# ---------- recall: a timeline ----------

CHECKS_BY_EXPERT_Q = (
    "MATCH (c:Case)-[:HAS_CHECK]->(k:Check) WHERE k.expert_id = $id "
    "RETURN k.ts, k.id, k.label, k.tier, k.score, k.data_version, c.id, c.story ORDER BY k.ts DESC LIMIT $limit")
CHECKS_BY_CASE_Q = (
    "MATCH (c:Case)-[:HAS_CHECK]->(k:Check) WHERE c.id = $id "
    "RETURN k.ts, k.id, k.label, k.tier, k.score, k.data_version, c.id, c.story ORDER BY k.ts DESC LIMIT $limit")
CHECKS_BY_SESSION_Q = (
    "MATCH (c:Case)-[:HAS_CHECK]->(k:Check) WHERE k.session_id = $id "
    "RETURN k.ts, k.id, k.label, k.tier, k.score, k.data_version, c.id, c.story ORDER BY k.ts DESC LIMIT $limit")
EVENTS_BY_CASE_Q = ("MATCH (e:Event) WHERE e.case_id = $id AND e.tool <> 'recall' "
                    "RETURN e.ts, e.id, e.tool, e.ok, e.error_code, e.agent ORDER BY e.ts DESC LIMIT $limit")
EVENTS_BY_SESSION_Q = ("MATCH (s:Session)-[:HAS_EVENT]->(e:Event) WHERE s.id = $id "
                       "RETURN e.ts, e.id, e.tool, e.ok, e.error_code, e.agent ORDER BY e.ts DESC LIMIT $limit")


SESSION_Q = "MATCH (s:Session) WHERE s.id = $id RETURN s.id"


def recall(log, expert_id, case_id, session_id, limit):
    given = [x for x in (expert_id, case_id, session_id) if x is not None]
    if len(given) != 1:
        raise ToolError("INVALID_ARG", "give exactly one of expert_id, case_id or session_id")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ToolError("INVALID_ARG", "limit must be an integer from 1 to 100")
    if expert_id is not None:
        if not isinstance(expert_id, str) or not EXPERT_RE.fullmatch(expert_id):
            raise ToolError("INVALID_ARG", "expert_id must be an OpenAlex author id (A...)")
        queries, key = [CHECKS_BY_EXPERT_Q], expert_id
    elif case_id is not None:
        queries, key = [CHECKS_BY_CASE_Q, EVENTS_BY_CASE_Q], get_case(case_id, log)["id"]  # NOT_FOUND if unknown
    else:
        key = check_id_arg(session_id, "ses", "session_id")
        if key != SESSION_ID and not ro(SESSION_Q, {"id": key}, log):
            raise ToolError("NOT_FOUND", f"no session {key} in this newsroom")
        queries = [CHECKS_BY_SESSION_Q, EVENTS_BY_SESSION_Q]
    timeline = []
    for q in queries:
        for row in ro(q, {"id": key, "limit": limit}, log):
            if q.startswith("MATCH (c:Case)"):
                ts, kid, label, tier, score, dv, cid, story = row
                timeline.append({"ts": ts, "kind": "check", "ref_id": kid, "case_id": cid,
                                 "summary": f"{label} checked in case '{story[:80]}': tier {tier}, score {score} ({dv})"})
            else:
                ts, eid, tool, ok, code, agent = row
                who = f" by {agent}" if agent else ""
                timeline.append({"ts": ts, "kind": "event", "ref_id": eid,
                                 "summary": f"{tool} called{who}: {'ok' if ok else code}"})
    timeline.sort(key=lambda t: t["ts"])
    return {"timeline": timeline[-limit:], "session_id": SESSION_ID}
