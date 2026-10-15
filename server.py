"""Checkpoint 5: the TieTrace MCP server (stdio), Track 1 tools. See docs/mcp-spec.md sections 4, 5 and 9.

Tools: resolve_experts, find_experts, tie_report, check_no_tie, bridges, check_case.
All of them call research.py (reads only, through GRAPH.RO_QUERY) and return the section-4 envelope.
No LLM call, no API key.

Newsroom: TIETRACE_NEWSROOM (env, required, [a-z0-9_-]+) is read at startup and logged on stderr.
Nothing is written to the newsroom graph yet (memory is checkpoint 6), so it is only held here.

Names: tools never take a name or a label. Single-expert tools take `expert_number` and say
"Expert #<n>". Only check_case(public_case=true) puts a name in the label (the documented GEBN case).

Stdout belongs to the MCP protocol. Everything we print goes to stderr.
Run: FALKORDB_PORT=6380 TIETRACE_NEWSROOM=alpha uv run python server.py
"""
import logging
import os
import re
import sys
import threading
import time
from typing import Any

NEWSROOM = os.environ.get("TIETRACE_NEWSROOM", "")
if not re.fullmatch(r"[a-z0-9_-]+", NEWSROOM):
    sys.exit("TieTrace: set TIETRACE_NEWSROOM to the newsroom id, lower case letters, digits, - or _ (for example alpha)")

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

import research

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="tietrace %(levelname)s %(message)s")
log = logging.getLogger("tietrace")

MAX_BATCH = 25         # experts per check_case call
BATCH_BUDGET_S = 90    # check_case stops starting new experts after this many seconds

RULES = (" Say 'ties found in the research record'; absence of a tie is not proof; every tie needs its DOI. "
         "Experts are 'Expert #n'; do not guess or add names.")

# The only experts who may be named: the documented public GEBN case (eval/results.md E3: Hill, Blair, Hand,
# Peters). public_case is honoured only when EVERY id in a check_case call is in this set.
GEBN_PUBLIC_IDS = {"A5031318756", "A5105003630", "A5046953968", "A5045252457"}
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

mcp = MCPServer("tietrace", instructions="TieTrace checks how connected an expert is to the company in a story, "
                "using an OpenAlex research graph (read-only)." + RULES)
def tool(fn):
    """Register fn as a read-only tool; its docstring plus the wording rules is the description."""
    return mcp.tool(structured_output=True, description=" ".join(fn.__doc__.split()) + RULES, annotations=READ_ONLY)(fn)


_lock = threading.Lock()  # research.py keeps one DB connection and one cache per process: one call at a time


def _error(code, message, cypher=None):
    return {"ok": False, "error": {"code": code, "message": message}, "cypher": cypher or []}


def _safe(fn, *args):
    """Run a research function; whatever happens, return an envelope (no traceback, no paths)."""
    try:
        with _lock:
            return fn(*args)
    except Exception as e:  # last resort: research.py already turns its own errors into envelopes
        log.exception("tool failed")
        return _error("INTERNAL", f"unexpected {type(e).__name__}; see the server log")


def _label(expert_number):
    n = research._int(expert_number, "expert_number", 1, 99)
    return f"Expert #{n}"


def _with_label(fn, expert_number, *args):
    try:
        label = _label(expert_number)
    except research.ToolError as e:
        return _error(e.code, e.message)
    return _safe(fn, *args, label)


# ---------- the tools (each docstring is what the model reads) ----------

@tool
def resolve_experts(names: list[str], topic: str) -> dict[str, Any]:
    """Turn names from the story into OpenAlex author IDs. Returns, per name, status MATCHED (one exact
    match), AMBIGUOUS (several, or a loose match: ask the human to pick, never guess) or NOT_FOUND, with
    candidates ranked by works on the topic. Topic is an OpenAlex topic ID (T10010) or plain words
    (for example 'obesity'). Only authors of the loaded obesity research graph can be found."""
    return _safe(research.resolve_experts, names, topic)


@tool
def find_experts(topic: str, n: int = 20, exclude_target_staff: bool = True) -> dict[str, Any]:
    """Suggest authors who work on a topic, when the journalist names no one (n from 1 to 100). Ranked by
    works on the topic; people affiliated with the company are left out unless exclude_target_staff is
    false. This is a list of active authors in the research record, not a recommendation or a vetting."""
    return _safe(research.find_experts, topic, n, exclude_target_staff)


@tool
def tie_report(expert_id: str, expert_number: int = 1) -> dict[str, Any]:
    """One expert in detail: tier A1/A2 (company or foundation money or affiliation), B (repeated small-paper
    co-authorship with a Tier-A person), C (within 3 co-author hops), or NONE; a score; the evidence with
    DOIs; and a ready sentence in data.wording. expert_id is an OpenAlex author ID (A5023888391).
    expert_number (1-99) only sets the display label 'Expert #n'; use the same number for the same
    expert in a story. Scope and data gaps are in the scope block."""

    return _with_label(research.tie_report, expert_number, expert_id)


