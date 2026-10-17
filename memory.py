"""Checkpoint 6: the newsroom memory graph `newsroom:<id>` (Track 2). See docs/mcp-spec.md sections 3-5 and 9d.

What lives here (all in ONE graph per newsroom; the id comes only from env TIETRACE_NEWSROOM):
    (:Session)-[:HAS_EVENT]->(:Event)        one Session per server process, one Event per tool call
    (:Case)-[:HAS_CHECK]->(:Check)           a story's experts, and each saved tie check
    (:Check)-[:SUPERSEDES {diff_json}]->(:Check)   written by recheck (and by record_verdict for changed checks)
    (:Verdict)-[:AFFECTS {tier/score before -> after}]->(:Check)   checkpoint 7, written by record_verdict
    (:Case)-[:HAS_NOTE]->(:Note)             checkpoint 7, written by draft_note, approved by approve_note

Rules kept here: writes are the fixed, parameterised queries below (never built from input); every read
goes through GRAPH.RO_QUERY with a timeout and a row cap; the graph name is never a tool argument.
"""
import hashlib
import json
import os
import re
import time
import urllib.parse
import uuid
from datetime import datetime, timezone

from redis.exceptions import RedisError, ResponseError

import research
import verifier
from research import ToolError

NEWSROOM_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
ID_RE = re.compile(r"(ses|case|chk|evt|vrd|note)_[0-9a-f]{32}")
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
           "CREATE INDEX FOR (k:Check) ON (k.session_id)", "CREATE INDEX FOR (v:Verdict) ON (v.id)",
           "CREATE INDEX FOR (n:Note) ON (n.id)"]


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
    "RETURN k.id, k.expert_id, k.tier, k.score, k.ts, k.data_version, k.graph, c.id, c.story, k.session_id, k.agent, "
    "k.snapshot_json ORDER BY k.ts DESC LIMIT 100")
# Verdicts that name these experts (a PROFILE_MERGE pair, or experts whose checks a verdict changed).
PRIOR_VERDICTS_Q = ("MATCH (v:Verdict) WHERE any(x IN v.expert_ids WHERE x IN $ids) "
                    "RETURN v.id, v.type, v.target, v.reviewer, v.checks_changed, v.expert_ids, v.ts "
                    "ORDER BY v.ts DESC LIMIT 100")

KIND_WORD = {"FUNDED_BY": "funding", "AFFILIATED": "affiliation"}


def tier_summary(tier, snap):
    """What a saved tier rests on, in words, so nobody has to guess what 'A2' means (Gate E11 follow-up:
    a session read A2 as 'bottler affiliation' when it was Coca-Cola Foundation funding)."""
    snap = snap or {}
    if tier in ("A1", "A2"):
        items = [e for e in snap.get("evidence", []) if e.get("tier") == tier]
        kinds = list(dict.fromkeys(f"{e['via']} {KIND_WORD.get(e['kind'], e['kind'])} ({e['kind']})" for e in items))
        reasons = sorted({e["reason"] for e in items if e.get("reason")})  # saved since checkpoint 7
        text = f"{tier} via " + "; ".join(kinds[:3]) + (f"; and {len(kinds) - 3} more" if len(kinds) > 3 else "")
        return text + f", {len(items)} work(s)" + (f": {', '.join(reasons)}" if reasons else "") + " (direct evidence)"
    if tier == "B":
        return (f"B: repeated small-paper co-authorship with {len(snap.get('coauth_ties', []))} Tier-A person(s) "
                "(network context only, not validated)")
    if tier == "C":
        return (f"C: a Tier-A person within {(snap.get('nearest') or {}).get('hops')} co-author hops "
                "(network context only, not validated)")
    nt = snap.get("no_tie") or {}
    return (f"NONE: no tie found in the research record ({nt.get('query_name')}, {nt.get('hops')} hops, "
            f"{nt.get('years')}, funder coverage {nt.get('funder_coverage')}); absence is not proof")


