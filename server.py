"""Checkpoints 5 and 6: the TieTrace MCP server (stdio). See docs/mcp-spec.md sections 4, 5, 9b-9d.

Track 1 tools: resolve_experts, find_experts, tie_report, check_no_tie, bridges, check_case, list_cases.
They call research.py (reads only, through GRAPH.RO_QUERY) and return the section-4 envelope.
Track 2 tools (checkpoint 6): open_case, recall, recheck. They call memory.py (the newsroom graph).
Checkpoint 7: record_verdict and approve_note (human-only: Claude Code's permission prompt is the gate, never
allowlist them) and draft_note (the Writer's tool; the Verifier runs on every claim). The research tools that
judge ties apply this newsroom's verdicts (research.with_verdicts) and list them in verdicts_applied.
No LLM call, no API key.

Case (checkpoint 5d): every research tool except list_cases takes an optional `case` slug. It must name a
folder cases/<slug>/ with a valid case.json, and the tool then reads research:<slug>. The default is env
TIETRACE_CASE, else obesity-coke. A tool never takes a graph name. A call with a newsroom `case_id` uses
the research case that newsroom case was opened on.

Newsroom: TIETRACE_NEWSROOM (env, required) picks the memory graph newsroom:<id>; no tool takes it.
Every tool call writes one Event under this process's Session (memory.log_event); a logging failure is
only a warning on the answer.

Names: tools never take a name or a label. Single-expert tools say "Expert #<n>": from `expert_number`,
or from the expert's place in the newsroom case. A name appears only in a case whose experts are all in
the research case's approved public_ids (case.json; for obesity-coke the documented GEBN case).

Stdout belongs to the MCP protocol. Everything we print goes to stderr.
Run: FALKORDB_PORT=6380 TIETRACE_NEWSROOM=alpha uv run python server.py
"""
import functools
import inspect
import json
import logging
import os
import re
import sys
import threading
import time
from typing import Any

NEWSROOM = os.environ.get("TIETRACE_NEWSROOM", "")
if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", NEWSROOM):  # same rule as memory.NEWSROOM_RE
    sys.exit("TieTrace: set TIETRACE_NEWSROOM to the newsroom id: 1 to 40 lower case letters, digits, - or _, "
             "starting with a letter or digit (for example alpha)")

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

import loader
import memory
import research
import verifier

try:  # a wrong TIETRACE_CASE must stop the server at once, like a missing TIETRACE_NEWSROOM
    loader.load_config(research.default_case())
except (ValueError, OSError):
    sys.exit(f"TieTrace: TIETRACE_CASE={research.default_case()!r} is not a case with a valid cases/<slug>/case.json; "
             f"known cases: {', '.join(research.known_cases()) or 'none'}")

memory.use_newsroom(NEWSROOM)

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="tietrace %(levelname)s %(message)s")
log = logging.getLogger("tietrace")

MAX_BATCH = 25         # experts per check_case call
BATCH_BUDGET_S = 90    # check_case stops starting new experts after this many seconds

RULES = (" Say 'ties found in the research record'; absence of a tie is not proof; every tie needs its DOI. "
         "Experts are 'Expert #n'; do not guess or add names.")

# The only experts who may be named are the case's public_ids in cases/<slug>/case.json (obesity-coke: the
# documented GEBN case, eval/results.md E3). public_case is honoured only when EVERY id is in that list.
CASE_NOTE = (" case: optional research case slug (see list_cases); default is the server's case. "
             "Answers name the graph and data_version in scope.")
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
# Tools that add memory records (a Case or Checks) to the newsroom graph. Nothing is ever deleted, and the
# research graph is never written. (Every tool also writes its Event; that log is not counted here.)
WRITES = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
# Human-only tools (CLAUDE.md: never allowlisted). A verdict changes every later answer in the newsroom and
# supersedes saved Checks, so it is marked destructive; approve_note only adds the approval to a note.
HUMAN_VERDICT = ToolAnnotations(title="Human only: record a verdict", readOnlyHint=False, destructiveHint=True,
                                idempotentHint=False, openWorldHint=False)
HUMAN_APPROVE = ToolAnnotations(title="Human only: approve a sourcing note", readOnlyHint=False,
                                destructiveHint=False, idempotentHint=True, openWorldHint=False)
AGENT_RE = re.compile(r"[a-z][a-z0-9_-]{0,29}")

mcp = MCPServer("tietrace", instructions="TieTrace checks how connected an expert is to the company in a story, "
                "using an OpenAlex research graph (read-only) and this newsroom's memory of earlier cases and "
                "checks." + RULES)


def tool(annotations=READ_ONLY):
    """Register fn as a tool; its docstring plus the wording rules is the description. Every call is logged
    as one Event (tool, args, result digest, agent, duration_ms). `agent` is checked here, before the tool runs."""
    def register(fn):
        params = inspect.signature(fn).parameters
        note = CASE_NOTE if "case" in params else ""
        note += " agent: optional name of the calling agent (for example writer), for the memory log only."

        @functools.wraps(fn)
        def logged(*args, **kwargs):
            t0 = time.perf_counter()
            call = inspect.signature(fn).bind(*args, **kwargs)
            call.apply_defaults()
            agent = call.arguments.get("agent")
            if agent is not None and not (isinstance(agent, str) and AGENT_RE.fullmatch(agent)):
                out = _error("INVALID_ARG", "agent must be 1 to 30 lower case letters, digits, - or _ (for example writer)")
                call.arguments["agent"] = None  # never store a bad value
            else:
                out = fn(*args, **kwargs)
            duration_ms = round(1000 * (time.perf_counter() - t0), 1)  # the tool's wall time, before the Event write
            warning = memory.log_event(fn.__name__, dict(call.arguments), out, duration_ms)
            if warning:
                log.warning("%s: %s", fn.__name__, warning)
                if out.get("ok"):
                    out["warnings"].append(warning)
            return out
        return mcp.tool(structured_output=True, description=" ".join(fn.__doc__.split()) + note + RULES,
                        annotations=annotations)(logged)
    return register


