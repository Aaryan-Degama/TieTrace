"""Tests for loader.py (checkpoint 2).

Run:  uv run python tests/test_loader.py
Part (a) tests loader.py's pure functions on small handmade works (no DB, no API).
Part (b) checks invariants of the live graph `research:obesity-coke`, read-only via ro_query.
A failing test means the loader (or graph) breaks the spec; it is reported, not hidden.
"""
import json
import os
import random
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import loader  # noqa: E402

COMPANY, FOUNDATION = loader.COMPANY_FUNDER, loader.FOUNDATION_FUNDER


# ---------- fixtures ----------
def author(aid, orcid=None, insts=(), name=None, pos="middle"):
    return {"author": {"id": f"https://openalex.org/{aid}" if aid else None,
                       "display_name": name or aid, "orcid": orcid},
            "author_position": pos,
            "institutions": [{"id": f"https://openalex.org/{i}", "display_name": i, "type": "x"}
                             for i in insts]}


def work(wid, authors, doi="https://doi.org/10.1/ABC", year=2022, funders=(), topics=("T10010",)):
    return {"id": f"https://openalex.org/{wid}", "doi": doi, "title": f"title {wid}",
            "publication_year": year, "authorships": authors,
            "funders": [{"id": f"https://openalex.org/{f}", "display_name": f} for f in funders],
            "topics": [{"id": f"https://openalex.org/{t}"} for t in topics]}


def rows_for(works, truncated=(), works_counts=None):
    return loader.build_rows({loader.short(w["id"]): w for w in works}, set(truncated), works_counts or {})


def coauth_map(rows):
    return {(r["a"], r["b"]): r for r in rows["coauth"]}


# ---------- (a) unit tests ----------
def test_short():
    assert loader.short("https://openalex.org/A123") == "A123"
    assert loader.short(None) is None


def test_bare_doi():
    assert loader.bare_doi("https://doi.org/10.1016/S0140-6736(14)60460-8") == "10.1016/s0140-6736(14)60460-8"
    assert loader.bare_doi(None) is None
    assert loader.bare_doi("") is None


def test_bare_doi_prefix_only_is_none():
    # OpenAlex really returns "https://doi.org/" for 6 cached topic works; that is "no DOI".
    assert loader.bare_doi("https://doi.org/") is None, repr(loader.bare_doi("https://doi.org/"))


def test_single_author_no_coauth():
    r = rows_for([work("W1", [author("A1")])])
    assert r["coauth"] == [] and len(r["authored"]) == 1


def test_two_authors_weights():
    r = rows_for([work("W1", [author("A1"), author("A2")], year=2022)])
    e = coauth_map(r)[("A1", "A2")]
    assert (e["n_papers"], e["w"], e["small_papers"]) == (1, 1.0, 1)
    assert (e["recency_w"], e["recent_small_papers"], e["recent_small_w"]) == (1.0, 1, 1.0)
    assert e["dois"] == ["10.1/abc"] and e["years"] == [2022] and e["works"] == ["W1"]


def test_both_directions_identical():
    r = rows_for([work("W1", [author(f"A{i}") for i in range(4)])])
    m = coauth_map(r)
    assert len(m) == 12  # 6 pairs x 2
    for (a, b), e in m.items():
        assert a != b
        o = m[(b, a)]
        assert all(e[k] == o[k] for k in e if k not in ("a", "b"))


def test_26_authors_is_large():
    r = rows_for([work("W1", [author(f"A{i:02d}") for i in range(26)], year=2023)])
    e = coauth_map(r)[("A00", "A01")]
    assert e["small_papers"] == 0 and e["recent_small_papers"] == 0 and e["recent_small_w"] == 0
    assert abs(e["w"] - 1 / 25) < 1e-12 and abs(e["recency_w"] - 1 / 25) < 1e-12


def test_25_authors_is_small():
    r = rows_for([work("W1", [author(f"A{i:02d}") for i in range(25)], year=2023)])
    e = coauth_map(r)[("A00", "A01")]
    assert e["small_papers"] == 1 and abs(e["recent_small_w"] - 1 / 24) < 1e-12