def _score(score):
    return f"score {score} (context only, not validated)"


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
    for kid, eid, tier, score, ts, dv, graph, cid, cstory, sid, agent, snap in ro(PRIOR_MEMORY_Q, {"ids": expert_ids}, log):
        who = f" by {agent}" if agent else ""
        when = "earlier in this session" if sid == SESSION_ID else f"in session {sid}"
        basis = tier_summary(tier, json.loads(snap) if snap else None)
        prior.append({"kind": "check", "label": labels[eid], "expert_id": eid, "check_id": kid, "case_id": cid,
                      "tier": tier, "tier_basis": basis, "score": score, "score_basis": research.SCORE_BASIS,
                      "ts": ts, "data_version": dv, "graph": graph, "session_id": sid, "agent": agent,
                      "summary": f"{labels[eid]} was checked {ts[:10]} {when}{who}, in case '{cstory[:80]}', "
                                 f"tier {basis}"})
    for vid, vtype, target, reviewer, n, ids, ts in ro(PRIOR_VERDICTS_Q, {"ids": expert_ids}, log):
        named = ", ".join(labels[e] for e in expert_ids if e in ids)
        prior.append({"kind": "verdict", "verdict_id": vid, "type": vtype, "target": target, "ts": ts,
                      "summary": f"verdict {vtype} on {target} by {reviewer} ({ts[:10]}) concerns {named}; "
                                 f"it changed {n} saved check(s); research tools apply it in this newsroom"})
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
                "built_at: $built_at, verdicts: $verdicts, session_id: $sid, agent: $agent, ts: $ts}")
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
               "k.data_version, k.built_at, k.ts, c.id, collect(newer.id), k.verdicts")


def snapshot(report, no_tie=None):
    """What a Check keeps of a tie_report answer (and of check_no_tie for tier NONE): enough to diff it later."""
    d = report["data"]
    snap = {"evidence": [{k: e[k] for k in ("kind", "doi", "work_id", "year", "tier", "via", "topic_label", "reason")}
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
              "verdicts": list(report.get("verdicts_applied") or []),
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
            "built_at", "ts", "case_id", "superseded_by", "verdicts")
    out = dict(zip(keys, rows[0]))
    out["verdicts"] = out["verdicts"] or []  # Checks saved before checkpoint 7 have none
    return out


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
    "RETURN k.ts, k.id, k.label, k.tier, k.score, k.data_version, c.id, c.story, k.snapshot_json, k.expert_id "
    "ORDER BY k.ts DESC LIMIT $limit")
CHECKS_BY_CASE_Q = (
    "MATCH (c:Case)-[:HAS_CHECK]->(k:Check) WHERE c.id = $id "
    "RETURN k.ts, k.id, k.label, k.tier, k.score, k.data_version, c.id, c.story, k.snapshot_json, k.expert_id "
    "ORDER BY k.ts DESC LIMIT $limit")
CHECKS_BY_SESSION_Q = (
    "MATCH (c:Case)-[:HAS_CHECK]->(k:Check) WHERE k.session_id = $id "
    "RETURN k.ts, k.id, k.label, k.tier, k.score, k.data_version, c.id, c.story, k.snapshot_json, k.expert_id "
    "ORDER BY k.ts DESC LIMIT $limit")
EVENTS_BY_CASE_Q = ("MATCH (e:Event) WHERE e.case_id = $id AND e.tool <> 'recall' "
                    "RETURN e.ts, e.id, e.tool, e.ok, e.error_code, e.agent ORDER BY e.ts DESC LIMIT $limit")
EVENTS_BY_SESSION_Q = ("MATCH (s:Session)-[:HAS_EVENT]->(e:Event) WHERE s.id = $id "
                       "RETURN e.ts, e.id, e.tool, e.ok, e.error_code, e.agent ORDER BY e.ts DESC LIMIT $limit")


