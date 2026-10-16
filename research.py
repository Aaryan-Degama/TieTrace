"""Checkpoints 3 and 5d: the research queries Q1-Q4 and the name lookup, on one case graph `research:<slug>`.

Every public function returns the envelope from docs/mcp-spec.md section 4:
    {"ok", "data", "cypher": [{"graph","query","params"}], "scope", "verdicts_applied", "warnings"}
or {"ok": False, "error": {"code", "message"}, "cypher": [...]}.

Rules kept here: reads only through GRAPH.RO_QUERY (timeout + row cap); every value goes in as a
query parameter, never into the query text; COAUTH is always matched with `->`.
Port: env FALKORDB_PORT (default 6379).

Which graph: `on_graph(graph)` sets it for the calls inside the `with` block (a contextvar);
the default is GRAPH (research:obesity-coke). Cached facts are kept per graph.
"""
import contextlib
import contextvars
import json
import os
import re

from falkordb import FalkorDB
from redis.exceptions import RedisError, ResponseError

import loader  # the one shared junk rule and name folding live in the loader

GRAPH = "research:obesity-coke"     # the default case graph
GRAPH_RE = re.compile(r"research:[a-z0-9][a-z0-9-]{1,39}")  # research:<slug>, same slug rule as loader.SLUG_RE
QUERY_NAME_Q3 = "q3_bfs_v1"
TIER_WEIGHT = {"A1": 100, "A2": 50}  # fixed score formula, redesign section 4
BIG = 10000                          # row_cap for the "who is Tier A" queries
SERVER_CAP = 10000                   # FalkorDB silently stops a result at 10,000 rows, so reaching it is an error
STOPWORDS = {"and", "the", "for", "with", "from", "that", "this", "are", "of", "in", "on", "to"}

# Works that are not papers (F1000 peer-review reports, corrections, retraction notices, "Index" /
# "Front Matter" records) are never evidence. The rule lives in loader.py (is_junk_work, is_non_paper)
# and is stored at load time as Work.junk, so no query depends on toLower(title). Untitled real papers are kept.
JUNK_PARAMS = {}  # kept so callers can still pass **JUNK_PARAMS; the rule needs no parameters any more
NOT_JUNK = "NOT w.junk"

_db = None
_cache = {}  # per-process, keyed (graph, name): Meta, the Tier-A map, staff. Restart the server after a rebuild.
_graph = contextvars.ContextVar("graph", default=GRAPH)


@contextlib.contextmanager
def on_graph(graph):
    """Run the calls inside the `with` block on `graph` (a research:<slug> name)."""
    token = _graph.set(graph)
    try:
        yield
    finally:
        _graph.reset(token)


def default_case():
    """The case used when a tool gets no `case`: env TIETRACE_CASE, else obesity-coke."""
    return os.environ.get("TIETRACE_CASE") or loader.DEFAULT_CASE


def current_graph():
    return _graph.get()


def known_cases():
    """Case slugs that have a cases/<slug>/case.json (the registry; the loader writes status.json)."""
    return sorted(p.parent.name for p in loader.CASES.glob("*/case.json") if loader.SLUG_RE.fullmatch(p.parent.name))


class ToolError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


# ---------- the read-only guard ----------

def _show(params):
    """Params as logged. The Tier-A id list is long, so it is named after the queries that produce it."""
    out = {}
    for k, v in (params or {}).items():
        if k == "tier_a":
            v = f"<{len(v)} ids: the result of the two Tier-A set queries in this answer>"
        elif isinstance(v, list) and len(v) > 20:
            v = f"<list of {len(v)}>"
        out[k] = v
    return out


def ro_query(graph, query, params=None, timeout_ms=3000, row_cap=500, log=None):
    """Run a read with GRAPH.RO_QUERY. Returns the rows; appends what was run to `log`.
    Reads with no LIMIT of their own get LIMIT row_cap+1, so a runaway result is never fetched in full."""
    if not isinstance(graph, str) or not GRAPH_RE.fullmatch(graph):
        raise ToolError("INVALID_ARG", "graph must be a research:<slug> name")
    params = dict(params or {})
    if "RETURN" in query and not re.search(r"\bLIMIT\b", query, re.IGNORECASE):
        query += " LIMIT $row_cap_plus_one"
        params["row_cap_plus_one"] = row_cap + 1
    if log is not None:
        log.append({"graph": graph, "query": query, "params": _show(params)})
    try:
        rows = _conn().select_graph(graph).ro_query(query, params, timeout_ms).result_set
    except ResponseError as e:
        msg = str(e)
        if "empty key" in msg.lower():  # RO_QUERY on a graph that does not exist (it never creates one)
            raise ToolError("NO_GRAPH", f"no research graph for case '{graph.split(':', 1)[1]}'; "
                                        f"known cases: {', '.join(known_cases()) or 'none'}")
        if "timed out" in msg.lower():
            raise ToolError("TIMEOUT", f"query took longer than {timeout_ms} ms")
        if "read-only" in msg.lower():
            raise ToolError("READ_ONLY", "write queries are not allowed on the research graph")
        raise ToolError("DB_ERROR", msg[:200])
    if len(rows) > row_cap or len(rows) >= SERVER_CAP:  # the server cuts silently at SERVER_CAP rows
        raise ToolError("ROW_LIMIT", f"query returned {len(rows)} rows (cap {min(row_cap, SERVER_CAP - 1)})")
    return rows


