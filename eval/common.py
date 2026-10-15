"""Shared helpers for the checkpoint-4 evaluations (E1, E2, E3, E7).
Reads the graph only through research.ro_query (GRAPH.RO_QUERY), in pages well under the silent
10,000-row cap. Run from the project root, e.g.:  FALKORDB_PORT=6380 uv run python eval/e1.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("FALKORDB_PORT", "6380")

import research  # noqa: E402
from research import GRAPH, JUNK_PARAMS, NOT_JUNK  # noqa: E402


def rq(query, params=None, timeout_ms=120000, row_cap=9000):
    """One read-only query. Each page must stay below row_cap (< 10,000)."""
    return research.ro_query(GRAPH, query, params, timeout_ms=timeout_ms, row_cap=row_cap)


def by_ids(query, ids, extra=None, size=1000):
    """Run `query` (which uses $ids) over slices of `ids` and concatenate the rows."""
    ids, rows = list(ids), []
    for i in range(0, len(ids), size):
        rows += rq(query, {"ids": ids[i:i + size], **(extra or {})})
    return rows


def load_works():
    """{work id: dict(year, n, doi, title, paper, topic, scope, truncated, untitled)}.
    paper=False for junk (F1000 reports, corrections, retractions) and Index/Front Matter records:
    the one rule the loader and research.py use (NOT_JUNK). topic=True if the work has an ABOUT edge."""
    years = [r[0] for r in rq("MATCH (w:Work) RETURN DISTINCT w.year")]  # one page per year
    works = {}
    for y in years:
        q = ("MATCH (w:Work) WHERE " + ("w.year = $y " if y is not None else "w.year IS NULL ") +
             f"OPTIONAL MATCH (w)-[:ABOUT]->(t:Topic) "
             f"RETURN w.oa_id, w.year, w.n_authors, w.doi, w.title, {NOT_JUNK}, count(t) > 0, w.scope, "
             "coalesce(w.authors_truncated,false), coalesce(w.title_unknown,false)")
        for wid, year, n, doi, title, paper, topic, scope, trunc, untitled in rq(q, {**JUNK_PARAMS, "y": y}):
            works[wid] = dict(year=year, n=n, doi=doi, title=title, paper=bool(paper), topic=bool(topic),
                              scope=scope, truncated=bool(trunc), untitled=bool(untitled))
    return works


def load_authors(work_ids):
    """{work id: [person ids]} (AUTHORED), read in slices of work ids."""
    out = {}
    q = "MATCH (w:Work) WHERE w.oa_id IN $ids MATCH (p:Person)-[:AUTHORED]->(w) RETURN w.oa_id, p.oa_id"
    for wid, pid in by_ids(q, work_ids, size=400):
        out.setdefault(wid, []).append(pid)
    return out


def load_target_links():
    """Tier-A raw material: ({work: set of funder kinds}, [(person, work, org rel)]).
    Funder kinds are 'company' / 'foundation'; org rel is 'bottler', 'subsidiary' or '' (the parent)."""
    funded = {}
    for wid, kind in rq("MATCH (w:Work)-[:FUNDED_BY]->(f:Funder {target:true}) RETURN w.oa_id, f.kind"):
        funded.setdefault(wid, set()).add(kind)
    affil = rq("MATCH (p:Person)-[a:AFFILIATED]->(o:Org {target:true}) WITH p, a, o "
               "OPTIONAL MATCH (:Org)-[po:PARENT_OF]->(o) RETURN p.oa_id, a.work, coalesce(po.rel,'')")
    return funded, [tuple(r) for r in affil]


def load_works_counts(person_ids):
    """{person id: lifetime works_count from OpenAlex} (may be None)."""
    q = "MATCH (p:Person) WHERE p.oa_id IN $ids RETURN p.oa_id, p.works_count"
    return dict(by_ids(q, person_ids, size=3000))


def auc(pos, neg):
    """AUC = P(positive score > negative score) + 0.5 * P(tie), by the rank-sum (Mann-Whitney) method.
    pos, neg are lists of scores."""
    both = sorted([(s, 1) for s in pos] + [(s, 0) for s in neg])
    rank_sum, i = 0.0, 0
    while i < len(both):
        j = i
        while j < len(both) and both[j][0] == both[i][0]:
            j += 1
        avg_rank = (i + 1 + j) / 2  # tied scores share the average of their ranks
        rank_sum += avg_rank * sum(lab for _, lab in both[i:j])
        i = j
    n1, n0 = len(pos), len(neg)
    return (rank_sum - n1 * (n1 + 1) / 2) / (n1 * n0)


def percentile(sorted_values, q):
    """q in [0,1] on an already sorted list (nearest-rank, no interpolation)."""
    return sorted_values[min(len(sorted_values) - 1, max(0, int(q * len(sorted_values))))]