_V = "RETURN DISTINCT v.ts, v.id, v.type, v.target, v.reviewer, v.checks_changed ORDER BY v.ts DESC LIMIT $limit"
VERDICTS_BY_EXPERT_Q = "MATCH (v:Verdict) WHERE $id IN v.expert_ids " + _V
VERDICTS_BY_CASE_Q = "MATCH (v:Verdict)-[:AFFECTS]->(:Check)<-[:HAS_CHECK]-(c:Case) WHERE c.id = $id " + _V
VERDICTS_BY_SESSION_Q = "MATCH (v:Verdict) WHERE v.session_id = $id " + _V
_N = "RETURN n.ts, n.id, n.status, n.approved_by ORDER BY n.ts DESC LIMIT $limit"
NOTES_BY_CASE_Q = "MATCH (c:Case)-[:HAS_NOTE]->(n:Note) WHERE c.id = $id " + _N
NOTES_BY_SESSION_Q = "MATCH (n:Note) WHERE n.session_id = $id " + _N

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
        queries, key = [(CHECKS_BY_EXPERT_Q, "check"), (VERDICTS_BY_EXPERT_Q, "verdict")], expert_id
    elif case_id is not None:
        key = get_case(case_id, log)["id"]  # NOT_FOUND if unknown
        queries = [(CHECKS_BY_CASE_Q, "check"), (EVENTS_BY_CASE_Q, "event"), (VERDICTS_BY_CASE_Q, "verdict"),
                   (NOTES_BY_CASE_Q, "note")]
    else:
        key = check_id_arg(session_id, "ses", "session_id")
        if key != SESSION_ID and not ro(SESSION_Q, {"id": key}, log):
            raise ToolError("NOT_FOUND", f"no session {key} in this newsroom")
        queries = [(CHECKS_BY_SESSION_Q, "check"), (EVENTS_BY_SESSION_Q, "event"), (VERDICTS_BY_SESSION_Q, "verdict"),
                   (NOTES_BY_SESSION_Q, "note")]
    timeline = []
    for q, kind in queries:
        for row in ro(q, {"id": key, "limit": limit}, log):
            if kind == "check":
                ts, kid, label, tier, score, dv, cid, story, snap, eid = row
                basis = tier_summary(tier, json.loads(snap) if snap else None)
                timeline.append({"ts": ts, "kind": "check", "ref_id": kid, "case_id": cid, "expert_id": eid,
                                 "label": label, "tier": tier,
                                 "summary": f"{label} checked in case '{story[:80]}': tier {basis}, {_score(score)} ({dv})"})
            elif kind == "event":
                ts, eid, tool, ok, code, agent = row
                who = f" by {agent}" if agent else ""
                timeline.append({"ts": ts, "kind": "event", "ref_id": eid,
                                 "summary": f"{tool} called{who}: {'ok' if ok else code}"})
            elif kind == "verdict":  # the reason is never repeated (it may hold a name)
                ts, vid, vtype, target, reviewer, n = row
                timeline.append({"ts": ts, "kind": "verdict", "ref_id": vid,
                                 "summary": f"verdict {vtype} on {target} by {reviewer}: changed {n} saved check(s)"})
            else:
                ts, nid, status, approved_by = row
                timeline.append({"ts": ts, "kind": "note", "ref_id": nid,
                                 "summary": f"sourcing note {status}" + (f" by {approved_by}" if approved_by else "")})
    timeline.sort(key=lambda t: t["ts"])
    return {"timeline": timeline[-limit:], "session_id": SESSION_ID}


# ---------- checkpoint 7: verdicts (semantic memory) ----------

VERDICTS_Q = "MATCH (v:Verdict) RETURN v.id, v.type, v.doi, v.ids ORDER BY v.ts LIMIT 501"
SAME_VERDICT_Q = "MATCH (v:Verdict) WHERE v.type = $type AND v.target = $target RETURN v.id LIMIT 1"
# The current (not superseded) Checks of this newsroom, with what is needed to run them again.
HEADS_Q = ("MATCH (c:Case)-[:HAS_CHECK]->(k:Check) WHERE NOT ()-[:SUPERSEDES]->(k) "
           "RETURN k.id, k.expert_id, k.label, k.tier, k.score, k.no_tie, k.snapshot_json, k.graph, k.data_version, "
           "k.built_at, k.ts, c.id, c.research_case, c.public_case, k.verdicts ORDER BY k.ts LIMIT 501")