def test_recency_boundary():
    r = rows_for([work("W1", [author("A1"), author("A2")], year=2020),
                  work("W2", [author("A1"), author("A2")], year=2021, doi="https://doi.org/10.1/x")])
    e = coauth_map(r)[("A1", "A2")]
    assert e["n_papers"] == 2 and e["small_papers"] == 2 and e["recent_small_papers"] == 1
    assert e["recency_w"] == 1.0 and e["w"] == 2.0


def test_duplicate_author_on_work():
    # OpenAlex sometimes lists the same author twice (362 such slots in the cache).
    r = rows_for([work("W1", [author("A1"), author("A1"), author("A2")])])
    assert ("A1", "A1") not in coauth_map(r)
    assert len([x for x in r["authored"] if x["p"] == "A1"]) == 1
    assert len(r["coauth"]) == 2


def test_duplicate_author_does_not_inflate_n():
    # A repeated slot is one person: the work has 2 authors, so w must be 1/(2-1) = 1.
    r = rows_for([work("W1", [author("A1"), author("A1"), author("A2")])])
    assert r["works"][0]["n_authors"] == 2, r["works"][0]["n_authors"]
    assert coauth_map(r)[("A1", "A2")]["w"] == 1.0


def test_null_author_id_counts_in_n_but_no_node():
    r = rows_for([work("W1", [author("A1"), author(None), author("A2")])])
    assert {p["oa_id"] for p in r["persons"]} == {"A1", "A2"}
    assert r["works"][0]["n_authors"] == 3
    assert abs(coauth_map(r)[("A1", "A2")]["w"] - 0.5) < 1e-12


def test_missing_doi():
    r = rows_for([work("W1", [author("A1"), author("A2")], doi=None)])
    assert r["works"][0]["doi"] is None
    assert coauth_map(r)[("A1", "A2")]["dois"] == [""]


def test_missing_year_storable():
    # FalkorDB rejects arrays containing null ("Property values can only be of primitive types
    # or arrays of primitive types"), so a None in years[] would crash the whole COAUTH batch.
    r = rows_for([work("W1", [author("A1"), author("A2")], year=None)])
    e = coauth_map(r)[("A1", "A2")]
    assert e["recency_w"] == 0 and e["recent_small_papers"] == 0
    assert None not in e["years"], e["years"]


def test_truncated_flag_and_large():
    w = work("W1", [author(f"A{i:03d}") for i in range(100)])
    r = rows_for([w], truncated={"W1"})
    assert r["works"][0].get("authors_truncated") is True
    assert coauth_map(r)[("A000", "A001")]["small_papers"] == 0
    r2 = rows_for([w])
    assert "authors_truncated" not in r2["works"][0]


def test_profile_flags():
    ws = [work("W1", [author("A1", orcid="https://orcid.org/0000-1"), author("A2"),
                      author("A3"), author("A4", orcid="https://orcid.org/0000-4")])]
    r = rows_for(ws, works_counts={"A1": 10, "A2": 5, "A3": 2001, "A4": None})
    p = {x["oa_id"]: x for x in r["persons"]}
    assert p["A1"]["profile_flag"] == "ok" and p["A1"]["orcid"] == "0000-1"
    assert p["A2"]["profile_flag"] == "no_orcid"
    assert p["A3"]["profile_flag"] == "works_count_anomaly"  # stronger flag wins over no_orcid
    assert p["A4"]["profile_flag"] == "ok" and p["A4"]["works_count"] is None
    r = rows_for(ws, works_counts={"A1": 2000})
    assert {x["oa_id"]: x for x in r["persons"]}["A1"]["profile_flag"] == "ok"  # strictly above


def test_funder_kind_and_target():
    r = rows_for([work("W1", [author("A1")], funders=(COMPANY, FOUNDATION, "F999"))])
    f = {x["oa_id"]: x for x in r["funders"]}
    assert f[COMPANY]["kind"] == "company" and f[COMPANY]["target"] is True
    assert f[FOUNDATION]["kind"] == "foundation" and f[FOUNDATION]["target"] is True
    assert f["F999"]["kind"] is None and f["F999"]["target"] is False
    assert len(r["funded_by"]) == 3