@tool
def check_no_tie(expert_id: str, k: int = 3, years: int = 5, expert_number: int = 1) -> dict[str, Any]:
    """Search for the shortest co-author path from an expert to a Tier-A person (k hops, 1 to 3). Returns
    NO_TIE_FOUND or TIE_FOUND with the query name and the scope (hops, years, coverage). A NO_TIE_FOUND
    answer is only 'no tie found in the research record within this scope'. It is not proof of no tie, and
    the scope must be quoted with it. years must be 5 (the graph is precomputed for the last 5 years)."""

    return _with_label(research.check_no_tie, expert_number, expert_id, k, years)


@tool
def bridges(expert_id: str, depth: int = 2, limit: int = 5, expert_number: int = 1) -> dict[str, Any]:
    """Show the strongest co-author paths (depth 2 or 3 links) from an expert to Tier-A people, each link with
    its DOIs and strength. Use it to explain a Tier B or C result. Co-authors appear as 'Co-author #i.j' and
    'Tier-A #i'. bridge_count can be null when counting took too long; the paths are still valid."""

    return _with_label(research.bridges, expert_number, expert_id, depth, limit)


@tool
def check_case(expert_ids: list[str], public_case: bool = False) -> dict[str, Any]:
    """Check up to 25 experts in one run: tie_report for each, plus check_no_tie for those with tier NONE.
    Labels are 'Expert #1..#n' in the order given. Returns results ranked by tier then score, and counts per
    tier. Rows with the same tier and score keep the order given. Use tie_report for the evidence behind one
    result. public_case=true is honoured only when every id is a documented public GEBN expert; otherwise
    it is ignored and labels stay 'Expert #n'."""

    return _safe(_check_case, expert_ids, public_case)


# ---------- check_case: a loop over tie_report and check_no_tie ----------

TIER_ORDER = {"A1": 0, "A2": 1, "B": 2, "C": 3, "NONE": 4}


def _check_case(expert_ids, public_case):
    if (not isinstance(expert_ids, list) or not expert_ids or not all(isinstance(e, str) and e.strip() for e in expert_ids)
            or len(set(expert_ids)) != len(expert_ids)):
        return _error("INVALID_ARG", "expert_ids must be a non-empty list of distinct non-empty strings")
    if len(expert_ids) > MAX_BATCH:
        return _error("INVALID_ARG", f"at most {MAX_BATCH} experts per call; split the list")
    if not isinstance(public_case, bool):
        return _error("INVALID_ARG", "public_case must be true or false")

    start, by_query, warnings, scope = time.time(), {}, [], {}
    if public_case and not set(expert_ids) <= GEBN_PUBLIC_IDS:
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
            results.append({"label": label, "expert_id": eid, "tier": None, "score": None, "check_id": None,
                            "no_tie": None, "error": rep["error"]})
            continue
        d = rep["data"]
        no_tie = None
        if d["tier"] == "NONE":
            nt = research.check_no_tie(eid, label=label, public_case=public_case)
            merge(nt)
            no_tie = nt["data"]["result"] if nt["ok"] else None
            if not nt["ok"]:
                warnings.append(f"{label}: check_no_tie failed ({nt['error']['code']})")
        results.append({"label": d["label"], "expert_id": eid, "tier": d["tier"], "score": d["score"],
                        "check_id": None, "no_tie": no_tie, "error": None})

    if results and all(r["error"] and r["error"]["code"] == "DB_ERROR" for r in results):
        return _error("DB_ERROR", results[0]["error"]["message"], list(by_query.values()))
    if skipped:
        warnings.append(f"time budget of {BATCH_BUDGET_S} s reached: {len(skipped)} expert(s) not checked")
    # ranked by tier, then score high to low; failed rows last
    results.sort(key=lambda r: (TIER_ORDER.get(r["tier"], 9), -(r["score"] or 0)))
    counts = {t: sum(r["tier"] == t for r in results) for t in TIER_ORDER}
    data = {"results": results, "counts": counts, "not_checked": skipped}
    return {"ok": True, "data": data, "cypher": list(by_query.values()), "scope": scope, "verdicts_applied": [], "warnings": warnings}


def warm():
    """Load the shared facts once, so the first real call is fast. A failure here is only logged."""
    try:
        t = time.time()
        scratch = []
        research._meta(scratch), research._tier_a(scratch), research._staff(scratch)
        log.info("caches warm in %.1f s", time.time() - t)
    except Exception as e:
        log.warning("warm-up failed (%s); tools will report DB_ERROR until the database is up", type(e).__name__)


if __name__ == "__main__":
    log.info("starting, newsroom=%s, FalkorDB port=%s", NEWSROOM, os.environ.get("FALKORDB_PORT", "6379"))
    warm()
    mcp.run("stdio")