_lock = threading.RLock()  # re-entrant (one thread may nest calls); research.py keeps one DB connection and one cache per process: one call at a time


def _error(code, message, cypher=None):
    return {"ok": False, "error": {"code": code, "message": message}, "cypher": cypher or []}


def _ok(data, cypher, scope, warnings=None, verdicts=None):
    return {"ok": True, "data": data, "cypher": cypher, "scope": scope, "verdicts_applied": verdicts or [],
            "warnings": warnings or []}


def _case_config(case):
    """The case's checked config. case=None means the server default. Raises research.ToolError."""
    slug = research.default_case() if case is None else case
    if not isinstance(slug, str) or not loader.SLUG_RE.fullmatch(slug):
        raise research.ToolError("INVALID_ARG", "case must be a case slug: lower case letters, digits and -, "
                                                "2 to 40 characters (see list_cases)")
    if slug not in research.known_cases():
        raise research.ToolError("NO_GRAPH", f"unknown case '{slug}'; known cases: "
                                             f"{', '.join(research.known_cases()) or 'none'}")
    try:
        return loader.load_config(slug)
    except ValueError as e:  # the validator's own message names the problem, never a path
        raise research.ToolError("NO_GRAPH", f"case '{slug}' has no valid case.json ({str(e)[:150]})")
    except OSError:
        raise research.ToolError("NO_GRAPH", f"case '{slug}' has no readable case.json")


def _safe(fn, *args, case=None, verdicts=False):
    """Run a research function on the case's graph; whatever happens, return an envelope (no traceback, no paths).
    verdicts=True: apply this newsroom's verdicts (the memory read that found them comes first in `cypher`)."""
    mlog = []
    try:
        with _lock:
            slug = _case_config(case)["slug"]
            research.drop_cache_if_rebuilt(slug)
            found = memory.active_verdicts(mlog) if verdicts else None
            with research.on_graph(f"research:{slug}"), research.with_verdicts(found):
                out = fn(*args)
        out["cypher"] = mlog + out.get("cypher", [])
        return out
    except research.ToolError as e:
        return _error(e.code, e.message, mlog)
    except Exception as e:  # last resort: research.py already turns its own errors into envelopes
        log.exception("tool failed")
        return _error("INTERNAL", f"unexpected {type(e).__name__}; see the server log")


def _label(expert_number):
    n = research._int(expert_number, "expert_number", 1, 99)
    return f"Expert #{n}"


def _with_label(fn, expert_number, *args, case=None):
    try:
        label = _label(expert_number)
    except research.ToolError as e:
        return _error(e.code, e.message)
    return _safe(fn, *args, label, case=case, verdicts=True)


def _in_case(fn, case_id, case, *args):
    """Run fn(newsroom_case, *args) on the research case that newsroom case was opened on.
    The memory reads that found the case come first in `cypher`."""
    mlog = []
    try:
        kase = memory.get_case(case_id, mlog)
        if case is not None and case != kase["research_case"]:
            raise research.ToolError("INVALID_ARG", f"case_id {case_id} was opened on research case "
                                                    f"'{kase['research_case']}'; leave case out or pass that one")
    except research.ToolError as e:
        return _error(e.code, e.message, mlog)
    out = _safe(fn, kase, *args, case=kase["research_case"], verdicts=True)
    out["cypher"] = mlog + out.get("cypher", [])
    return out


def _built_at():
    """built_at of the research graph in use (from its cached Meta node; no extra query once warm)."""
    return research._meta([]).get("built_at")


def _span():
    """The graph's precomputed recent span in years (5 for obesity-coke; a staged slice can be shorter)."""
    m = research._meta([])
    return m["last_year"] - m["recent_year"]


def _report(expert_id, label, public_case):
    """tie_report, plus check_no_tie when the tier is NONE (a saved "no tie" needs its query_name and scope)."""
    rep = research.tie_report(expert_id, label, public_case)
    nt = None
    if rep["ok"] and rep["data"]["tier"] == "NONE":
        # searched_hops=3 only after a tier NONE tie_report: its depth 1-3 BFS already reached no Tier-A person
        nt = research.check_no_tie(expert_id, 3, _span(), label, public_case, searched_hops=3)
        rep["cypher"] += nt["cypher"]
    return rep, nt


def _save(env, case_id, report, no_tie=None, old_id=None, diff=None, agent=None):
    """Save a Check; its write goes into env's cypher. A failed save is a warning, the answer still stands.
    A tier NONE check is saved only together with its check_no_tie result and scope."""
    if report["data"]["tier"] == "NONE" and not (no_tie and no_tie.get("ok")):
        code = no_tie["error"]["code"] if no_tie else "not run"
        env["warnings"].append(f"check not saved: tier NONE needs its check_no_tie scope, and check_no_tie failed ({code})")
        return None
    try:
        return memory.save_check(env["cypher"], case_id, report, no_tie, _built_at(), old_id, diff, agent)
    except research.ToolError as e:
        env["warnings"].append(f"check not saved ({e.code}: {e.message})")
        return None


# ---------- the tools (each docstring is what the model reads) ----------

@tool()
def resolve_experts(names: list[str], topic: str, case: str | None = None, agent: str | None = None) -> dict[str, Any]:
    """Turn names from the story into OpenAlex author IDs. Returns, per name, status MATCHED (one exact
    match), AMBIGUOUS (several, or a loose match: ask the human to pick, never guess) or NOT_FOUND, with
    candidates ranked by works on the topic. Topic is an OpenAlex topic ID (T10010) or plain words
    (for example 'obesity'). Only authors loaded into the case's research graph can be found."""
    return _safe(research.resolve_experts, names, topic, case=case)