def test_family_orgs_and_parent_of():
    r = rows_for([work("W1", [author("A1", insts=("I4405274843", "I1"))])])
    o = {x["oa_id"]: x for x in r["orgs"]}
    assert all(o[i]["target"] for i in loader.COKE_FAMILY)  # seeded even when unseen
    assert o["I1"]["target"] is False
    assert sorted((x["a"], x["b"]) for x in r["parent_of"]) == sorted(
        (loader.COKE_PARENT, c) for c in loader.COKE_FAMILY if c != loader.COKE_PARENT)
    aff = [x for x in r["affiliated"] if x["o"] == "I4405274843"][0]
    assert aff == {"p": "A1", "o": "I4405274843", "year": 2022, "doi": "10.1/abc", "work": "W1"}


def test_about_only_loaded_topics():
    r = rows_for([work("W1", [author("A1")], topics=("T10010", "T99999", "T10352"))])
    assert sorted(x["t"] for x in r["about"]) == ["T10010", "T10352"]


def test_no_cypher_string_formatting_of_data():
    # Every data-bearing query must take its values from $rows parameters.
    for name, q in loader.QUERIES.items():
        assert q.startswith("UNWIND $rows AS r"), name
    src = Path(loader.__file__).read_text(encoding="utf-8")
    fq = [ln.strip() for ln in src.splitlines() if 'query(f"' in ln]
    # the only f-string queries interpolate loop constants (label / rel), never API data
    assert all("{label}" in ln or "{rel}" in ln for ln in fq), fq


def test_n_authors_counts_id_less_slots_once_each():
    w = work("W1", [author("A1"), author(None), author(None), author("A1")])
    assert loader.author_slots(w) == (["A1"], 3)


def test_coauth_b_rows():
    ws = [work("W1", [author("A1"), author("A2")], year=2022),
          work("W2", [author("A1"), author("A2"), author("A3")], year=2023, doi="https://doi.org/10.1/y"),
          work("W3", [author("A3"), author("A4")], year=2019, doi="https://doi.org/10.1/z")]
    r = rows_for(ws)
    assert sorted((x["a"], x["b"]) for x in r["coauth_b"]) == [("A1", "A2"), ("A2", "A1")]
    assert loader.QUERIES["coauth_b"].startswith("UNWIND $rows AS r")


def test_parent_of_rel():
    r = rows_for([work("W1", [author("A1")])])
    assert {x["rel"] for x in r["parent_of"]} <= {"subsidiary", "bottler"}
    assert all(x["rel"] == "bottler" for x in r["parent_of"])


def test_dedupe_by_doi_and_title():
    ws = {"W1": work("W1", [author("A1"), author("A2")], doi="https://doi.org/10.1/A"),
          "W2": work("W2", [author("A1"), author("A2")], doi="https://doi.org/10.1/a"),
          "W3": work("W3", [author("A1"), author("A2")], doi="https://doi.org/10.1/b"),
          "W4": work("W4", [author("A2"), author("A1")], doi=None),
          "W5": work("W5", [author("A3")], doi=None)}
    ws["W3"]["title"] = ws["W4"]["title"] = "Same  Title!"
    scope = {"W1": "topic", "W2": "funded", "W3": "topic", "W4": "family", "W5": "topic"}
    assert loader.dedupe(ws, scope) == 2
    assert sorted(ws) == ["W2", "W4", "W5"]  # funded / family records are kept


def test_cache_header_params_checked():
    import tempfile
    old = loader.RAW
    try:
        loader.RAW = Path(tempfile.mkdtemp())
        (loader.RAW / "x.jsonl").write_text(loader.header_line({"a": 1}) + '{"k": 1}\n', encoding="utf-8")
        assert loader.read_cache("x", {"a": 1}, False) == [{"k": 1}]
        assert loader.read_cache("x", {"a": 2}, False) is None
        assert loader.read_cache("x", {"a": 1}, True) is None
    finally:
        loader.RAW = old