# The guard makes a second identical verdict write nothing, even from another server process (FalkorDB runs
# the writes to one graph one at a time, so this check and the CREATE cannot interleave with another write).
SAVE_VERDICT_Q = (
    "OPTIONAL MATCH (d:Verdict) WHERE d.type = $type AND d.target = $target WITH count(d) AS n WHERE n = 0 "
    "CREATE (v:Verdict {id: $id, type: $type, target: $target, doi: $doi, ids: $ids, reason: $reason, "
    "reviewer: $reviewer, expert_ids: $expert_ids, checks_changed: $checks_changed, research_case: $research_case, "
    "session_id: $sid, agent: $agent, ts: $ts}) RETURN v.id")
AFFECTS_Q = ("MATCH (v:Verdict) WHERE v.id = $vid MATCH (k:Check) WHERE k.id = $kid "
             "CREATE (v)-[:AFFECTS {tier_before: $tier_before, tier_after: $tier_after, score_before: $score_before, "
             "score_after: $score_after, new_check_id: $new_check_id}]->(k) RETURN k.id")
DOI_RE = re.compile(r"10\.\d{4,9}/\S{1,200}")
VERDICT_TYPES = ("PAPER_EXCLUDED", "PROFILE_MERGE")


def active_verdicts(log):
    """Every verdict of this newsroom, for research.with_verdicts. The read is added to `log` only when it
    found verdicts, so an answer with no verdicts in force looks as before (spec 9f)."""
    scratch = []
    rows = ro(VERDICTS_Q, {}, scratch)
    if not rows:
        return None
    log.extend(scratch)
    return {"ids": [r[0] for r in rows], "dois": [r[2] for r in rows if r[1] == "PAPER_EXCLUDED"],
            "merges": [r[3] for r in rows if r[1] == "PROFILE_MERGE"]}


def verdict_target(vtype, target):
    """Check a verdict's type and target. Returns (doi or None, ids [] or the sorted pair, stored target text)."""
    if vtype not in VERDICT_TYPES:
        raise ToolError("INVALID_ARG", "type must be PAPER_EXCLUDED or PROFILE_MERGE")
    if vtype == "PAPER_EXCLUDED":
        doi = verifier.norm_doi(target) if isinstance(target, str) else ""
        if not DOI_RE.fullmatch(doi):
            raise ToolError("INVALID_ARG", "PAPER_EXCLUDED needs target = one DOI (10.xxxx/...)")
        return doi, [], doi
    if (not isinstance(target, list) or len(target) != 2 or len(set(target)) != 2
            or not all(isinstance(i, str) and EXPERT_RE.fullmatch(i) for i in target)):
        raise ToolError("INVALID_ARG", "PROFILE_MERGE needs target = two different OpenAlex author ids [A..., A...]")
    ids = sorted(target)
    return None, ids, ",".join(ids)


def same_verdict(vtype, target, log):
    rows = ro(SAME_VERDICT_Q, {"type": vtype, "target": target}, log)
    return rows[0][0] if rows else None


def current_checks(log):
    rows = ro(HEADS_Q, {}, log)
    keys = ("id", "expert_id", "label", "tier", "score", "no_tie", "snapshot_json", "graph", "data_version",
            "built_at", "ts", "case_id", "research_case", "public_case", "verdicts")
    out = [dict(zip(keys, r)) for r in rows]
    for c in out:
        c["verdicts"] = c["verdicts"] or []
    return out