@tool()
def find_experts(topic: str, n: int = 20, exclude_target_staff: bool = True, case: str | None = None,
                 agent: str | None = None) -> dict[str, Any]:
    """Suggest authors who work on a topic, when the journalist names no one (n from 1 to 100). Ranked by
    works on the topic; people affiliated with the company are left out unless exclude_target_staff is
    false. This is a list of active authors in the research record, not a recommendation or a vetting."""
    return _safe(research.find_experts, topic, n, exclude_target_staff, case=case)


@tool(WRITES)
def tie_report(expert_id: str, expert_number: int = 1, case: str | None = None, case_id: str | None = None,
               agent: str | None = None) -> dict[str, Any]:
    """One expert in detail: tier A1/A2 (company or foundation money or affiliation), B (repeated small-paper
    co-authorship with a Tier-A person), C (within 3 co-author hops), or NONE; a score; the evidence with
    DOIs; and a ready sentence in data.wording. Only A1/A2 rest on direct evidence (data.tier_basis); B, C and
    the score are network context only, not validated (E1/E2 failed): never call them a finding or rank by them. expert_id is an OpenAlex author ID (A5023888391).
    With case_id (from open_case) the label comes from the case and the result is saved as a Check
    (data.check_id). Without it, expert_number (1-99) only sets the display label 'Expert #n'; use the
    same number for the same expert in a story. Scope and data gaps are in the scope block."""
    if case_id is None:
        return _with_label(research.tie_report, expert_number, expert_id, case=case)
    return _in_case(_tie_report_saved, case_id, case, expert_id, agent)


def _tie_report_saved(kase, expert_id, agent):
    rep, nt = _report(expert_id, memory.label_in_case(kase, expert_id), kase["public_case"])
    if rep["ok"]:
        rep["data"]["check_id"] = _save(rep, kase["id"], rep, nt, agent=agent)
    return rep


@tool()
def check_no_tie(expert_id: str, k: int = 3, years: int = 5, expert_number: int = 1,
                 case: str | None = None, case_id: str | None = None, agent: str | None = None) -> dict[str, Any]:
    """Search for the shortest co-author path from an expert to a Tier-A person (k hops, 1 to 3). Returns
    NO_TIE_FOUND or TIE_FOUND with the query name and the scope (hops, years, funder_coverage = share of topic
    works with any funder, expert_funder_coverage = share of this expert's works with a funder, and coverage,
    which also counts affiliations). A NO_TIE_FOUND
    answer is only 'no tie found in the research record within this scope'. It is not proof of no tie, and
    the scope, with the funder-only figures, must be quoted with it. years must be the graph's recent span (5 for obesity-coke). With
    case_id the label comes from the case."""
    if case_id is None:
        return _with_label(research.check_no_tie, expert_number, expert_id, k, years, case=case)
    return _in_case(lambda kase: research.check_no_tie(expert_id, k, years, memory.label_in_case(kase, expert_id),
                                                       kase["public_case"]), case_id, case)


@tool()
def bridges(expert_id: str, depth: int = 2, limit: int = 5, expert_number: int = 1,
            case: str | None = None, case_id: str | None = None, agent: str | None = None) -> dict[str, Any]:
    """Show the strongest co-author paths (depth 2 or 3 links) from an expert to Tier-A people, each link with
    its DOIs and strength. Use it to explain a Tier B or C result. Co-authors appear as 'Co-author #i.j' and
    'Tier-A #i'. bridge_count can be null when counting took too long; the paths are still valid. With
    case_id the label comes from the case."""
    if case_id is None:
        return _with_label(research.bridges, expert_number, expert_id, depth, limit, case=case)
    return _in_case(lambda kase: research.bridges(expert_id, depth, limit, memory.label_in_case(kase, expert_id),
                                                  kase["public_case"]), case_id, case)


@tool(WRITES)
def check_case(expert_ids: list[str] | None = None, public_case: bool = False, case: str | None = None,
               case_id: str | None = None, agent: str | None = None) -> dict[str, Any]:
    """Check up to 25 experts in one run: tie_report for each, plus check_no_tie for those with tier NONE.
    Give case_id (from open_case: its experts, labels and research case are used, and each result is saved
    as a Check with a check_id) or expert_ids (nothing is saved). Labels are 'Expert #1..#n' in the order
    given. Returns results with Tier A1 then A2 first, then everyone else in input order (never ranked by
    score: Tier B/C and the score are network context only, not validated), and counts per tier. Use tie_report for the evidence
    behind one result. public_case=true (expert_ids only) is honoured only when every id is in the case's
    approved public list; otherwise it is ignored and labels stay 'Expert #n'."""
    if (expert_ids is None) == (case_id is None):
        return _error("INVALID_ARG", "give either case_id (a case from open_case) or expert_ids, not both")
    if case_id is None:
        return _safe(_check_case, expert_ids, public_case, case=case, verdicts=True)
    return _in_case(lambda kase: _check_case(kase["expert_ids"], kase["public_case"], kase, agent), case_id, case)


@tool()
def list_cases(agent: str | None = None) -> dict[str, Any]:
    """List the research cases: each case slug, whether its graph is loaded, its build status, data_version,
    built_at, topic and funder ids, and node and edge counts. Use it to pick the `case` argument of the
    other tools, or to see that a company or topic has no case yet (then a human must approve and build one)."""
    try:
        with _lock:
            return research.list_cases()
    except Exception as e:
        log.exception("tool failed")
        return _error("INTERNAL", f"unexpected {type(e).__name__}; see the server log")


# ---------- Track 2: memory tools (newsroom graph) ----------