def test_get_error_hides_key():
    class R:
        status_code = 403
    real, loader.httpx.get = loader.httpx.get, lambda *a, **k: R()
    loader.KEY = "SECRETKEY123"
    try:
        loader.get("works", {})
    except RuntimeError as e:
        assert "SECRETKEY123" not in str(e) and "api_key" not in str(e), e
    else:
        raise AssertionError("no error raised")
    finally:
        loader.httpx.get = real


# ---------- (b) live graph invariants ----------
_G = None


def G():
    global _G
    if _G is None:
        from falkordb import FalkorDB
        _G = FalkorDB(port=int(os.environ.get("FALKORDB_PORT", 6379))).select_graph(loader.GRAPH)
    return _G


def ro(q, params=None):
    return G().ro_query(q, params, timeout=600000).result_set


def test_graph_counts_match_meta():
    meta = ro("MATCH (m:Meta) RETURN m")
    assert len(meta) == 1, f"{len(meta)} Meta nodes"
    counts = json.loads(meta[0][0].properties["counts"])
    for label in ("Person", "Work", "Org", "Funder", "Topic"):
        assert ro(f"MATCH (n:{label}) RETURN count(n)")[0][0] == counts[label], label
    for rel in ("AUTHORED", "AFFILIATED", "FUNDED_BY", "PARENT_OF", "ABOUT", "COAUTH"):
        assert ro(f"MATCH ()-[r:{rel}]->() RETURN count(r)")[0][0] == counts[rel], rel


def test_meta_complete():
    p = ro("MATCH (m:Meta) RETURN properties(m)")[0][0]
    for k in ("graph", "built_at", "data_version", "load_scope", "topic_ids", "funder_ids",
              "topic_cap", "topic_total", "counts", "small_paper", "recent_year", "truncated_works",
              "duplicates_removed", "years", "years_loaded", "years_total"):
        assert p.get(k) not in (None, ""), k
    assert p["graph"] == loader.GRAPH
    assert sorted(p["funder_ids"]) == sorted([COMPANY, FOUNDATION])
    assert sorted(p["topic_ids"]) == sorted(loader.TOPICS)
    assert p["topic_total"] >= p["topic_cap"]
    assert (p["small_paper"], p["recent_year"]) == (loader.SMALL_PAPER, loader.RECENT_YEAR)
    assert len(p["years"]) == len(p["years_loaded"]) == len(p["years_total"])
    assert all(0 < lo <= t for lo, t in zip(p["years_loaded"], p["years_total"]))
    assert p["data_version"].startswith("openalex@20")
    assert json.loads(p["counts"])["COAUTH_B"] == ro("MATCH ()-[r:COAUTH_B]->() RETURN count(r)")[0][0]


def test_coauth_b_matches_filter():
    want = ro("MATCH ()-[r:COAUTH]->() WHERE r.recent_small_papers >= 2 RETURN count(r)")[0][0]
    assert ro("MATCH ()-[r:COAUTH_B]->() RETURN count(r)")[0][0] == want > 0
    bad = ro("MATCH (a:Person)-[b:COAUTH_B]->(c:Person) OPTIONAL MATCH (a)-[r:COAUTH]->(c) "
             "WITH b, r WHERE r IS NULL OR b.recent_small_papers <> r.recent_small_papers "
             "OR b.works <> r.works OR NOT (c)-[:COAUTH_B]->(a) RETURN count(*)")[0][0]
    assert bad == 0


def test_work_scope_and_family_affiliations():
    rows = dict((r[0], r[1]) for r in ro("MATCH (w:Work) RETURN w.scope, count(w)"))
    assert set(rows) == {"funded", "family", "topic"}, rows
    fam = ro("MATCH (:Org {oa_id:'I1312732213'})-[:PARENT_OF*0..3]->(o:Org) WITH o "
             "MATCH ()-[r:AFFILIATED]->(o) RETURN count(DISTINCT r.work)")[0][0]
    assert fam >= 600, fam  # ~648 works in OpenAlex carry a family affiliation
    assert ro("MATCH (w:Work) WHERE w.doi = '' RETURN count(w)")[0][0] == 0
    assert ro("MATCH ()-[r:PARENT_OF]->() WHERE NOT r.rel IN ['subsidiary', 'bottler'] RETURN count(r)")[0][0] == 0