def save_verdict(log, vid, vtype, target, doi, ids, reason, reviewer, expert_ids, checks_changed, research_case,
                 agent):
    return write(SAVE_VERDICT_Q, {"id": vid, "type": vtype, "target": target, "doi": doi, "ids": ids, "reason": reason,
                           "reviewer": reviewer, "expert_ids": expert_ids, "checks_changed": checks_changed,
                           "research_case": research_case, "sid": SESSION_ID, "agent": agent, "ts": now()}, log)


def save_affects(log, vid, check_id, before, after, new_check_id):
    write(AFFECTS_Q, {"vid": vid, "kid": check_id, "tier_before": before["tier"], "tier_after": after["tier"],
                      "score_before": before["score"], "score_after": after["score"], "new_check_id": new_check_id},
          log)


# ---------- checkpoint 7: sourcing notes ----------

MAX_CLAIMS = 50
SAVE_NOTE_Q = (
    "MATCH (c:Case) WHERE c.id = $case_id CREATE (c)-[:HAS_NOTE]->(n:Note {id: $id, case_id: $case_id, "
    "status: $status, claims_json: $claims_json, rejections_json: $rejections_json, verdicts: $verdicts, "
    "graph: $graph, data_version: $data_version, built_at: $built_at, header_json: $header_json, "
    "experts_json: $experts_json, session_id: $sid, agent: $agent, ts: $ts}) RETURN n.id")
GET_NOTE_Q = ("MATCH (c:Case)-[:HAS_NOTE]->(n:Note) WHERE n.id = $id "
              "RETURN n.id, n.status, n.claims_json, n.verdicts, n.graph, n.data_version, n.approved_by, "
              "n.approved_at, n.ts, c.id, n.built_at, n.header_json, n.experts_json")
# Only a verified note can be approved; the WHERE makes two approvals at once write once.
APPROVE_NOTE_Q = ("MATCH (n:Note) WHERE n.id = $id AND n.status = 'verified' "
                  "SET n.status = 'approved', n.approved_by = $reviewer, n.approved_at = $ts RETURN n.id")


def claims_arg(claims, case):
    """Shape of draft_note's claims. Content (DOIs, scope, words, names) is the Verifier's job."""
    if not isinstance(claims, list) or not 1 <= len(claims) <= MAX_CLAIMS:
        raise ToolError("INVALID_ARG", f"claims must be a list of 1 to {MAX_CLAIMS} claims")
    for i, c in enumerate(claims):
        refs = c.get("evidence_refs") if isinstance(c, dict) else None
        if (not isinstance(c, dict) or set(c) - {"expert_id", "text", "evidence_refs"}
                or not isinstance(c.get("text"), str) or not c["text"].strip() or len(c["text"]) > 1000
                or not isinstance(refs, list) or len(refs) > 20 or not all(isinstance(r, str) and r.strip() for r in refs)):
            raise ToolError("INVALID_ARG", f"claim {i}: needs expert_id, text (1-1000 characters) and evidence_refs "
                                           "(a list of up to 20 DOIs or one query_name), and nothing else")
        if c.get("expert_id") not in case["expert_ids"]:
            raise ToolError("INVALID_ARG", f"claim {i}: expert_id is not one of this case's experts")
    return claims


def save_note(log, case_id, status, claims, rejections, verdicts, graph, data_version, built_at, header, experts,
              agent):
    """header: the case's story/company/topic, each None if it named an expert or used a banned word;
    experts: [{expert_id, label, disclosure}] for every expert of the case, from the draft-time run."""
    nid = new_id("note")
    rows = write(SAVE_NOTE_Q, {"case_id": case_id, "id": nid, "status": status,
                               "claims_json": json.dumps(claims, sort_keys=True),
                               "rejections_json": json.dumps(rejections, sort_keys=True), "verdicts": verdicts,
                               "graph": graph, "data_version": data_version, "built_at": built_at,
                               "header_json": json.dumps(header, sort_keys=True),
                               "experts_json": json.dumps(experts), "sid": SESSION_ID, "agent": agent,
                               "ts": now()}, log)
    if not rows:
        raise ToolError("NOT_FOUND", f"case {case_id} is gone; note not saved")
    return nid