@tool(WRITES)
def open_case(story: str, company: str, topic: str, expert_ids: list[str], public_case: bool = False,
              case: str | None = None, agent: str | None = None) -> dict[str, Any]:
    """Open a newsroom case for a story before checking its experts. Stores the story title, company, topic
    and the experts (1-25 OpenAlex author ids, labelled 'Expert #1..#n' in this order) and returns case_id.
    data.prior_memory lists earlier checks of these experts in other cases of this newsroom (who checked,
    when, which tier): mention them to the reporter. Then run check_case(case_id). public_case=true names
    the experts only when every id is in the research case's approved public list; otherwise it is ignored."""
    mlog = []
    try:
        if not isinstance(public_case, bool):
            raise research.ToolError("INVALID_ARG", "public_case must be true or false")
        cfg = _case_config(case)
        warnings = []
        memory.expert_ids_arg(expert_ids)
        if public_case and not set(expert_ids) <= set(cfg["public_ids"]):
            public_case = False
            warnings.append("public_case ignored: not every expert is in the documented public case; labels stay 'Expert #n'")
        data = memory.open_case(mlog, story, company, topic, expert_ids, public_case, cfg["slug"])
    except research.ToolError as e:
        return _error(e.code, e.message, mlog)
    return _ok(data, mlog, {"graph": memory.GRAPH, "research_case": cfg["slug"]}, warnings)


@tool()
def recall(expert_id: str | None = None, case_id: str | None = None, session_id: str | None = None,
           limit: int = 20, agent: str | None = None) -> dict[str, Any]:
    """This newsroom's memory as a timeline, oldest first (the newest `limit` items, 1-100). Give exactly
    one of: expert_id (every saved check of that expert, in any case), case_id (the case's checks and tool
    calls) or session_id (one server session's checks and tool calls). Each item has ts, kind (check or
    event), summary and ref_id (a check_id can be passed to recheck). data.session_id is the current session."""
    mlog = []
    try:
        data = memory.recall(mlog, expert_id, case_id, session_id, limit)
    except research.ToolError as e:
        return _error(e.code, e.message, mlog)
    return _ok(data, mlog, {"graph": memory.GRAPH})


@tool(WRITES)
def recheck(check_id: str, agent: str | None = None) -> dict[str, Any]:
    """Run a saved check again on the current research graph and show what changed: data.before and data.after
    (tier, score, nearest, data_version, built_at), data.diff (new_evidence, removed_evidence with DOIs,
    rebuilt, changed, and cause: new_data when the result changed after a rebuild, else null), and the new
    check_id. The new Check SUPERSEDES the old one (kept). Rechecking a check that was already replaced
    rechecks its newest replacement."""
    mlog = []
    try:
        old = memory.get_check(check_id, mlog)
        kase = memory.get_case(old["case_id"], mlog)
    except research.ToolError as e:
        return _error(e.code, e.message, mlog)
    out = _safe(_recheck, old, kase, agent, case=kase["research_case"], verdicts=True)
    out["cypher"] = mlog + out.get("cypher", [])
    return out


def _recheck(old, kase, agent):
    asked, mlog = old["id"], []
    if old["superseded_by"]:  # only the head of a chain is rechecked, so there is one current Check
        head = memory.head_of(asked, mlog)
        if head is None:  # a chain longer than 100 links, or a broken one
            return _error("NOT_FOUND", f"{asked} was superseded, but the newest check of its chain was not found "
                                       "within 100 links; recheck a newer check_id (see recall)", mlog)
        old = memory.get_check(head, mlog)
    rep, nt = _report(old["expert_id"], old["label"], kase["public_case"])
    rep["cypher"] = mlog + rep["cypher"]
    if not rep["ok"]:
        return rep
    before, after, diff = _compare(old, rep, nt)
    warnings = rep["warnings"]
    if old["id"] != asked:
        warnings.append(f"{asked} was already superseded; rechecked its newest replacement {old['id']}")
    if diff["changed"] and not diff["cause"]:
        warnings.append("the result changed although the research graph was not rebuilt and no verdict changed "
                        "(a server code change?)")
    env = _ok({"label": old["label"], "case_id": kase["id"], "before": before, "after": after, "diff": diff},
              rep["cypher"], {**rep["scope"], "memory_graph": memory.GRAPH}, warnings, rep["verdicts_applied"])
    env["data"]["check_id"] = _save(env, kase["id"], rep, nt, old_id=old["id"], diff=diff, agent=agent)
    if env["data"]["check_id"] is None:  # another recheck superseded old first: point at its Check
        env["data"]["check_id"] = memory.head_of(old["id"], env["cypher"])
    return env


def _compare(old, rep, nt):
    """A saved Check (old) against a fresh run (rep, nt) on the current graph: (before, after, diff).
    cause: "new_data" when the result changed and the graph was rebuilt, else "verdict" when it changed and
    the verdicts in force differ from the ones the old Check was computed with, else null."""
    before_snap, after_snap = json.loads(old["snapshot_json"]), memory.snapshot(rep, nt)
    new_ev, removed_ev = memory.evidence_diff(before_snap, after_snap)
    built_at = _built_at()
    rebuilt = (old["data_version"], old["built_at"]) != (rep["scope"]["data_version"], built_at)
    verdicts_changed = sorted(old["verdicts"]) != sorted(rep["verdicts_applied"])
    d = rep["data"]
    before = {"check_id": old["id"], "tier": old["tier"], "score": old["score"], "nearest": before_snap["nearest"],
              "data_version": old["data_version"], "built_at": old["built_at"], "ts": old["ts"], "no_tie": old["no_tie"],
              "verdicts": old["verdicts"]}
    after = {"tier": d["tier"], "score": d["score"], "nearest": d["nearest"], "data_version": rep["scope"]["data_version"],
             "built_at": built_at, "no_tie": (after_snap["no_tie"] or {}).get("result"),
             "verdicts": rep["verdicts_applied"]}
    changed = (bool(new_ev or removed_ev) or (before["tier"], before["score"], before["nearest"], before["no_tie"])
               != (after["tier"], after["score"], after["nearest"], after["no_tie"]))
    cause = ("new_data" if rebuilt else "verdict" if verdicts_changed else None) if changed else None
    diff = {"new_evidence": new_ev, "removed_evidence": removed_ev, "cause": cause, "rebuilt": rebuilt,
            "changed": changed, "tier_changed": before["tier"] != after["tier"],
            "score_change": round(after["score"] - before["score"], 4), "score_basis": research.SCORE_BASIS}
    return before, after, diff