def test_indexes():
    idx = {(r[0], f, t) for r in ro("CALL db.indexes()") for f, ts in r[2].items() for t in ts}
    for want in [("Person", "oa_id", "RANGE"), ("Work", "oa_id", "RANGE"), ("Work", "doi", "RANGE"),
                 ("Work", "title", "FULLTEXT"), ("Org", "oa_id", "RANGE"),
                 ("Funder", "oa_id", "RANGE"), ("Topic", "oa_id", "RANGE")]:
        assert want in idx, want


def test_labels_and_schema():
    assert {r[0] for r in ro("CALL db.labels()")} == {"Person", "Work", "Org", "Funder", "Topic", "Meta"}
    assert {r[0] for r in ro("CALL db.relationshipTypes()")} == {
        "AUTHORED", "AFFILIATED", "FUNDED_BY", "PARENT_OF", "ABOUT", "COAUTH", "COAUTH_B"}
    p = ro("MATCH ()-[r:COAUTH]->() RETURN keys(r) LIMIT 1")[0][0]
    for k in ("n_papers", "w", "recency_w", "small_papers", "dois", "years", "works",
              "recent_small_papers", "recent_small_w"):
        assert k in p, k


def test_ids_unique():
    assert ro("MATCH (n) WHERE NOT n:Meta WITH labels(n)[0] AS l, n.oa_id AS id, count(*) AS c "
              "WHERE c > 1 RETURN count(*)")[0][0] == 0


def test_coauth_no_self_loops():
    assert ro("MATCH (a:Person)-[r:COAUTH]->(a) RETURN count(r)")[0][0] == 0


def test_coauth_one_edge_per_ordered_pair():
    assert ro("MATCH (a:Person)-[r:COAUTH]->(b:Person) WITH a, b, count(r) AS c WHERE c > 1 "
              "RETURN count(*)")[0][0] == 0


def test_coauth_symmetry_all_edges():
    bad = ro("""MATCH (a:Person)-[r:COAUTH]->(b:Person) OPTIONAL MATCH (b)-[s:COAUTH]->(a)
        WITH r, s WHERE s IS NULL OR r.n_papers <> s.n_papers OR r.small_papers <> s.small_papers
        OR r.recent_small_papers <> s.recent_small_papers OR abs(r.w - s.w) > 1e-9
        OR abs(r.recency_w - s.recency_w) > 1e-9 OR abs(r.recent_small_w - s.recent_small_w) > 1e-9
        OR r.dois <> s.dois OR r.years <> s.years OR r.works <> s.works
        RETURN count(*)""")[0][0]
    assert bad == 0, f"{bad} asymmetric COAUTH edges"


def test_coauth_parallel_lists_and_bounds():
    bad = ro("""MATCH ()-[r:COAUTH]->() WHERE size(r.dois) <> r.n_papers OR size(r.years) <> r.n_papers
        OR size(r.works) <> r.n_papers OR r.n_papers < 1 OR r.small_papers > r.n_papers
        OR r.recent_small_papers > r.small_papers OR r.w <= 0 OR r.w > r.n_papers + 1e-9
        OR r.recency_w > r.w + 1e-9 OR r.recent_small_w > r.recency_w + 1e-9
        RETURN count(r)""")[0][0]
    assert bad == 0, f"{bad} COAUTH edges break list/bound invariants"


def _recompute(a, b):
    ws = ro("MATCH (pa:Person {oa_id:$a})-[:AUTHORED]->(w:Work)<-[:AUTHORED]-(pb:Person {oa_id:$b}) "
            "RETURN w.oa_id, w.doi, w.year, w.n_authors", {"a": a, "b": b})
    exp = {"n_papers": 0, "w": 0.0, "small_papers": 0, "recency_w": 0.0,
           "recent_small_papers": 0, "recent_small_w": 0.0}
    triples = []
    for wid, doi, year, n in ws:
        assert n >= 2, f"COAUTH via {wid} with n_authors={n}"
        share, small, recent = 1 / (n - 1), n <= 25, (year or 0) >= 2021
        exp["n_papers"] += 1
        exp["w"] += share
        exp["small_papers"] += small
        exp["recency_w"] += share * recent
        exp["recent_small_papers"] += small and recent
        exp["recent_small_w"] += share * (small and recent)
        triples.append((wid, doi or "", year))
    return exp, sorted(triples)