def get_note(note_id, log):
    check_id_arg(note_id, "note", "note_id")
    rows = ro(GET_NOTE_Q, {"id": note_id}, log)
    if not rows:
        raise ToolError("NOT_FOUND", f"no note {note_id} in this newsroom")
    keys = ("id", "status", "claims_json", "verdicts", "graph", "data_version", "approved_by", "approved_at", "ts",
            "case_id", "built_at", "header_json", "experts_json")
    out = dict(zip(keys, rows[0]))
    out["verdicts"] = out["verdicts"] or []
    return out


def approve(note_id, reviewer, log):
    """Mark a verified note approved. Returns the approval time, or None if it was no longer 'verified'."""
    ts = now()
    return ts if write(APPROVE_NOTE_Q, {"id": note_id, "reviewer": reviewer, "ts": ts}, log) else None


def md_safe(text):
    """Journalist- or reviewer-typed text printed in the note: one line, HTML and Markdown control characters
    escaped, so a story title cannot inject links, headings, HTML or comments."""
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(text)).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_\[\]{}()#|!~])", r"\\\1", text)


def note_markdown(note, reviewer, approved_at):
    """The sourcing note: a section for every expert of the case with its disclosure line, each tie claim with
    its DOIs as links and what each DOI is evidence of, each no-tie claim with its scope. Built only from what
    draft_note stored; the case's story, company or topic is left out if it named an expert or used a banned word."""
    claims, header, experts = (json.loads(note[k]) for k in ("claims_json", "header_json", "experts_json"))
    left_out = "(left out: it names an expert or uses a banned word)"
    header = {k: (md_safe(v) if v else None) for k, v in header.items()}
    lines = [f"# Sourcing note: {header['story'] or 'case ' + note['case_id']}", "",
             f"Company: {header['company'] or left_out}. Topic: {header['topic'] or left_out}. Research graph "
             f"`{note['graph']}` ({note['data_version']}). Approved by {md_safe(reviewer)}, "
             f"{approved_at[:16].replace('T', ' ')} UTC. Note `{note['id']}`, case `{note['case_id']}`."
             + ("" if header["story"] else " The story title is left out: it names an expert or uses a banned word."),
             "Verdicts applied: " + (", ".join(note["verdicts"]) or "none") + ".", ""]
    for x in experts:
        mine = [c for c in claims if c["expert_id"] == x["expert_id"]]
        lines += [f"## {x['label']}", f"Disclosure: {x['disclosure']}", ""]
        for c in mine:
            if c["kind"] == "no_tie":
                s = c["scope"]
                mine_share = ("no loaded paper of this expert" if s.get("expert_funder_coverage") is None
                              else f"this expert: {round(100 * s['expert_funder_coverage'])}% of "
                                   f"{s.get('expert_works')} loaded works")
                lines.append(f"- {c['text']}  \n  Scope: query `{c['query_name']}`, {s['hops']} co-author hops, "
                             f"years {s['years']}; {round(100 * s['funder_coverage'])}% of topic works list a funder "
                             f"({mine_share}); {round(100 * s['coverage'])}% have funder or affiliation data. "
                             "Absence in the research record is not proof.")
            else:
                links = "; ".join(
                    f"[{x_['doi']}](https://doi.org/{urllib.parse.quote(x_['doi'], safe='/')}) ("
                    + " | ".join(f"{e['kind']} {e['via']}, tier {e['tier']}, {e['topic_label']}, {e['year']}"
                                for e in x_["evidence"]) + ")" for x_ in c["sources"])
                lines.append(f"- {c['text']}  \n  Sources: {links}")
        if not mine:
            lines.append("- No claim in this note.")
        lines.append("")
    lines.append("All records come from OpenAlex (CC0) as loaded into TieTrace; the research record can be incomplete.")
    return "\n".join(lines)