# ---------- checkpoint 7: verdicts and sourcing notes ----------

@tool(HUMAN_VERDICT)
def record_verdict(type: str, target: str | list[str], reason: str, reviewer: str, case: str | None = None,
                   agent: str | None = None) -> dict[str, Any]:
    """HUMAN ONLY: a reporter or reviewer must approve this call; never call it on your own judgment. Records a
    ruling in this newsroom's memory: PAPER_EXCLUDED (target = one DOI) leaves that paper out of all tie evidence
    from now on; PROFILE_MERGE (target = two OpenAlex author ids) treats the two profiles as one person. The
    target must exist in the research case's graph (case, default the server's case). Every current saved check
    in the newsroom is run again with the verdict: data.impact lists each check whose tier or score changed
    (before and after; those checks are superseded by new ones). Reason and reviewer are required."""
    try:
        reason, reviewer = memory._text(reason, "reason", 1000), memory._text(reviewer, "reviewer", 100)
        doi, ids, key = memory.verdict_target(type, target)
    except research.ToolError as e:
        return _error(e.code, e.message)
    return _safe(_record_verdict, type, doi, ids, key, reason, reviewer, agent, case=case, verdicts=True)


def _record_verdict(vtype, doi, ids, key, reason, reviewer, agent):
    """Runs under the lock on the case graph, with the verdicts already in force. Read first, write last."""
    log, warnings = [], []
    # Checked first (before re-running every check) so a duplicate costs one read; SAVE_VERDICT_Q refuses a
    # duplicate again at write time, which also covers another server process.
    if memory.same_verdict(vtype, key, log):
        return _error("INVALID_ARG", f"this {vtype} verdict on {key} is already recorded", log)
    if doi and not research.works_with_doi(log, doi):
        return _error("NOT_FOUND", f"no work with DOI {doi} in {research.current_graph()}", log)
    if ids and len(research.names_of(log, ids)) != 2:
        return _error("NOT_FOUND", f"not both of {key} are authors in {research.current_graph()}", log)
    heads = memory.current_checks(log)  # under the lock, so no other call of this process changes them meanwhile
    slug, vid, old = research.current_graph().split(":", 1)[1], memory.new_id("vrd"), research._verdicts.get()
    new = {"ids": (old["ids"] if old else []) + [vid], "dois": (old["dois"] if old else []) + ([doi] if doi else []),
           "merges": (old["merges"] if old else []) + ([ids] if ids else [])}
    changed, not_rechecked, start = [], [], time.time()
    for h in heads:  # every current Check of the newsroom, on its own research graph
        if time.time() - start > BATCH_BUDGET_S:
            not_rechecked.append(h["id"])
            continue
        hslug = h["research_case"]
        research.drop_cache_if_rebuilt(hslug)
        with research.on_graph(f"research:{hslug}"), research.with_verdicts(new):
            rep, nt = _report(h["expert_id"], h["label"], h["public_case"])
            if not rep["ok"]:
                warnings.append(f"{h['id']}: not run again ({rep['error']['code']})")
                continue
            before, after, diff = _compare(h, rep, nt)
        if diff["changed"]:
            changed.append((h, rep, nt, before, after, diff))
    experts = sorted(set(ids) | {h["expert_id"] for h, *_ in changed})
    if not memory.save_verdict(log, vid, vtype, key, doi, ids, reason, reviewer, experts, len(changed), slug, agent):
        return _error("INVALID_ARG", f"this {vtype} verdict on {key} was just recorded by another session", log)
    details = []
    for h, rep, nt, before, after, diff in changed:  # each changed Check is superseded, and AFFECTS records it
        with research.on_graph(f"research:{h['research_case']}"), research.with_verdicts(new):
            target, new_check = _supersede(log, h, rep, nt, diff, agent)
            if target is not h:  # another call replaced the check meanwhile: compare with that one
                before, after, diff = _compare(target, rep, nt)
        if target is None:
            warnings.append(f"{h['id']}: replaced meanwhile and its newest check was not found; recheck it")
            continue
        memory.save_affects(log, vid, target["id"], before, after, new_check)
        if diff["rebuilt"]:
            warnings.append(f"{target['id']}: the research graph was rebuilt since this check, so its change is "
                            "not only the verdict's")
        details.append({"label": h["label"], "expert_id": h["expert_id"], "case_id": h["case_id"],
                        "check_id": target["id"], "new_check_id": new_check, "tier_before": before["tier"],
                        "tier_after": after["tier"], "score_before": before["score"], "score_after": after["score"],
                        "rebuilt": diff["rebuilt"]})
    if not_rechecked:
        warnings.append(f"time budget of {BATCH_BUDGET_S} s reached: {len(not_rechecked)} check(s) not run again "
                        "(recheck them later; the verdict applies to them anyway)")
    impact = {"experts_changed": len({d["expert_id"] for d in details}), "checks_changed": len(details),
              "checks_rechecked": len(heads) - len(not_rechecked), "not_rechecked": not_rechecked,
              "details": details, "score_basis": research.SCORE_BASIS,
              "recomputed": VERDICT_RECOMPUTED}
    return _ok({"verdict_id": vid, "type": vtype, "target": key, "impact": impact}, log,
               {"graph": memory.GRAPH, "research_case": slug}, warnings, new["ids"])


def _supersede(log, old, rep, nt, diff, agent):
    """Save rep as the Check that SUPERSEDES old. If old was replaced meanwhile (another session, or a recheck),
    supersede the newest Check of its chain instead. Returns (the Check superseded, new check id), or (None, None)."""
    for _ in range(5):
        try:
            return old, memory.save_check(log, old["case_id"], rep, nt, _built_at(), old["id"], diff, agent)
        except research.ToolError:
            head = memory.head_of(old["id"], log)
            if head is None:
                return None, None
            old = {**memory.get_check(head, log)}
            diff = _compare(old, rep, nt)[2]
    return None, None