def test_coauth_sampled_recompute():
    total = ro("MATCH ()-[r:COAUTH]->() RETURN count(r)")[0][0]
    rnd = random.Random(20261015)
    pairs = []
    for k in sorted(rnd.sample(range(total), 30)):
        pairs += ro("MATCH (a:Person)-[r:COAUTH]->(b:Person) RETURN a.oa_id, b.oa_id SKIP $k LIMIT 1", {"k": k})
    # plus edge cases: multi-paper pairs, recent small, truncated works, missing DOI
    pairs += ro("MATCH (a:Person)-[r:COAUTH]->(b:Person) WHERE r.n_papers >= 4 RETURN a.oa_id, b.oa_id LIMIT 5")
    pairs += ro("MATCH (a:Person)-[r:COAUTH]->(b:Person) WHERE r.recent_small_papers >= 2 RETURN a.oa_id, b.oa_id LIMIT 5")
    pairs += ro("MATCH (w:Work {authors_truncated:true})<-[:AUTHORED]-(a:Person) WITH w, a LIMIT 1 "
                "MATCH (w)<-[:AUTHORED]-(b:Person) WHERE b <> a RETURN a.oa_id, b.oa_id LIMIT 2")
    pairs += ro("MATCH (w:Work) WHERE w.doi IS NULL AND w.n_authors >= 2 WITH w LIMIT 1 "
                "MATCH (w)<-[:AUTHORED]-(a:Person), (w)<-[:AUTHORED]-(b:Person) WHERE a <> b "
                "RETURN a.oa_id, b.oa_id LIMIT 2")
    assert len(pairs) >= 40, len(pairs)
    for a, b in pairs:
        got = ro("MATCH (:Person {oa_id:$a})-[r:COAUTH]->(:Person {oa_id:$b}) RETURN properties(r)",
                 {"a": a, "b": b})
        assert len(got) == 1, (a, b, len(got))
        r = got[0][0]
        exp, triples = _recompute(a, b)
        for k in ("n_papers", "small_papers", "recent_small_papers"):
            assert r[k] == exp[k], (a, b, k, r[k], exp[k])
        for k in ("w", "recency_w", "recent_small_w"):
            assert abs(r[k] - exp[k]) < 1e-9, (a, b, k, r[k], exp[k])
        assert sorted(zip(r["works"], r["dois"], r["years"])) == triples, (a, b)


def test_no_coauth_from_single_author_works():
    # Every work on a sample of COAUTH edges has >= 2 authors (also checked in recompute).
    bad = ro("MATCH ()-[r:COAUTH]->() WITH r LIMIT 20000 UNWIND r.works AS wid "
             "MATCH (w:Work {oa_id:wid}) WHERE w.n_authors < 2 RETURN count(*)")[0][0]
    assert bad == 0


def test_funder_kinds():
    rows = ro("MATCH (f:Funder) WHERE f.target OR f.kind IS NOT NULL RETURN f.oa_id, f.kind, f.target")
    assert sorted(rows) == sorted([[COMPANY, "company", True], [FOUNDATION, "foundation", True]]), rows


def test_parent_of_family():
    rows = ro("MATCH (a:Org)-[:PARENT_OF]->(b:Org) RETURN a.oa_id, b.oa_id")
    assert sorted(rows) == sorted([["I1312732213", "I4405274843"], ["I1312732213", "I4405273583"]])
    fam = ro("MATCH (:Org {oa_id:'I1312732213'})-[:PARENT_OF*0..3]->(o:Org) RETURN o.oa_id")
    assert {r[0] for r in fam} == set(loader.COKE_FAMILY)
    assert {r[0] for r in ro("MATCH (o:Org) WHERE o.target RETURN o.oa_id")} == set(loader.COKE_FAMILY)