def _conn():
    global _db
    if _db is None:
        _db = FalkorDB(port=int(os.environ.get("FALKORDB_PORT", 6379)))
    return _db


def _int(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ToolError("INVALID_ARG", f"{name} must be an integer from {low} to {high}")
    return value


def _str(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ToolError("INVALID_ARG", f"{name} must be a non-empty string")
    return value


def _words(text):
    """Searchable words: 3+ letters and not a stopword."""
    return [w for w in re.findall(r"\w+", text.lower()) if len(w) >= 3 and w not in STOPWORDS]


def _run(impl, *args):
    """Call impl(log, *args) and wrap the result in the envelope; ToolError becomes ok=false."""
    log = []
    try:
        return impl(log, *args)
    except ToolError as e:
        return {"ok": False, "error": {"code": e.code, "message": e.message}, "cypher": log}
    except RedisError as e:  # connection lost, server error: an envelope, not an exception
        return {"ok": False, "error": {"code": "DB_ERROR", "message": f"{type(e).__name__}: {str(e)[:200]}"},
                "cypher": log}


def _ok(data, log, scope=None, warnings=None):
    """Every answer names its graph and the OpenAlex snapshot date (Meta is cached, so this adds no query)."""
    scope = {"graph": current_graph(), "data_version": _meta(log).get("data_version"), **(scope or {})}
    return {"ok": True, "data": data, "cypher": log, "scope": scope,
            "verdicts_applied": [], "warnings": warnings or []}


def _cached(key, log, fn):
    """Run fn(local_log) once per process and graph. The Cypher is added to `log` on every call."""
    key = (current_graph(), key)  # a second case graph never gets the first one's Tier-A map
    if key not in _cache:
        local = []
        _cache[key] = (fn(local), local)
    value, local = _cache[key]
    seen = {(e["query"], str(e["params"])) for e in log}  # do not repeat a query already in this answer
    log.extend(dict(e, cached=True) for e in local if (e["query"], str(e["params"])) not in seen)
    return value


def clear_cache(graph=None):
    """Forget cached facts for one graph, or for all graphs."""
    for key in list(_cache):
        if graph is None or key[0] == graph:
            del _cache[key]


# ---------- shared facts (cached): Meta, coverage, Tier-A people ----------

def _meta(log):
    def load(lg):
        m = ro_query(current_graph(), "MATCH (m:Meta) RETURN properties(m)", log=lg)[0][0]
        if not m.get("work_junk_flag"):  # without Work.junk, NOT_JUNK would drop every work
            raise ToolError("DB_ERROR", f"{current_graph()} was built by an older loader (no Work.junk); "
                                        "rebuild it with loader.py --rebuild")
        return {**m, "last_year": max(m["years"])}
    return _cached("meta", log, load)


def _years(meta):
    return f"{meta['recent_year']}-{meta['last_year']}"


def _coverage(log):
    """Share of topic works with funder data, and with funder or affiliation data. The counts were
    computed at load time and stored in the Meta node (loader.load_stats)."""
    m = _meta(log)
    total = m["topic_works"]
    return {"coverage": round(m["topic_with_funder_or_affiliation"] / total, 3),
            "coverage_funder_only": round(m["topic_with_funder"] / total, 3), "topic_works": total}


def _caveats(log):
    """Known gaps in the data, as plain sentences. Counts come from the Meta node."""
    m = _meta(log)
    return [f"{m['dup_groups']} title+year groups ({m['dup_records']} work records) may still be duplicate "
            f"records of one paper; {m['dup_links']} Tier B/C co-author links include one of them",
            f"{m['truncated_works']} topic works list only their first 100 authors (OpenAlex list limit)",
            "co-authors' other works are not loaded, so ties through unloaded papers are invisible"]


def _tier_a(log):
    """{person oa_id: "A1" | "A2"} for everyone with Tier A evidence (cached)."""
    def load(lg):
        small = _meta(lg)["small_paper"]
        # Authors of works funded by the company (A1 if small) or foundation (A2). max() = best evidence.
        funded = ro_query(current_graph(),
            "MATCH (p:Person)-[:AUTHORED]->(w:Work)-[:FUNDED_BY]->(f:Funder {target:true}) "
            f"WHERE {NOT_JUNK} "
            "RETURN p.oa_id, max(CASE WHEN f.kind = 'company' AND w.n_authors <= $small THEN 1 ELSE 0 END)",
            {**JUNK_PARAMS, "small": small}, row_cap=BIG, log=lg)
        # People with an affiliation to the company family on a work (A1 unless bottler or large paper).
        affil = ro_query(current_graph(),
            "MATCH (p:Person)-[a:AFFILIATED]->(o:Org {target:true}) WITH p, a, o "
            f"MATCH (w:Work) WHERE w.oa_id = a.work AND {NOT_JUNK} "
            "OPTIONAL MATCH (:Org)-[po:PARENT_OF]->(o) "
            "RETURN p.oa_id, max(CASE WHEN coalesce(po.rel,'') <> 'bottler' AND w.n_authors <= $small THEN 1 ELSE 0 END)",
            {**JUNK_PARAMS, "small": small}, row_cap=BIG, log=lg)
        best = {}
        for pid, is_a1 in funded + affil:
            best[pid] = max(best.get(pid, 0), is_a1)
        return {pid: "A1" if v else "A2" for pid, v in best.items()}
    return _cached("tier_a", log, load)


def _staff(log):
    """Ids of people with an affiliation to the company family (cached)."""
    def load(lg):
        rows = ro_query(current_graph(), "MATCH (p:Person)-[:AFFILIATED]->(:Org {target:true}) RETURN DISTINCT p.oa_id",
                        row_cap=BIG, log=lg)
        return [r[0] for r in rows]
    return _cached("staff", log, load)


def _person(log, expert_id):
    _str(expert_id, "expert_id")
    rows = ro_query(current_graph(), "MATCH (p:Person) WHERE p.oa_id = $id "
                           "RETURN p.name, p.orcid, p.works_count, p.profile_flag", {"id": expert_id}, log=log)
    if not rows:
        raise ToolError("NOT_FOUND", f"no person {expert_id} in {current_graph()}")
    return dict(zip(("name", "orcid", "works_count", "profile_flag"), rows[0]))


def _topic_ids(log, topic):
    """Topic id or text -> ([topic ids], display name, warnings)."""
    topics = ro_query(current_graph(), "MATCH (t:Topic) RETURN t.oa_id, t.name", log=log)
    for tid, name in topics:
        if topic.strip().upper() == tid:
            return [tid], name, []
    words = set(_words(topic))
    hit = [(tid, name) for tid, name in topics if words & set(_words(name))]
    if hit:
        return [t for t, _ in hit], ", ".join(n for _, n in hit), []
    return [t for t, _ in topics], topic, [f"topic '{topic}' matches no loaded Topic; used all loaded topics"]


def _label(name, label, public_case):
    return name if public_case is True and name else label or "Expert #1"


def _flag_warnings(profile_flag):
    return [] if profile_flag in (None, "ok") else [f"profile_flag={profile_flag}"]


# ---------- name lookup ----------

def resolve_experts(names, topic):
    return _run(_resolve_experts, names, topic)


def _resolve_experts(log, names, topic):
    if not isinstance(names, list) or not names or not all(isinstance(n, str) and loader.norm_name(n) for n in names):
        raise ToolError("INVALID_ARG", "names must be a non-empty list of non-empty strings")
    _str(topic, "topic")
    tids, _, warnings = _topic_ids(log, topic)
    matches = []
    for name in names:
        words = loader.norm_name(name).split()  # accents folded, lower case, same as Person.name_norm
        # 1. The whole name.
        rows = ro_query(current_graph(), EXACT_NAME_Q, {"norm": " ".join(words), "tids": tids}, log=log)
        loose = False
        # 2. Otherwise the first and the last word, each as a whole word (middle names allowed).
        if not rows and len(words) > 1:
            rows = ro_query(current_graph(), LOOSE_NAME_Q, {"first": words[0] + " ", "last": " " + words[-1], "tids": tids}, log=log)
            loose = bool(rows)
        orgs = _last_orgs(log, [r[0] for r in rows])
        cands = [{"expert_id": r[0], "works_on_topic": r[2], "orcid": r[1], "last_known_org": orgs.get(r[0])}
                 for r in rows]
        # Never guess: only one exact name with works on the topic is MATCHED.
        if not cands:
            status = "NOT_FOUND"
        elif len(cands) == 1 and not loose and cands[0]["works_on_topic"] > 0:
            status = "MATCHED"
        else:
            status = "AMBIGUOUS"
            if loose:
                warnings.append(f"'{name}': no exact name match; candidates share the first and last word only")
            elif len(cands) == 1:
                warnings.append(f"'{name}': the only person with this name has no works on the topic; confirm")
        matches.append({"name_in_input": name, "status": status,
                        "expert_id": cands[0]["expert_id"] if status == "MATCHED" else None,
                        "candidates": cands})
    return _ok({"matches": matches}, log, {"load_scope": _meta(log)["load_scope"]}, warnings)


# Candidates for one name, ranked by works on the topic (OPTIONAL: people with 0 topic works still show).
# Person.name_norm is the loader's folded name, so "Jose Ramirez" finds "José Ramírez".
EXACT_NAME_Q = (
    "MATCH (p:Person) WHERE p.name_norm = $norm "
    "OPTIONAL MATCH (p)-[:AUTHORED]->(w:Work)-[:ABOUT]->(t:Topic) WHERE t.oa_id IN $tids "
    "RETURN p.oa_id, p.orcid, count(DISTINCT w) AS n ORDER BY n DESC, p.oa_id LIMIT 10")
# The spaces make STARTS WITH / ENDS WITH match whole words: "al " does not start "alon eliakim".
LOOSE_NAME_Q = (
    "MATCH (p:Person) WHERE p.name_norm STARTS WITH $first AND p.name_norm ENDS WITH $last "
    "OPTIONAL MATCH (p)-[:AUTHORED]->(w:Work)-[:ABOUT]->(t:Topic) WHERE t.oa_id IN $tids "
    "RETURN p.oa_id, p.orcid, count(DISTINCT w) AS n ORDER BY n DESC, p.oa_id LIMIT 10")


def _last_orgs(log, ids):
    """Most recent affiliation (by paper year) for each person id."""
    if not ids:
        return {}
    rows = ro_query(current_graph(), "MATCH (p:Person) WHERE p.oa_id IN $ids "
                           "MATCH (p)-[a:AFFILIATED]->(o:Org) "
                           "RETURN p.oa_id, o.name, max(a.year) AS y ORDER BY y DESC", {"ids": ids}, log=log)
    out = {}
    for pid, org, _ in rows:  # newest first, so the first one seen is the latest
        out.setdefault(pid, org)
    return out


# ---------- Q1: find_experts ----------

def find_experts(topic, n=20, exclude_target_staff=True):
    return _run(_find_experts, topic, n, exclude_target_staff)


def _find_experts(log, topic, n, exclude_target_staff):
    _str(topic, "topic")
    _int(n, "n", 1, 100)
    if not isinstance(exclude_target_staff, bool):
        raise ToolError("INVALID_ARG", "exclude_target_staff must be true or false")
    staff = _staff(log) if exclude_target_staff else []
    tids, tname, warnings = _topic_ids(log, topic)
    if topic.strip().upper() in tids and len(tids) == 1:
        # A topic id: authors of the works ABOUT that topic.
        q = ("MATCH (p:Person)-[:AUTHORED]->(w:Work)-[:ABOUT]->(t:Topic) "
             "WHERE t.oa_id IN $tids AND NOT p.oa_id IN $staff "
             "RETURN p.oa_id, count(DISTINCT w) AS n, p.works_count, p.profile_flag "
             "ORDER BY n DESC, p.oa_id LIMIT $n")
        params = {"tids": tids, "staff": staff, "n": n}
        topic_out = {"id": tids[0], "name": tname}
    else:
        # Text: the full-text index on Work.title finds the works, then authors are counted over them.
        words = _words(topic)  # 3+ letters, no stopwords: "and" alone is not a topic
        if not words:
            raise ToolError("INVALID_ARG", "topic has no searchable words")
        q = ("CALL db.idx.fulltext.queryNodes('Work', $text) YIELD node AS w "
             "WITH DISTINCT w WHERE (w)-[:ABOUT]->(:Topic) "
             "MATCH (p:Person)-[:AUTHORED]->(w) WHERE NOT p.oa_id IN $staff "
             "RETURN p.oa_id, count(DISTINCT w) AS n, p.works_count, p.profile_flag "
             "ORDER BY n DESC, p.oa_id LIMIT $n")
        params = {"text": " ".join(words), "staff": staff, "n": n}
        topic_out = {"id": None, "name": topic}
        warnings = []
    rows = ro_query(current_graph(), q, params, log=log)
    experts = [{"expert_id": r[0], "label": f"Expert #{i}", "works_on_topic": r[1],
                "works_count": r[2], "profile_flag": r[3]} for i, r in enumerate(rows, 1)]
    flagged = [e["label"] for e in experts if e["profile_flag"] not in ("ok", None)]
    if flagged:
        warnings.append("profile_flag is not ok for: " + ", ".join(flagged))
    meta = _meta(log)
    return _ok({"experts": experts, "topic": topic_out}, log,
               {"years": "all loaded years", "load_scope": meta["load_scope"]}, warnings)


# ---------- Q2: tie_report ----------

# A person's funded works, with the funder and whether the work is ABOUT a loaded topic.
FUNDED_EVIDENCE_Q = (
    "MATCH (p:Person) WHERE p.oa_id = $id "
    "WITH p MATCH (p)-[:AUTHORED]->(w:Work) "  # WITH pins the start at the indexed Person
    f"WHERE {NOT_JUNK} "
    "MATCH (w)-[:FUNDED_BY]->(f:Funder {target:true}) "
    "OPTIONAL MATCH (w)-[:ABOUT]->(t:Topic) "
    "RETURN w.oa_id, w.doi, w.year, w.n_authors, w.authors_truncated, f.name, f.kind, count(t) > 0, w.title_unknown")

# A person's affiliations with the company family, one row per work. The work id travels through WITH
# (FalkorDB cannot use a relationship property straight after a variable-length pattern).
AFFIL_EVIDENCE_Q = (
    "MATCH (p:Person) WHERE p.oa_id = $id "
    "MATCH (p)-[a:AFFILIATED]->(o:Org {target:true}) "
    "WITH a.work AS wid, o "
    f"MATCH (w:Work) WHERE w.oa_id = wid AND {NOT_JUNK} "
    "OPTIONAL MATCH (parent:Org)-[po:PARENT_OF]->(o) "
    "OPTIONAL MATCH (w)-[:ABOUT]->(t:Topic) "
    "RETURN w.oa_id, w.doi, w.year, w.n_authors, w.authors_truncated, o.name, parent.name, po.rel, count(t) > 0, w.title_unknown")

# Co-authors who are Tier A, over COAUTH (for the score) and, where it exists, the COAUTH_B link (for Tier B).
COAUTH_TIES_Q = (
    "MATCH (e:Person) WHERE e.oa_id = $id "
    "MATCH (e)-[c:COAUTH]->(b:Person) WHERE c.recent_small_w > 0 AND b.oa_id IN $tier_a "
    "OPTIONAL MATCH (e)-[cb:COAUTH_B]->(b) "
    "RETURN b.oa_id, b.name, c.recent_small_papers, c.recent_small_w, cb.recent_small_dois "
    "ORDER BY c.recent_small_w DESC, b.oa_id")


def _evidence(log, expert_id, small):
    """All Tier A evidence for one person, each piece classed A1 or A2 with a reason."""
    out = []
    for wid, doi, year, n, trunc, funder, kind, on_topic, untitled in ro_query(
            current_graph(), FUNDED_EVIDENCE_Q, {"id": expert_id, **JUNK_PARAMS}, log=log):
        if kind == "foundation":
            tier, reason = "A2", "foundation grant"
        elif n > small:
            tier, reason = "A2", f"large multi-author paper ({n}{'+' if trunc else ''} authors)"
        else:
            tier, reason = "A1", "company-funded work"
        out.append({"kind": "FUNDED_BY", "doi": doi, "work_id": wid, "year": year, "n_authors": n,
                    "via": funder, "family_path": [funder], "tier": tier, "reason": reason,
                    "topic_label": "on-topic" if on_topic else "off-topic", "untitled": bool(untitled)})
    for wid, doi, year, n, trunc, org, parent, rel, on_topic, untitled in ro_query(
            current_graph(), AFFIL_EVIDENCE_Q, {"id": expert_id, **JUNK_PARAMS}, log=log):
        if rel == "bottler":
            tier, reason = "A2", "bottler affiliation"
        elif n > small:
            tier, reason = "A2", f"large multi-author paper ({n}{'+' if trunc else ''} authors)"
        else:
            tier, reason = "A1", "company affiliation on a work"
        out.append({"kind": "AFFILIATED", "doi": doi, "work_id": wid, "year": year, "n_authors": n,
                    "via": org, "family_path": [org, parent] if parent else [org], "tier": tier,
                    "reason": reason, "topic_label": "on-topic" if on_topic else "off-topic",
                    "untitled": bool(untitled)})
    # No duplicates (same work and same company entity), strongest first, then newest.
    unique = {(e["kind"], e["work_id"], e["via"]): e for e in out}
    return sorted(unique.values(), key=lambda e: (e["tier"], -(e["year"] or 0), e["work_id"]))


def _nearest_tier_a(log, expert_id, k, tier_a):
    """Fewest COAUTH_B hops (1..k) to a Tier-A person, via algo.BFS. BFS returns NO ROW when nothing is
    reached; the count below is then 0. Returns (hops, how many Tier-A people at that distance,
    first one's id, first one's name), or (None, 0, None, None)."""
    for hops in range(1, k + 1):
        count, first_id, first_name = ro_query(current_graph(),
            "MATCH (e:Person) WHERE e.oa_id = $id "
            "CALL algo.BFS(e, $hops, 'COAUTH_B') YIELD nodes "
            "UNWIND nodes AS n WITH n WHERE n.oa_id IN $tier_a "
            "WITH n ORDER BY n.oa_id RETURN count(n), head(collect(n.oa_id)), head(collect(n.name))",
            {"id": expert_id, "hops": hops, "tier_a": list(tier_a)}, log=log)[0]
        if count:
            return hops, count, first_id, first_name
    return None, 0, None, None


def _check_label_args(label, public_case):
    if label is not None and not isinstance(label, str):
        raise ToolError("INVALID_ARG", "label must be a string or null")
    if not isinstance(public_case, bool):
        raise ToolError("INVALID_ARG", "public_case must be true or false")


def tie_report(expert_id, label=None, public_case=False):
    return _run(_tie_report, expert_id, label, public_case)


def _tie_report(log, expert_id, label, public_case):
    _check_label_args(label, public_case)
    person = _person(log, expert_id)
    meta, tier_a = _meta(log), _tier_a(log)
    label = _label(person["name"], label, public_case)
    evidence = _evidence(log, expert_id, meta["small_paper"])
    ties = ro_query(current_graph(), COAUTH_TIES_Q, {"id": expert_id, "tier_a": list(tier_a)}, log=log)

    # Tier B links = at least 2 recent small shared papers with a Tier-A person; they carry the DOIs.
    coauth_ties = []
    for pid, name, n_papers, w, dois in ties:
        if dois is not None and n_papers >= 2:
            coauth_ties.append({"to_label": _label(name, f"Tier-A #{len(coauth_ties) + 1}", public_case),
                                "to_expert_id": pid, "anchor_tier": tier_a[pid], "n_papers": n_papers,
                                "w": round(w, 4), "dois": [d for d in dois if d]})
    coauth_w = sum(t[3] for t in ties)

    nearest = None
    if any(e["tier"] == "A1" for e in evidence):
        tier = "A1"
    elif evidence:
        tier = "A2"
    elif coauth_ties:
        tier = "B"
    else:
        hops, count, _, _ = _nearest_tier_a(log, expert_id, 3, tier_a)  # 1 hop would already be Tier B
        tier = "C" if hops else "NONE"
        if hops:
            nearest = {"hops": hops, "anchor_count": count}
    score = TIER_WEIGHT.get(tier, 0) + coauth_w

    warnings = _flag_warnings(person["profile_flag"])
    if any(e["doi"] is None for e in evidence):
        warnings.append("some evidence works have no DOI in OpenAlex; they cannot back a note claim")
    if any(e["n_authors"] and e["reason"].endswith("+ authors)") for e in evidence):
        warnings.append("an evidence work lists 100+ authors; its author list was cut by OpenAlex")
    data = {"expert_id": expert_id, "label": label, "tier": tier, "score": round(score, 4),
            "score_parts": {"tier_weight": TIER_WEIGHT.get(tier, 0), "coauth_w": round(coauth_w, 4),
                            "tier_a_links": len(ties)},
            "evidence": evidence, "coauth_ties": coauth_ties, "nearest": nearest,
            "orcid": person["orcid"], "profile_flag": person["profile_flag"],
            "wording": _wording(label, tier, evidence, coauth_ties, nearest, meta),
            "check_id": None}
    scope = {"years": _years(meta), "load_scope": meta["load_scope"], **_coverage(log),
             "caveats": _caveats(log)}
    return _ok(data, log, scope, warnings)


def _wording(label, tier, evidence, coauth_ties, nearest, meta):
    """One sentence per tier. Only 'OpenAlex records ...' phrasing; never the banned words."""
    if tier in ("A1", "A2"):
        e = next(x for x in evidence if x["tier"] == tier)
        what = (f"as author of a work funded by {e['via']}" if e["kind"] == "FUNDED_BY"
                else f"as listing an affiliation with {e['via']} on a work")
        extra = f"; {e['reason']}" if tier == "A2" else ""
        more = f", plus {len(evidence) - 1} more record(s)" if len(evidence) > 1 else ""
        return (f"OpenAlex records {label} {what} (DOI {e['doi'] or 'not available'}, {e['year']}, "
                f"{e['topic_label']} {'untitled ' if e['untitled'] else ''}work{extra}){more}.")
    if tier == "B":
        n = sum(t["n_papers"] for t in coauth_ties)
        return (f"OpenAlex records {label} as co-author of {n} small papers since {meta['recent_year']} "
                f"with {len(coauth_ties)} Tier-A person(s).")
    if tier == "C":
        return (f"OpenAlex records {label} within {nearest['hops']} co-author hops of a Tier-A person "
                f"(context only, not for a note).")
    return f"OpenAlex records no tie for {label} within the loaded data; absence is not proof."


# ---------- Q3: check_no_tie ----------

def check_no_tie(expert_id, k=3, years=5, label=None, public_case=False):
    return _run(_check_no_tie, expert_id, k, years, label, public_case)


def _check_no_tie(log, expert_id, k, years, label, public_case):
    _check_label_args(label, public_case)
    _str(expert_id, "expert_id")
    _int(k, "k", 1, 3)
    _int(years, "years", 0, 100)
    meta = _meta(log)
    span = meta["last_year"] - meta["recent_year"]
    if years != span:
        raise ToolError("INVALID_ARG", f"the graph is precomputed for the last {span} years "
                                       f"({_years(meta)}); years must be {span}")
    person = _person(log, expert_id)
    label = _label(person["name"], label, public_case)
    tier_a = _tier_a(log)
    cov = _coverage(log)

    nearest = None
    if expert_id in tier_a:  # the expert has direct Tier A evidence: 0 hops
        nearest = {"tier_a_label": label, "tier_a_id": expert_id, "anchor_tier": tier_a[expert_id], "hops": 0}
    else:
        hops, count, pid, name = _nearest_tier_a(log, expert_id, k, tier_a)
        if hops:
            nearest = {"tier_a_label": _label(name, "Tier-A #1", public_case), "tier_a_id": pid,
                       "anchor_tier": tier_a[pid], "hops": hops, "anchor_count": count}
    found = nearest is not None
    pct, pct_funder = round(100 * cov["coverage"]), round(100 * cov["coverage_funder_only"])
    if found:
        wording = (f"OpenAlex records a Tier A tie or a Tier-A person {nearest['hops']} co-author hop(s) "
                   f"from {label}; see tie_report for the DOIs.")
    else:
        wording = (f"No Tier A or B tie and no Tier-A person within {k} co-author hops of {label} "
                   f"({_years(meta)}; {pct_funder}% of topic works have funder data and {pct}% have funder or "
                   f"affiliation data). "
                   f"Absence in the research record is not proof.")
    scope = {"hops": k, "years": _years(meta), "coverage": cov["coverage"],
             "coverage_funder_only": cov["coverage_funder_only"], "load_scope": meta["load_scope"],
             "caveats": _caveats(log)}
    data = {"result": "TIE_FOUND" if found else "NO_TIE_FOUND", "nearest": nearest,
            "query_name": QUERY_NAME_Q3, "label": label, "wording": wording}
    return _ok(data, log, scope, _flag_warnings(person["profile_flag"]))


# ---------- Q4: bridges ----------

def bridges(expert_id, depth=2, limit=5, label=None, public_case=False):
    return _run(_bridges, expert_id, depth, limit, label, public_case)


def _bridges(log, expert_id, depth, limit, label, public_case):
    _check_label_args(label, public_case)
    _str(expert_id, "expert_id")
    _int(depth, "depth", 2, 3)
    _int(limit, "limit", 1, 50)
    person = _person(log, expert_id)
    label = _label(person["name"], label, public_case)
    tier_a = _tier_a(log)
    params = {"id": expert_id, "tier_a": list(tier_a)}
    # Paths over COAUTH_B (links with 2+ recent small shared papers). Strength = the weakest link
    # on the path (the bottleneck), because a chain is only as strong as its weakest tie.
    if depth == 2:
        pattern = "(e)-[c1:COAUTH_B]->(m1:Person)-[c2:COAUTH_B]->(a:Person)"
        edges = ["c1", "c2"]
        where = "a.oa_id IN $tier_a AND a <> e"
        strength = "CASE WHEN c1.recent_small_w < c2.recent_small_w THEN c1.recent_small_w ELSE c2.recent_small_w END"
        mids = "m1"
    else:
        pattern = "(e)-[c1:COAUTH_B]->(m1:Person)-[c2:COAUTH_B]->(m2:Person)-[c3:COAUTH_B]->(a:Person)"
        edges = ["c1", "c2", "c3"]
        where = "a.oa_id IN $tier_a AND a <> e AND m2 <> e AND a <> m1"
        strength = ("CASE WHEN c1.recent_small_w <= c2.recent_small_w AND c1.recent_small_w <= c3.recent_small_w "
                    "THEN c1.recent_small_w WHEN c2.recent_small_w <= c3.recent_small_w "
                    "THEN c2.recent_small_w ELSE c3.recent_small_w END")
        mids = "m1, m2"
    edge_cols = ", ".join(f"{c}.recent_small_dois, {c}.recent_small_w" for c in edges)
    mid_cols = ", ".join(f"{m}.oa_id, {m}.name" for m in mids.split(", "))
    q = (f"MATCH (e:Person) WHERE e.oa_id = $id MATCH {pattern} WHERE {where} "
         f"WITH *, {strength} AS strength "
         f"RETURN {mid_cols}, a.oa_id, a.name, {edge_cols}, strength "
         f"ORDER BY strength DESC, a.oa_id LIMIT $limit")
    # Time budget: paths 2 s + count 1 s stays under the 3 s gate even for a bigger hub. If the count
    # does not finish in its 1 s, the paths are still returned and bridge_count is null with a warning.
    rows = ro_query(current_graph(), q, {**params, "limit": limit}, timeout_ms=2000, log=log)
    count, count_warning = None, []
    try:
        count = ro_query(current_graph(), f"MATCH (e:Person) WHERE e.oa_id = $id MATCH {pattern} WHERE {where} RETURN count(*)",
                         params, timeout_ms=1000, log=log)[0][0]
    except ToolError as e:
        if e.code != "TIMEOUT":
            raise
        count_warning = ["bridge_count not computed: counting all paths took over 1 s"]

    n_mid = depth - 1
    paths = []
    for i, r in enumerate(rows, 1):
        mid = [(r[2 * j], r[2 * j + 1]) for j in range(n_mid)]
        a_id, a_name = r[2 * n_mid], r[2 * n_mid + 1]
        rest = r[2 * n_mid + 2:]
        paths.append({
            "nodes": [label] + [_label(nm, f"Co-author #{i}.{j}", public_case) for j, (_, nm) in enumerate(mid, 1)]
                     + [_label(a_name, f"Tier-A #{i}", public_case)],
            "node_ids": [expert_id] + [m for m, _ in mid] + [a_id],
            "anchor_tier": tier_a[a_id],
            "edges": [{"dois": [d for d in rest[2 * j] if d], "w": round(rest[2 * j + 1], 4)} for j in range(depth)],
            "strength": round(rest[-1], 4)})
    meta = _meta(log)
    scope = {"hops": depth, "years": _years(meta), "load_scope": meta["load_scope"]}
    # max_flow is cut (checkpoints.md): kept null so the output matches the spec.
    return _ok({"label": label, "paths": paths, "bridge_count": count, "max_flow": None}, log, scope,
               _flag_warnings(person["profile_flag"]) + count_warning)


# ---------- list_cases: which case graphs exist (checkpoint 5d) ----------

def list_cases():
    return _run(_list_cases)


def _list_cases(log):
    """Every case in cases/*/case.json plus every research:* graph, with its Meta and status.json."""
    names = _conn().list_graphs()  # GRAPH.LIST; newsroom graphs are never shown
    graphs = {g for g in names if GRAPH_RE.fullmatch(g)}
    registered = set(known_cases())
    slugs = sorted(registered | {g.split(":", 1)[1] for g in graphs})
    staging = sorted(g.split(":", 1)[1] for g in names if g.startswith("staging:"))  # named by case, not by key
    warnings = ([f"unfinished build(s) left a staging graph that still uses RAM, for case(s): {', '.join(staging)}; "
                 "the next build of that case replaces it"] if staging else [])
    cases = []
    for slug in slugs:
        graph = f"research:{slug}"
        status_file = loader.CASES / slug / "status.json"
        try:
            status = json.loads(status_file.read_text())
        except (OSError, ValueError):
            status = None
        status = status if isinstance(status, dict) else None
        meta = None
        if graph in graphs:
            rows = ro_query(graph, "MATCH (m:Meta) RETURN m.built_at, m.data_version, m.config_hash, m.counts, "
                                   "m.topic_ids, m.funder_ids, m.build_status", log=log)
            if rows:
                meta = dict(zip(("built_at", "data_version", "config_hash", "counts", "topic_ids", "funder_ids",
                                 "build_status"), rows[0]))
                meta["counts"] = json.loads(meta["counts"]) if meta["counts"] else None
        cases.append({"case": slug, "graph": graph, "registered": slug in registered, "graph_exists": graph in graphs,
                      "status": status.get("status") if status else None, "meta": meta,
                      "total_graph_sz_mb": status.get("total_graph_sz_mb") if status else None})
    return {"ok": True, "data": {"cases": cases, "default_case": default_case()},
            "cypher": log, "scope": {"source": "GRAPH.LIST, each graph's Meta node, cases/*/status.json"},
            "verdicts_applied": [], "warnings": warnings}


# ---------- stale caches after a rebuild ----------

_seen_build = {}  # graph -> (built_at, config_hash) from status.json when its cache was filled


def drop_cache_if_rebuilt(slug):
    """A rebuild swaps a new graph in while the server runs. status.json (a small file, no query) says when;
    if its built_at or config_hash changed since we last looked, forget that graph's cached facts."""
    graph = f"research:{slug}"
    try:
        status = json.loads((loader.CASES / slug / "status.json").read_text())
        build = (status.get("built_at"), status.get("config_hash"))
    except (OSError, ValueError, AttributeError):
        build = None
    if graph in _seen_build and _seen_build[graph] != build:
        clear_cache(graph)
    _seen_build[graph] = build