VERDICT_RECOMPUTED = ("Recomputed with the verdict: Tier A evidence and the Tier-A set, Tier B links and the score "
                      "(the paper's share is taken out of each co-author link), the expert's funder share, bridges "
                      "paths. Not recomputed: algo.BFS still walks co-author links that rest only on an excluded "
                      "paper (Tier C and check_no_tie; a warning says so), graph-wide coverage figures, find_experts.")


@tool(WRITES)
def draft_note(case_id: str, claims: list[dict[str, Any]], agent: str | None = None) -> dict[str, Any]:
    """The Writer's tool: submit the sourcing-note claims for a case; the Verifier checks every claim against a
    fresh run on the research graph (with this newsroom's verdicts). Each claim is {expert_id, text,
    evidence_refs}. A tie claim cites DOIs that are Tier A evidence of that expert (Tier B co-author papers are
    context only and are not accepted). A no-tie claim cites only the query name q3_bfs_v1, says 'no tie found',
    and is rejected unless check_no_tie finds no tie; the server adds the scope itself. Texts use the expert's
    label, never a name (unless the case is public). Every expert of the case gets a disclosure line in the note,
    with or without a claim. Returns note_id and status verified or rejected, with rejections[{claim_index,
    reason}]: fix them and call again."""
    mlog = []
    try:
        kase = memory.get_case(case_id, mlog)
        memory.claims_arg(claims, kase)
    except research.ToolError as e:
        return _error(e.code, e.message, mlog)
    out = _safe(_draft_note, kase, claims, agent, case=kase["research_case"], verdicts=True)
    out["cypher"] = mlog + out.get("cypher", [])
    return out


def _draft_note(kase, claims, agent):
    log, facts = [], {}
    for eid in kase["expert_ids"]:  # one fresh run per expert of the case: every one gets a disclosure line
        rep, nt = _report(eid, memory.label_in_case(kase, eid), kase["public_case"])
        log += rep["cypher"]
        facts[eid] = (rep, nt)
    # Names never shown: the case's experts, unless the case is public (then their names are the labels).
    names = [] if kase["public_case"] else list(research.names_of(log, kase["expert_ids"]).values())
    checked, stored, failed = [], {}, []
    for i, c in enumerate(claims):
        rep, nt = facts[c["expert_id"]]
        if not rep["ok"]:
            failed.append({"claim_index": i, "reason": f"tie_report failed for this expert ({rep['error']['code']}); "
                                                       "no claim can be checked", "code": "VERIFIER_REJECT"})
        d = rep.get("data") or {"label": memory.label_in_case(kase, c["expert_id"]), "evidence": [], "tier": None}
        stored[c["expert_id"]] = [e["doi"] for e in d["evidence"] if e["doi"]]  # Tier A evidence only
        vc = {"expert_id": c["expert_id"], "text": c["text"], "label": d["label"], "forbidden_names": names,
              "evidence_refs": c["evidence_refs"]}
        query = next((r for r in c["evidence_refs"] if r in verifier.KNOWN_QUERIES), None)
        if query:
            ok_nt = nt is not None and nt["ok"]
            result = (nt["data"]["result"] if ok_nt else f"ERROR {nt['error']['code']}" if nt
                      else f"TIE_FOUND, tier {d['tier']}" if rep["ok"] else f"ERROR {rep['error']['code']}")
            vc.update(kind="no_tie", query_name=query, result=result,
                      scope={k: nt["scope"].get(k) for k in ("hops", "years", "coverage", "funder_coverage",
                                                             "expert_funder_coverage", "expert_works")} if ok_nt else None)
        else:
            vc.update(kind="tie", result=None)  # result marks a draft_note claim: its text is checked too
        checked.append(vc)
    result = verifier.verify_claims(checked, stored)
    rejections = sorted(result["rejections"] + failed, key=lambda r: r["claim_index"])
    status = "rejected" if rejections else "verified"
    stored_claims = []
    for vc in checked:
        evidence = (facts[vc["expert_id"]][0].get("data") or {}).get("evidence", [])
        # one DOI written three ways is one work (review round 3)
        dois = list(dict.fromkeys(verifier.norm_doi(r) for r in vc["evidence_refs"])) if vc["kind"] == "tie" else []
        sources = [{"doi": doi, "evidence": [{k: e[k] for k in ("kind", "via", "tier", "topic_label", "year")}
                                             for e in evidence if e["doi"] == doi]} for doi in dois]
        text = _claim_sentence(vc["kind"], vc["label"], sources)  # the note never prints the Writer's wording
        stored_claims.append({"expert_id": vc["expert_id"], "label": vc["label"], "text": text,
                              "writer_text_replaced": text != vc["text"], "kind": vc["kind"], "sources": sources,
                              "query_name": vc.get("query_name"), "scope": vc.get("scope")})
    experts = [{"expert_id": eid, "label": memory.label_in_case(kase, eid) if not facts[eid][0]["ok"]
                else facts[eid][0]["data"]["label"], "disclosure": _disclosure(*facts[eid], kase, eid)}
               for eid in kase["expert_ids"]]
    # The case's story, company and topic go into the note only if they name no expert and use no banned word.
    header = {k: (kase[k] if not verifier.text_problems(kase[k], names) else None) for k in ("story", "company", "topic")}
    good = next((r for r, _ in facts.values() if r["ok"]), None)
    scope = good["scope"] if good else {"graph": research.current_graph(), "data_version": None}
    note_id = memory.save_note(log, kase["id"], status, stored_claims, rejections, research.applied(),
                               scope["graph"], scope.get("data_version"), _built_at() if good else None,
                               header, experts, agent)
    warnings = [] if status == "verified" else [f"{len(rejections)} rejection(s): fix the claims and "
                                                "call draft_note again; a rejected note cannot be approved"]
    return _ok({"note_id": note_id, "case_id": kase["id"], "status": status, "claims_checked": len(claims),
                "rejections": [{"claim_index": r["claim_index"], "reason": r["reason"], "code": r["code"]}
                               for r in rejections]},
               log, {**scope, "memory_graph": memory.GRAPH}, warnings, research.applied())


