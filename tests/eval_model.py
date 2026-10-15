"""Independent model of the research graph for tests/test_eval.py (Reviewer, checkpoint 4).
Own code: it does not import eval/ or research.py. Every query stays far below FalkorDB's silent
10,000-row cap (slices of ids), and each result is length-checked."""
import os
from collections import defaultdict
from falkordb import FalkorDB

GRAPH = "research:obesity-coke"
G = FalkorDB(port=int(os.environ.get("FALKORDB_PORT", "6379"))).select_graph(GRAPH)
JUNK_TITLES = ("peer review report", "correction:", "corrigendum", "erratum", "notice of retraction")


def q(query, params=None):
    rows = G.ro_query(query, params or {}, 300000).result_set
    assert len(rows) < 9500, "result near the 10,000-row cap: slice the query"
    return rows


def slices(ids, n):
    ids = list(ids)
    for i in range(0, len(ids), n):
        yield ids[i:i + n]


def is_junk(doi, title):
    t = (title or "").lower()
    return (doi or "").startswith("10.5256/") or t.startswith(JUNK_TITLES) or t.strip() in ("index", "front matter")


def build():
    m = {}
    m["meta"] = dict(q("MATCH (m:Meta) RETURN properties(m)")[0][0])
    years = [r[0] for r in q("MATCH (w:Work) RETURN DISTINCT w.year")]
    works = {}
    for y in years:
        cond = "w.year = $y" if y is not None else "w.year IS NULL"
        for r in q(f"MATCH (w:Work) WHERE {cond} RETURN w.oa_id, w.doi, w.title, w.year, w.n_authors, w.scope, "
                   "coalesce(w.authors_truncated,false), coalesce(w.title_unknown,false)", {"y": y}):
            works[r[0]] = dict(doi=r[1], title=r[2], year=r[3], n=r[4], scope=r[5], trunc=bool(r[6]), untitled=bool(r[7]))
    m["works"] = works
    assert len(works) == int(eval(m["meta"]["counts"].replace("null", "None"))["Work"]), "Work count differs from Meta"
    topic = set()
    for ids in slices(works, 3000):
        topic |= {r[0] for r in q("MATCH (w:Work)-[:ABOUT]->(:Topic) WHERE w.oa_id IN $ids RETURN DISTINCT w.oa_id", {"ids": ids})}
    m["topic"] = topic
    auth = defaultdict(list)
    for ids in slices(works, 400):
        for w, p in q("MATCH (p:Person)-[:AUTHORED]->(w:Work) WHERE w.oa_id IN $ids RETURN w.oa_id, p.oa_id", {"ids": ids}):
            auth[w].append(p)
    m["auth"] = auth
    funded = defaultdict(set)
    for w, kind in q("MATCH (w:Work)-[:FUNDED_BY]->(f:Funder {target:true}) RETURN w.oa_id, f.kind"):
        funded[w].add(kind)
    m["funded"] = funded
    m["any_funder"] = set()
    for ids in slices(works, 3000):
        m["any_funder"] |= {r[0] for r in q("MATCH (w:Work)-[:FUNDED_BY]->() WHERE w.oa_id IN $ids RETURN DISTINCT w.oa_id", {"ids": ids})}
    m["bottlers"] = {r[0] for r in q("MATCH (:Org)-[r:PARENT_OF]->(o:Org) WHERE r.rel = 'bottler' RETURN o.oa_id")}
    target_orgs = [r[0] for r in q("MATCH (o:Org {target:true}) RETURN o.oa_id")]
    aff = []
    for o in target_orgs:  # per org: stays under the cap
        skip = 0
        while True:
            rows = q("MATCH (p:Person)-[a:AFFILIATED]->(o:Org {oa_id:$o}) RETURN p.oa_id, a.work ORDER BY p.oa_id, a.work "
                     f"SKIP {skip} LIMIT 8000", {"o": o})
            aff += [(p, w, o) for p, w in rows]
            if len(rows) < 8000:
                break
            skip += 8000
    m["aff"] = aff
    # affiliation of ANY org on a work (for coverage): (work -> exists) via AFFILIATED.work
    return m


def paper(m, w):
    v = m["works"][w]
    return not is_junk(v["doi"], v["title"])


def tier_a_events(m):
    """{person: [(year, 'A1'|'A2', work)]} Tier A evidence events (junk works dropped)."""
    ev = defaultdict(list)
    W = m["works"]
    for w, kinds in m["funded"].items():
        if not paper(m, w):
            continue
        for p in m["auth"].get(w, []):
            small = W[w]["n"] <= 25
            ev[p].append((W[w]["year"], "A1" if ("company" in kinds and small) else "A2", w))
    for p, w, o in m["aff"]:
        if w not in W or not paper(m, w):
            continue
        ev[p].append((W[w]["year"], "A1" if (o not in m["bottlers"] and W[w]["n"] <= 25) else "A2", w))
    return ev