def test_funded_works_complete_author_lists():
    # Tier A must not rest on truncated lists: no funded work is flagged or sits at exactly 100.
    assert ro("MATCH (w:Work)-[:FUNDED_BY]->(:Funder {target:true}) "
              "WHERE w.authors_truncated OR w.n_authors = 100 RETURN count(DISTINCT w)")[0][0] == 0
    assert ro("MATCH (w:Work)-[:FUNDED_BY]->(:Funder {target:true}) RETURN count(DISTINCT w)")[0][0] == 817
    # 820 funded works in OpenAlex; round 2 merges 3 same-paper duplicates (same title, year and
    # authors under two DOIs: a reprint, an F1000 v1/v2, a duplicated peer-review report).


def test_truncated_flag_consistent():
    assert ro("MATCH (w:Work {authors_truncated:true}) WHERE w.n_authors < 100 RETURN count(w)")[0][0] == 0


def test_profile_flags_live():
    rows = dict((r[0], r[1]) for r in ro("MATCH (p:Person) RETURN p.profile_flag, count(p)"))
    assert set(rows) <= {"ok", "no_orcid", "works_count_anomaly"}, rows
    assert ro("MATCH (p:Person) WHERE (p.works_count > 2000) <> (p.profile_flag = 'works_count_anomaly') "
              "RETURN count(p)")[0][0] == 0


def test_doi_format():
    assert ro("MATCH (w:Work) WHERE w.doi IS NOT NULL AND (NOT w.doi STARTS WITH '10.' "
              "OR w.doi <> toLower(w.doi)) RETURN count(w)")[0][0] == 0


def test_no_duplicate_doi_works():
    # The same DOI under two oa_ids double-counts n_papers on COAUTH.
    dup = ro("MATCH (w:Work) WHERE w.doi IS NOT NULL AND w.doi <> '' WITH w.doi AS d, count(*) AS c "
             "WHERE c > 1 RETURN count(d)")[0][0]
    assert dup == 0, f"{dup} DOIs appear on more than one Work"


def test_topic_works_have_about():
    # Every work that came only from the primary_topic filter must have an ABOUT edge.
    # (Round 2: family-affiliation works of any topic are loaded too, so scope it by Work.scope.)
    assert ro("MATCH (w:Work) WHERE w.scope = 'topic' AND NOT (w)-[:FUNDED_BY]->(:Funder {target:true}) "
              "AND NOT (w)-[:ABOUT]->(:Topic) RETURN count(w)")[0][0] == 0


def test_gate_dated_tier_a_path_with_doi():
    rows = ro("""MATCH (p:Person)-[:AUTHORED]->(w:Work)-[:FUNDED_BY]->(f:Funder {kind:'company'})
        WHERE w.n_authors <= 25 AND w.doi IS NOT NULL AND w.year IS NOT NULL
        RETURN p.oa_id, w.doi, w.year, f.name LIMIT 5""")
    assert rows and all(r[1].startswith("10.") and r[2] for r in rows), rows
    aff = ro("""MATCH (c:Org {oa_id:'I1312732213'})-[:PARENT_OF*0..3]->(o:Org) WITH o
        MATCH (p:Person)-[r:AFFILIATED]->(o) WITH p, o, r.doi AS doi, r.year AS year
        WHERE doi IS NOT NULL RETURN p.oa_id, o.name, doi, year LIMIT 5""")
    assert aff and all(r[2].startswith("10.") and r[3] for r in aff), aff


def test_ro_query_rejects_write():
    try:
        G().ro_query("CREATE (:ShouldNotExist)")
    except Exception as e:
        assert "read-only" in str(e).lower() or "read only" in str(e).lower(), e
    else:
        raise AssertionError("ro_query accepted a write")
    assert ro("MATCH (n:ShouldNotExist) RETURN count(n)")[0][0] == 0


# ---------- runner ----------
if __name__ == "__main__":
    tests = [(n, f) for n, f in list(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception as e:
            failed += 1
            msg = str(e) or type(e).__name__
            line = traceback.extract_tb(e.__traceback__)[-1].lineno
            print(f"FAIL  {name}  (line {line}): {msg[:300]}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