def _claim_sentence(kind, label, sources):
    """The sentence the note prints for a claim, written by the server from what the Verifier checked (review
    round 2: a free text can state a tie, or deny one, in words no filter catches). The Writer chooses which
    claims and DOIs go in; its text is only checked, never published."""
    if kind == "no_tie":
        return f"No tie found in the research record for {label} within the stated scope; absence is not proof."
    phrases = {("funded by " if e["kind"] == "FUNDED_BY" else "listing an affiliation with ") + e["via"]
               for x in sources for e in x["evidence"]}
    return (f"Ties found in the research record: OpenAlex records {label} as author of {len(sources)} cited "
            f"work(s): {'; '.join(sorted(phrases))}.")


def _disclosure(rep, nt, kase, eid):
    """The disclosure line of one expert in the approved note, from the draft-time run (never the banned words)."""
    if not rep["ok"]:
        return f"could not be checked ({rep['error']['code']}); nothing is said about this expert."
    d = rep["data"]
    snap = memory.snapshot(rep, nt)
    if d["tier"] in ("A1", "A2"):
        return f"ties found in the research record: tier {memory.tier_summary(d['tier'], snap)}."
    if d["tier"] in ("B", "C"):
        return f"tier {memory.tier_summary(d['tier'], snap)}; no claim is made about it."
    if not (nt and nt["ok"]):
        return f"check_no_tie failed ({nt['error']['code'] if nt else 'not run'}); nothing is said about this expert."
    s = nt["scope"]
    return (f"no tie found in the research record within the stated scope ({nt['data']['query_name']}, "
            f"{s['hops']} co-author hops, {s['years']}; {round(100 * s['funder_coverage'])}% of topic works list a "
            "funder); absence is not proof.")


@tool(HUMAN_APPROVE)
def approve_note(note_id: str, reviewer: str, agent: str | None = None) -> dict[str, Any]:
    """HUMAN ONLY: a reporter or reviewer must approve this call; never call it on your own judgment. Approves a
    sourcing note that draft_note marked verified and returns it as markdown (data.markdown): one section per
    expert of the case with a disclosure line, every DOI as a https://doi.org link with what it is evidence of,
    and every no-tie claim with its scope. Fails with NOT_APPROVED if the note is not verified, or if a verdict
    was recorded or the research graph was rebuilt after it was drafted (draft it again). Approving an approved
    note again returns the same markdown and changes nothing."""
    mlog = []
    try:
        reviewer = memory._text(reviewer, "reviewer", 100)
        note = memory.get_note(note_id, mlog)
        warnings = []
        if not (note["header_json"] and note["experts_json"]):  # drafted by code before review round 1
            raise research.ToolError("NOT_APPROVED", f"note {note_id} was drafted before notes listed every expert; "
                                                     "run draft_note again")
        if note["status"] != "approved" and _reviewer_problems(note, reviewer):  # it is printed in the note
            raise research.ToolError("INVALID_ARG", "reviewer must be a person's name or role, not an expert of the "
                                                    "case and without banned words")
        if note["status"] == "approved":
            warnings.append(f"already approved by {note['approved_by']}; nothing changed")
        else:
            if note["status"] != "verified":
                raise research.ToolError("NOT_APPROVED", f"note {note_id} is {note['status']}; only a verified "
                                                         "note can be approved (fix the claims with draft_note)")
            now_in_force = (memory.active_verdicts(mlog) or {"ids": []})["ids"]
            if sorted(now_in_force) != sorted(note["verdicts"]):
                raise research.ToolError("NOT_APPROVED", "a verdict was recorded after this note was drafted; "
                                                         "run draft_note again so the Verifier sees it")
            if _build_of(note["graph"]) != (note["data_version"], note["built_at"]):
                raise research.ToolError("NOT_APPROVED", f"{note['graph']} was rebuilt after this note was drafted; "
                                                         "run draft_note again on the new data")
            approved_at = memory.approve(note_id, reviewer, mlog)
            if approved_at is None:  # approved by someone else a moment ago
                note = memory.get_note(note_id, mlog)
                warnings.append(f"already approved by {note['approved_by']}; nothing changed")
            else:
                note.update(status="approved", approved_by=reviewer, approved_at=approved_at)
    except research.ToolError as e:
        return _error(e.code, e.message, mlog)
    md = memory.note_markdown(note, note["approved_by"], note["approved_at"])
    return _ok({"note_id": note_id, "case_id": note["case_id"], "status": "approved", "approved_by": note["approved_by"],
                "approved_at": note["approved_at"], "markdown": md}, mlog,
               {"graph": memory.GRAPH, "research_graph": note["graph"], "data_version": note["data_version"]},
               warnings, note["verdicts"])


def _reviewer_problems(note, reviewer):
    """The reviewer string goes into the approved note, so it gets the same banned-word and name check as a claim."""
    kase = memory.get_case(note["case_id"], [])
    names = []
    if not kase["public_case"]:
        with _lock, research.on_graph(note["graph"]):
            names = list(research.names_of([], kase["expert_ids"]).values())
    return verifier.text_problems(reviewer, names)


def _build_of(graph):
    """(data_version, built_at) of a research graph as it is now (its cached Meta node)."""
    slug = graph.split(":", 1)[1]
    with _lock:
        research.drop_cache_if_rebuilt(slug)
        with research.on_graph(graph):
            m = research._meta([])
    return m.get("data_version"), m.get("built_at")


# ---------- check_case: a loop over tie_report and check_no_tie ----------

TIER_ORDER = {"A1": 0, "A2": 1, "B": 2, "C": 3, "NONE": 4}  # the keys of data.counts
# Decided by Aaryan, Oct 16 (E1 and E2 failed): rows are NOT ranked by score or by Tier B/C. Tier A (direct
# evidence) comes first, then every other checked expert, then failed rows; input order inside each group.
RANK = {"A1": 0, "A2": 1, "B": 2, "C": 2, "NONE": 2}
ORDER_NOTE = ("Tier A1, then A2, then every other checked expert (B, C, NONE mixed), then failed rows; "
              "input order inside each group. Not ranked by score: Tier B/C and the score are context only (E1/E2).")


def _check_case(expert_ids, public_case, kase=None, agent=None):
    """kase=None: a plain run over expert_ids. With a newsroom case: its experts, and a Check saved per row."""
    if (not isinstance(expert_ids, list) or not expert_ids or not all(isinstance(e, str) and e.strip() for e in expert_ids)
            or len(set(expert_ids)) != len(expert_ids)):
        return _error("INVALID_ARG", "expert_ids must be a non-empty list of distinct non-empty strings")
    if len(expert_ids) > MAX_BATCH:
        return _error("INVALID_ARG", f"at most {MAX_BATCH} experts per call; split the list")
    if not isinstance(public_case, bool):
        return _error("INVALID_ARG", "public_case must be true or false")

    start, by_query, warnings, scope = time.time(), {}, [], {}
    public_ids = set(loader.load_config(research.current_graph().split(":", 1)[1])["public_ids"])
    if public_case and not set(expert_ids) <= public_ids:
        public_case = False
        warnings.append("public_case ignored: not every expert is in the documented public case; labels stay 'Expert #n'")
    results, skipped = [], []

    def merge(env):  # one Cypher entry per query text, with the params of every run; collect warnings and scope
        for c in env.get("cypher", []):
            e = by_query.setdefault(c["query"], {"graph": c["graph"], "query": c["query"], "params": c["params"],
                                                 "runs": 0, "other_params": []})
            e["runs"] += 1
            if e["runs"] > 1 and c["params"] != e["params"] and c["params"] not in e["other_params"]:
                e["other_params"].append(c["params"])
        for w in env.get("warnings", []):
            if w not in warnings:
                warnings.append(w)
        scope.update(env.get("scope", {}))

    for i, eid in enumerate(expert_ids, 1):
        if time.time() - start > BATCH_BUDGET_S:
            skipped.append(eid)
            continue
        label = f"Expert #{i}"
        rep = research.tie_report(eid, label, public_case)
        merge(rep)
        if not rep["ok"]:
            results.append({"label": label, "expert_id": eid, "tier": None, "tier_basis": None, "score": None,
                            "check_id": None, "no_tie": None, "error": rep["error"]})
            continue
        d = rep["data"]
        no_tie, nt = None, None
        if d["tier"] == "NONE":  # searched_hops=3: this tier NONE tie_report already ran the depth 1-3 BFS
            nt = research.check_no_tie(eid, 3, _span(), label, public_case, searched_hops=3)
            merge(nt)
            no_tie = nt["data"]["result"] if nt["ok"] else None
            if not nt["ok"]:
                warnings.append(f"{label}: check_no_tie failed ({nt['error']['code']})")
        check_id = None
        if kase:
            saved = {"cypher": [], "warnings": []}
            check_id = _save(saved, kase["id"], rep, nt, agent=agent)
            merge(saved)
        results.append({"label": d["label"], "expert_id": eid, "tier": d["tier"], "tier_basis": d["tier_basis"],
                        "score": d["score"], "check_id": check_id, "no_tie": no_tie, "error": None})

    codes = {r["error"]["code"] if r["error"] else None for r in results}
    if len(codes) == 1 and codes <= {"DB_ERROR", "NO_GRAPH"}:  # every row failed the same way: the graph is the problem
        return _error(codes.pop(), results[0]["error"]["message"], list(by_query.values()))
    if skipped:
        warnings.append(f"time budget of {BATCH_BUDGET_S} s reached: {len(skipped)} expert(s) not checked")
    results.sort(key=lambda r: RANK.get(r["tier"], 3))  # stable: input order inside each group
    counts = {t: sum(r["tier"] == t for r in results) for t in TIER_ORDER}
    data = {"results": results, "counts": counts, "not_checked": skipped, "order": ORDER_NOTE,
            "score_basis": research.SCORE_BASIS}
    if kase:
        data["case_id"] = kase["id"]
        scope["memory_graph"] = memory.GRAPH
    return {"ok": True, "data": data, "cypher": list(by_query.values()), "scope": scope,
            "verdicts_applied": research.applied(), "warnings": warnings}


def warm():
    """Load the shared facts once, so the first real call is fast. A failure here is only logged."""
    try:
        memory.init()  # newsroom indexes (creates the newsroom graph on first start)
    except Exception as e:
        log.warning("memory indexes not created (%s); memory tools will report DB_ERROR until the database is up",
                    type(e).__name__)
    try:
        t = time.time()
        scratch = []
        slug = _case_config(None)["slug"]
        research.drop_cache_if_rebuilt(slug)  # remembers this build, so a later swap is noticed
        with research.on_graph(f"research:{slug}"):
            research._meta(scratch), research._tier_a(scratch), research._staff(scratch)
        log.info("caches warm in %.1f s", time.time() - t)
    except Exception as e:
        log.warning("warm-up failed (%s); tools will report DB_ERROR until the database is up", type(e).__name__)


if __name__ == "__main__":
    log.info("starting, newsroom=%s, session=%s, case=%s, FalkorDB port=%s", NEWSROOM, memory.SESSION_ID,
             research.default_case(), os.environ.get("FALKORDB_PORT", "6379"))
    warm()
    mcp.run("stdio")
