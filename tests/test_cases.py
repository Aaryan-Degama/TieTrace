"""Tests for checkpoints 5c-5d: case config, staging + RENAME swap, case-aware research and server.

Run:  FALKORDB_PORT=6379 uv run python tests/test_cases.py
Scratch graphs are named research:zz-test-* and are deleted at the end. No API calls.
The handmade works below are test fixtures (the only place synthetic data is allowed).
"""
import copy
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["TIETRACE_NEWSROOM"] = "zz-test-cases"  # server.py exits without it; scratch, deleted at the end
import loader  # noqa: E402
import research  # noqa: E402
import server  # noqa: E402
from falkordb import FalkorDB  # noqa: E402

DB = FalkorDB(port=int(os.environ.get("FALKORDB_PORT", 6379)))
A, B = "research:zz-test-a", "research:zz-test-b"
GEBN = {"A5031318756", "A5105003630", "A5046953968", "A5045252457"}


# ---------- fixtures ----------
def work(wid, authors, funders=(), year=2022):
    return {"id": f"https://openalex.org/{wid}", "doi": f"https://doi.org/10.9/{wid}", "title": f"title {wid}",
            "publication_year": year, "primary_topic": {"id": "https://openalex.org/T10010"},
            "authorships": [{"author": {"id": f"https://openalex.org/{a}", "display_name": f"Person {a}",
                                        "orcid": None}, "author_position": "middle", "institutions": []}
                            for a in authors],
            "funders": [{"id": f"https://openalex.org/{f}", "display_name": f} for f in funders],
            "topics": [{"id": "https://openalex.org/T10010"}]}


def build(graph, works, fail=False):
    """Build `graph` through the real write_graph (staging + self-check + RENAME)."""
    rows = loader.build_rows({loader.short(w["id"]): w for w in works}, set(), {})
    meta = {"graph": graph, "data_version": f"test@{graph}", "years": [2021, 2026], "recent_year": 2021,
            "small_paper": 25, "load_scope": "test fixture", "truncated_works": 0, **rows["stats"]}
    old_graph, old_check = loader.GRAPH, loader.self_check
    loader.GRAPH = graph
    if fail:
        def broken(g, counts):
            raise RuntimeError("simulated self-check failure")
        loader.self_check = broken
    try:
        return loader.write_graph(rows, meta, rebuild=True)
    finally:
        loader.GRAPH, loader.self_check = old_graph, old_check


def drop(*graphs):
    for g in graphs:
        for name in (g, loader.staging_name(g)):
            if name in DB.list_graphs():
                DB.select_graph(name).delete()


def raises(code, fn, *args, **kw):
    try:
        fn(*args, **kw)
    except research.ToolError as e:
        assert e.code == code, (e.code, e.message)
        return e
    raise AssertionError(f"no {code}")


# ---------- 5c: config ----------
def test_obesity_coke_config_equals_checkpoint2_constants():
    assert loader.GRAPH == "research:obesity-coke" and loader.SLUG == "obesity-coke"
    assert loader.FUNDER_KIND == {"F4320307807": "company", "F4320308599": "foundation"}
    assert (loader.COMPANY_FUNDER, loader.FOUNDATION_FUNDER) == ("F4320307807", "F4320308599")
    assert loader.TOPICS == {"T10010": "Obesity, Physical Activity, Diet", "T10352": "Physical Activity and Health"}
    assert list(loader.TOPIC_YEARS) == list(range(2010, 2027))
    assert (loader.TOPIC_CAP, loader.PER_YEAR) == (30000, 1764)
    assert loader.COKE_PARENT == "I1312732213"
    assert list(loader.COKE_FAMILY) == ["I1312732213", "I4405274843", "I4405273583"]
    assert loader.COKE_CHILD_REL == {"I4405274843": "bottler", "I4405273583": "bottler"}
    assert (loader.SMALL_PAPER, loader.RECENT_YEAR, loader.WORKS_COUNT_ANOMALY) == (25, 2021, 2000)
    assert set(loader.load_config("obesity-coke")["public_ids"]) == GEBN
    # the download filters are the checkpoint-2 strings, so the data/raw/ cache still matches
    q = {name: p["filter"] for name, p, _ in loader.work_queries()}
    assert q["funded"] == "funders.id:F4320307807|F4320308599"
    assert q["family"] == "authorships.institutions.lineage:I1312732213|I4405274843|I4405273583"
    assert q["topic_2015"] == "primary_topic.id:T10010|T10352,publication_year:2015"


def test_invalid_configs_are_rejected():
    good = loader.load_config("obesity-coke")
    loader.check_config(copy.deepcopy(good), "obesity-coke")  # the real one passes
    edits = {
        "unknown key": lambda c: c.update(graph="research:x"),
        "missing key": lambda c: c.pop("max_api_calls"),
        "slug mismatch": lambda c: c.update(slug="other-case"),
        "21 public ids": lambda c: c.update(public_ids=[f"A{i}" for i in range(21)]),
        "21 topics": lambda c: c.update(topics=[{"id": f"T{i}", "name": "t"} for i in range(21)]),
        "bad funder kind": lambda c: c["funders"][0].update(kind="owner"),
        "extra item key": lambda c: c["funders"][0].update(note="x"),
        "bad id": lambda c: c["topics"][0].update(id="X1; MATCH (n)"),
        "duplicate id": lambda c: c["topics"].append(dict(c["topics"][0])),
        "two parents": lambda c: c["institutions"][1].update(rel="parent"),
        "decision unnamed": lambda c: c["decided_by"].pop("recent_year"),
        "decision bad format": lambda c: c["decided_by"].update(topics="someone"),
        "years reversed": lambda c: c.update(years=[2026, 2010]),
        "recent_year outside years": lambda c: c.update(recent_year=2030),
        "bool as int": lambda c: c.update(small_paper=True),
    }
    for why, edit in edits.items():
        cfg = copy.deepcopy(good)
        edit(cfg)
        try:
            loader.check_config(cfg, "obesity-coke")
        except ValueError:
            continue
        raise AssertionError(f"accepted a config with {why}")
    for slug in ("../x", "RESEARCH:X", "newsroom:alpha", "research:obesity-coke", "a", "", "-x", "x" * 41, None):
        try:
            loader.load_config(slug)
        except ValueError:
            continue
        raise AssertionError(f"accepted slug {slug!r}")


def test_config_file_over_20_kb_is_rejected():
    old = loader.CASES
    try:
        loader.CASES = Path(tempfile.mkdtemp())
        (loader.CASES / "big-case").mkdir()
        cfg = {**json.loads((old / "obesity-coke" / "case.json").read_text()), "slug": "big-case", "notes": "x" * 20_000}
        (loader.CASES / "big-case" / "case.json").write_text(json.dumps(cfg))
        try:
            loader.load_config("big-case")
        except ValueError as e:
            assert "20000" in str(e), e
        else:
            raise AssertionError("accepted a 20 KB config")
    finally:
        loader.CASES = old


# ---------- 5c: staging + swap ----------
def test_swap_replaces_the_graph_and_a_failed_build_keeps_the_old_one():
    drop(A)
    try:
        build(A, [work("W1", ["P1", "P2"], funders=["F4320307807"])])
        assert A in DB.list_graphs() and loader.staging_name(A) not in DB.list_graphs()
        meta = DB.select_graph(A).ro_query("MATCH (m:Meta) RETURN m.build_status, m.graph").result_set
        assert meta == [["complete", A]], meta
        before = DB.select_graph(A).ro_query("MATCH (p:Person) RETURN p.oa_id ORDER BY p.oa_id").result_set
        try:
            build(A, [work("W9", ["P7", "P8", "P9"])], fail=True)
        except RuntimeError:
            pass
        else:
            raise AssertionError("the failing build did not raise")
        after = DB.select_graph(A).ro_query("MATCH (p:Person) RETURN p.oa_id ORDER BY p.oa_id").result_set
        assert after == before == [["P1"], ["P2"]], (before, after)
        assert loader.staging_name(A) not in DB.list_graphs(), "staging left behind"
        build(A, [work("W9", ["P7", "P8", "P9"])])  # a good build replaces it, indexes included
        g = DB.select_graph(A)
        assert g.ro_query("MATCH (p:Person) RETURN count(p)").result_set == [[3]]
        assert "Index" in str(g.explain("MATCH (p:Person) WHERE p.oa_id = 'P7' RETURN p"))
    finally:
        drop(A)


# ---------- 5d: research per graph ----------
def test_caches_are_kept_per_graph_and_cypher_names_the_graph():
    drop(A, B)
    try:
        build(A, [work("W1", ["P1", "P2"], funders=["F4320307807"])])  # P1 is Tier A in A
        build(B, [work("W1", ["P1", "P2"])])                           # and nowhere in B
        research.clear_cache()
        with research.on_graph(A):
            ta = research._tier_a([])
        with research.on_graph(B):
            tb = research._tier_a([])
            out = research.tie_report("P1")
        assert ta == {"P1": "A1", "P2": "A1"} and tb == {}, (ta, tb)
        assert out["ok"] and out["data"]["tier"] == "NONE", out
        assert {c["graph"] for c in out["cypher"]} == {B}, out["cypher"]
        assert (out["scope"]["graph"], out["scope"]["data_version"]) == (B, f"test@{B}"), out["scope"]
        research.clear_cache(A)
        assert {k[0] for k in research._cache} == {B}, list(research._cache)
        assert research.current_graph() == research.GRAPH  # the with block was left
    finally:
        research.clear_cache()
        drop(A, B)


def test_ro_query_guard_and_no_graph():
    before = set(DB.list_graphs())
    for g in ("newsroom:alpha", "research:../x", "RESEARCH:X", "staging:obesity-coke", "research:a", "research:x y"):
        raises("INVALID_ARG", research.ro_query, g, "MATCH (n) RETURN count(n)")
    e = raises("NO_GRAPH", research.ro_query, "research:zz-test-missing", "MATCH (n) RETURN count(n)")
    assert "obesity-coke" in e.message, e.message
    assert set(DB.list_graphs()) == before


# ---------- 5d: server ----------
def test_server_case_argument_and_list_cases():
    before = set(DB.list_graphs())
    for bad in ("newsroom:alpha", "../x", "RESEARCH:X", "research:obesity-coke", 5, ""):
        out = server.tie_report("A5031318756", 1, case=bad)
        assert out["ok"] is False and out["error"]["code"] == "INVALID_ARG", (bad, out)
    out = server.find_experts("obesity", 2, True, case="zz-not-a-case")
    assert out["error"]["code"] == "NO_GRAPH" and "obesity-coke" in out["error"]["message"], out
    assert set(DB.list_graphs()) == before
    out = server.tie_report("A5031318756", 1, case="obesity-coke")
    assert out["ok"] and out["scope"]["graph"] == "research:obesity-coke" and out["scope"]["data_version"], out["scope"]
    assert all(c["graph"] == "research:obesity-coke" for c in out["cypher"])
    cases = server.list_cases()
    assert cases["ok"], cases
    row = next(c for c in cases["data"]["cases"] if c["case"] == "obesity-coke")
    assert row["graph_exists"] and row["registered"] and row["meta"]["data_version"], row
    assert not any(c["graph"].startswith("newsroom") for c in cases["data"]["cases"])
    # public_case honours the case's public_ids (the GEBN list for obesity-coke)
    out = server.check_case(sorted(GEBN), True, case="obesity-coke")
    assert out["ok"] and not any(r["label"].startswith("Expert #") for r in out["data"]["results"]), out["data"]


# ---------- Reviewer tests (checkpoints 5c-5d review, Oct 16) ----------
def snapshot(*dirs):
    """{path: (size, mtime_ns)} of every file under the dirs: proves that nothing was written."""
    return {str(p): (p.stat().st_size, p.stat().st_mtime_ns) for d in dirs if d.exists()
            for p in sorted(d.rglob("*")) if p.is_file()}


def test_failed_build_keeps_old_meta_and_indexes():
    drop(A)
    try:
        build(A, [work("W1", ["P1", "P2"], funders=["F4320307807"])])
        g = DB.select_graph(A)
        meta = g.ro_query("MATCH (m:Meta) RETURN properties(m)").result_set
        for bad in ([work("W9", ["P7"])], []):  # self-check fails; then an empty build (no Person) fails for real
            try:
                build(A, bad, fail=bool(bad))
            except RuntimeError:
                pass
            else:
                raise AssertionError("a failing build did not raise")
            assert g.ro_query("MATCH (m:Meta) RETURN properties(m)").result_set == meta
            assert "Index" in str(g.explain("MATCH (p:Person) WHERE p.oa_id = 'P1' RETURN p")), "old index lost"
            assert "Index" in str(g.explain("MATCH (w:Work) WHERE w.oa_id = 'W1' RETURN w")), "old index lost"
            assert loader.staging_name(A) not in DB.list_graphs(), "staging left behind"
    finally:
        drop(A)


def test_leftover_staging_from_a_crash_is_replaced_not_merged():
    drop(A)
    try:
        st = DB.select_graph(loader.staging_name(A))
        st.query("CREATE (:Person {oa_id: 'GHOST'}), (:Meta {build_status: 'complete'})")  # a killed build
        build(A, [work("W1", ["P1", "P2"])])
        rows = DB.select_graph(A).ro_query("MATCH (p:Person) RETURN p.oa_id ORDER BY p.oa_id").result_set
        assert rows == [["P1"], ["P2"]], rows
        assert DB.select_graph(A).ro_query("MATCH (m:Meta) RETURN count(m)").result_set == [[1]]
    finally:
        drop(A)


def test_dry_run_writes_nothing_and_counts_calls_right():
    import contextlib
    import io
    calls = []

    def fake_get(path, params):
        calls.append(params)
        if "group_by" in params:
            return {"group_by": [{"key": str(y), "count": 5000} for y in loader.TOPIC_YEARS]}
        return {"meta": {"count": {"funded": 820, "family": 648}[
            "funded" if params["filter"].startswith("funders") else "family"]}}

    before, graphs = snapshot(loader.CASES, loader.RAW), set(DB.list_graphs())
    old_get, old_raw = loader.get, loader.RAW
    try:
        loader.get = fake_get
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            loader.dry_run()  # obesity-coke: every list query is in data/raw/, so 0 calls expected
        assert "expected API list calls: about 0 " in out.getvalue(), out.getvalue()
        assert all(p.get("per_page") == 1 for p in calls if "group_by" not in p), calls
        loader.RAW = Path(tempfile.mkdtemp()) / "raw"  # nothing cached: 9 + 7 + 17 * 18 pages + 1 group_by + authors
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            loader.dry_run()
        works = 820 + 648 + 17 * 1764
        want = 9 + 7 + 17 * 18 + 1 + round(works * loader.PERSONS_PER_WORK / 100)
        assert f"expected API list calls: about {want} " in out.getvalue(), (want, out.getvalue())
        assert not loader.RAW.exists(), "dry run created the cache folder"
    finally:
        loader.get, loader.RAW = old_get, old_raw
    assert snapshot(loader.CASES, loader.RAW) == before, "dry run wrote a file"
    assert set(DB.list_graphs()) == graphs


def test_max_api_calls_stops_before_the_network():
    import httpx

    def no_network(*a, **kw):
        raise AssertionError("httpx.get was called past max_api_calls")

    old = (loader.MAX_API_CALLS, loader.api_calls, httpx.get)
    try:
        loader.MAX_API_CALLS, loader.api_calls, httpx.get = 3, 3, no_network
        try:
            loader.get("works", {"filter": "x"})
        except SystemExit as e:
            assert "max_api_calls" in str(e), e
        else:
            raise AssertionError("no stop at max_api_calls")
        assert loader.api_calls == 3
    finally:
        loader.MAX_API_CALLS, loader.api_calls, httpx.get = old


def test_status_json_lifecycle():
    old = (loader.CASES, loader.SLUG)
    try:
        loader.CASES, loader.SLUG = Path(tempfile.mkdtemp()), "zz-status"
        (loader.CASES / "zz-status").mkdir()
        path = loader.CASES / "zz-status" / "status.json"
        loader.write_status("building")
        assert json.loads(path.read_text())["status"] == "building"
        loader.write_status("ready", built_at="b1", data_version="openalex@x", counts={"Person": 1}, graph=A)
        loader.write_status("building")
        loader.write_status("failed", error="RuntimeError: boom")
        s = json.loads(path.read_text())
        assert (s["status"], s["built_at"], s["counts"], s["error"]) == ("failed", "b1", {"Person": 1}, "RuntimeError: boom"), s
        loader.write_status("ready", built_at="b2")
        s = json.loads(path.read_text())
        assert s["status"] == "ready" and s["built_at"] == "b2" and "error" not in s, s
        assert [p.name for p in path.parent.iterdir()] == ["status.json"], "temp file left behind"
    finally:
        loader.CASES, loader.SLUG = old


def with_cases(fn, public_a=()):
    """Run fn with loader.CASES = a temp registry: obesity-coke, zz-test-a/-b (graphs A, B), zz-test-c (no graph)."""
    real = json.loads((loader.CASES / "obesity-coke" / "case.json").read_text())
    old = loader.CASES
    tmp = Path(tempfile.mkdtemp())
    for slug, pub in (("obesity-coke", real["public_ids"]), ("zz-test-a", list(public_a)), ("zz-test-b", []),
                      ("zz-test-c", [])):
        (tmp / slug).mkdir()
        (tmp / slug / "case.json").write_text(json.dumps({**real, "slug": slug, "public_ids": pub}))
    try:
        loader.CASES = tmp
        return fn(tmp)
    finally:
        loader.CASES = old
        research.clear_cache()


def test_server_cases_do_not_leak_into_each_other():
    drop(A, B)
    try:
        build(A, [work("W1", ["P1", "P2"], funders=["F4320307807"])])
        build(B, [work("W1", ["P1", "P2"])])

        def run(tmp):
            research.clear_cache()
            for case, graph, tier in (("zz-test-a", A, "A1"), ("zz-test-b", B, "NONE"), ("zz-test-a", A, "A1"),
                                      ("zz-test-b", B, "NONE")):
                out = server.tie_report("P1", 1, case=case)
                assert out["ok"] and out["data"]["tier"] == tier, (case, out)
                assert {c["graph"] for c in out["cypher"]} == {graph} and out["scope"]["graph"] == graph, case
                assert out["scope"]["data_version"] == f"test@{graph}", out["scope"]
            os.environ["TIETRACE_CASE"] = "zz-test-b"
            try:
                out = server.tie_report("P1", 1)  # no case: the env default
                assert out["ok"] and out["scope"]["graph"] == B and out["data"]["tier"] == "NONE", out
            finally:
                del os.environ["TIETRACE_CASE"]
            assert research.current_graph() == research.GRAPH
        with_cases(run)
    finally:
        drop(A, B)


def test_public_ids_are_per_case_and_never_borrowed():
    drop(A)
    gebn_id = "A5031318756"  # public in obesity-coke, not in zz-test-a unless that case lists it
    try:
        build(A, [work("W1", [gebn_id, "P2"], funders=["F4320307807"])])

        def labels(tmp):
            out = server.check_case([gebn_id], True, case="zz-test-a")
            assert out["ok"], out
            return [r["label"] for r in out["data"]["results"]], out["warnings"]
        got, warn = with_cases(labels)
        assert got == ["Expert #1"] and any("public_case ignored" in w for w in warn), (got, warn)
        got, warn = with_cases(labels, public_a=[gebn_id])
        assert got == [f"Person {gebn_id}"], got
        # public_case=false never names, even for an approved id
        out = with_cases(lambda t: server.check_case([gebn_id], False, case="zz-test-a"), public_a=[gebn_id])
        assert [r["label"] for r in out["data"]["results"]] == ["Expert #1"], out
        assert "Person " not in json.dumps(out), "a name leaked"
    finally:
        drop(A)


def test_server_rejects_slug_lookalikes_and_unbuilt_cases():
    before = set(DB.list_graphs())
    for bad in ("obesity‐coke", "obesity-coke\n", " obesity-coke", "obesity-cokе", "x" * 41, "-x",
                "obesity_coke", "Obesity-coke", "staging:obesity-coke", "research:", ["obesity-coke"]):
        out = server.find_experts("obesity", 2, True, case=bad)
        assert out["ok"] is False and out["error"]["code"] == "INVALID_ARG", (bad, out)
        assert out["cypher"] == [], bad

    def unbuilt(tmp):
        out = server.tie_report("P1", 1, case="zz-test-c")  # registered, never built
        assert out["ok"] is False and out["error"]["code"] == "NO_GRAPH", out
        assert "zz-test-c" in out["error"]["message"] and str(tmp) not in out["error"]["message"], out
        out = server.check_case(["P1", "P2"], False, case="zz-test-c")
        assert out["ok"] is False and out["error"]["code"] == "NO_GRAPH", out
    with_cases(unbuilt)
    assert set(DB.list_graphs()) == before, "a read created a graph"


def test_list_cases_hides_staging_and_newsroom_and_survives_odd_status():
    drop(A)
    nr = "newsroom:zz-test"
    try:
        build(A, [work("W1", ["P1", "P2"])])
        DB.select_graph(loader.staging_name(A)).query("CREATE (:Person {oa_id: 'X'})")
        DB.select_graph(nr).query("CREATE (:Note {text: 'secret'})")

        def run(tmp):
            (tmp / "zz-test-a" / "status.json").write_text(json.dumps({"updated_at": "x"}))  # no "status" key
            (tmp / "zz-test-b" / "status.json").write_text("[1, 2]")
            return server.list_cases()
        out = with_cases(run)
        assert out["ok"], out
        names = {c["graph"] for c in out["data"]["cases"]}
        assert A in names and not any(not n.startswith("research:") for n in names), names
        text = json.dumps(out)
        assert "staging:" not in text and "newsroom" not in text and "secret" not in text
        row = next(c for c in out["data"]["cases"] if c["case"] == "zz-test-a")
        assert row["graph_exists"] and row["registered"] and row["status"] is None, row
        assert any("zz-test-a" in w for w in out["warnings"]), out["warnings"]  # the leftover staging graph
        row = next(c for c in out["data"]["cases"] if c["case"] == "zz-test-c")
        assert row["registered"] and not row["graph_exists"] and row["meta"] is None, row
    finally:
        drop(A)
        if nr in DB.list_graphs():
            DB.select_graph(nr).delete()


# ---------- review fixes (Builder, Oct 16): junk flag, build lock, stale cache ----------
def test_junk_is_precomputed_and_queries_never_lower_titles():
    assert "toLower" not in research.NOT_JUNK and "toLower" not in research.FUNDED_EVIDENCE_Q
    g = DB.select_graph("research:obesity-coke")
    m = g.ro_query("MATCH (m:Meta) RETURN m.junk_works + m.non_paper_works, m.work_junk_flag").result_set[0]
    n = g.ro_query("MATCH (w:Work) WHERE w.junk RETURN count(w)").result_set[0][0]
    assert m == [n, True], (m, n)
    assert g.ro_query("MATCH (w:Work) WHERE w.junk IS NULL RETURN count(w)").result_set[0][0] == 0


def test_second_build_of_a_case_is_refused_by_the_lock():
    # A temp case and a stubbed build: even if the lock check broke, nothing real is rebuilt or overwritten.
    real = json.loads((loader.CASES / "obesity-coke" / "case.json").read_text())
    tmp = Path(tempfile.mkdtemp())
    (tmp / "zz-lock2").mkdir()
    (tmp / "zz-lock2" / "case.json").write_text(json.dumps({**real, "slug": "zz-lock2"}))
    lock = "build-lock:zz-lock2"
    assert DB.connection.set(lock, "someone-else", nx=True, ex=60), "a build of zz-lock2 is running?"
    old = (loader.CASES, sys.argv, loader.api_key, loader.build)

    def must_not_build(args):
        raise AssertionError("the build ran although another build holds the lock")
    try:
        loader.CASES, sys.argv, loader.api_key = tmp, ["loader.py", "--case", "zz-lock2", "--rebuild"], lambda: "x"
        loader.build = must_not_build
        try:
            loader.main()
        except SystemExit as e:
            assert "another build" in str(e), e
        else:
            raise AssertionError("second build was not refused")
        assert DB.connection.get(lock) in ("someone-else", b"someone-else"), "the lock of the other build was released"
        assert not (tmp / "zz-lock2" / "status.json").exists(), "a refused build wrote status.json"
    finally:
        loader.CASES, sys.argv, loader.api_key, loader.build = old
        DB.connection.delete(lock)
        loader.use_case("obesity-coke")


def test_list_cases_says_build_in_progress_while_the_lock_is_held():
    st = "staging:zz-test-a"
    try:
        DB.select_graph(st).query("CREATE (:Person {oa_id: 'X'})")
        DB.connection.set("build-lock:zz-test-a", "t", ex=60)
        out = with_cases(lambda tmp: server.list_cases())
        assert any("zz-test-a: build in progress" in w for w in out["warnings"]), out["warnings"]
        DB.connection.delete("build-lock:zz-test-a")
        out = with_cases(lambda tmp: server.list_cases())
        assert any("zz-test-a: an unfinished build" in w for w in out["warnings"]), out["warnings"]
    finally:
        DB.connection.delete("build-lock:zz-test-a")
        if st in DB.list_graphs():
            DB.select_graph(st).delete()


def test_server_drops_a_graph_cache_when_status_json_changes():
    old = loader.CASES
    try:
        loader.CASES = Path(tempfile.mkdtemp())
        (loader.CASES / "zz-test-a").mkdir()
        status = loader.CASES / "zz-test-a" / "status.json"
        status.write_text(json.dumps({"status": "ready", "built_at": "t1", "config_hash": "h"}))
        research._seen_build.clear()  # earlier tests may have looked at zz-test-a already
        research._cache[(A, "meta")] = ({"x": 1}, [])
        research._cache[(B, "meta")] = ({"x": 2}, [])
        research.drop_cache_if_rebuilt("zz-test-a")           # first look: remembered, nothing dropped
        assert (A, "meta") in research._cache
        status.write_text(json.dumps({"status": "ready", "built_at": "t2", "config_hash": "h"}))
        research.drop_cache_if_rebuilt("zz-test-a")
        assert (A, "meta") not in research._cache and (B, "meta") in research._cache
    finally:
        loader.CASES = old
        research.clear_cache()
        research._seen_build.clear()


# ---------- Reviewer tests, round 2 (Oct 16) ----------
OLD_NOT_JUNK = ("NOT (coalesce(w.non_paper, false) OR coalesce(w.doi,'') STARTS WITH $junk_doi "
                "OR any(pre IN $junk_titles WHERE toLower(coalesce(w.title,'')) STARTS WITH pre))")
OLD_JUNK_PARAMS = {"junk_doi": loader.JUNK_DOI_PREFIX, "junk_titles": list(loader.JUNK_TITLE_PREFIXES)}


def test_load_time_junk_flag_equals_the_old_cypher_tolower_rule():
    """Work.junk (Python, at load) must agree with the checkpoint-3 Cypher rule, title by title, on tricky
    titles and on every Work of the real graph."""
    titles = ["CORRECTION: x", "Erratum to x", "  Front Matter  ", "INDEX", "Index of x", None, "",
              "Peer Review Report For: x", "Notice Of Retraction x", "�correction: x", "İndex",
              "Korrigendum", "Corrigendum", "Obesity and correction: x", "Corrections: x", "front matter"]
    works = [work(f"W{i}", ["P1", "P2"]) for i in range(len(titles))]
    for w, t in zip(works, titles):
        w["title"] = t
    works.append({**work("W99", ["P1", "P2"]), "doi": loader.JUNK_DOI_PREFIX.upper() + "abc"})
    drop(A)
    try:
        build(A, works)
        rows = DB.select_graph(A).ro_query(f"MATCH (w:Work) RETURN w.oa_id, w.title, w.junk, {OLD_NOT_JUNK}",
                                           OLD_JUNK_PARAMS).result_set
        assert len(rows) == len(works), rows
        bad = [r for r in rows if r[2] is not (not r[3])]
        assert not bad, bad
        assert all(isinstance(r[2], bool) for r in rows), rows
    finally:
        drop(A)
    g = DB.select_graph("research:obesity-coke")  # the real graph: no Work where the two rules differ
    diff = g.ro_query(f"MATCH (w:Work) WHERE coalesce(w.junk, true) = ({OLD_NOT_JUNK}) RETURN count(w)",
                      OLD_JUNK_PARAMS, timeout=60000).result_set[0][0]
    assert diff == 0, diff


def test_graph_without_junk_flag_is_refused_not_silently_empty():
    drop(A)
    try:
        DB.select_graph(A).query("CREATE (:Meta {years: [2021, 2026], recent_year: 2021, small_paper: 25, "
                                 "data_version: 'x'}), (:Person {oa_id: 'P1'})")
        research.clear_cache()
        with research.on_graph(A):
            out = research.tie_report("P1")
        assert out["ok"] is False and out["error"]["code"] == "DB_ERROR" and "rebuild" in out["error"]["message"], out
    finally:
        research.clear_cache()
        drop(A)


def test_build_lock_is_taken_with_ttl_and_released_on_success_and_failure():
    real = json.loads((loader.CASES / "obesity-coke" / "case.json").read_text())
    old = (loader.CASES, sys.argv, loader.api_key, loader.build)
    tmp = Path(tempfile.mkdtemp())
    (tmp / "zz-lock").mkdir()
    (tmp / "zz-lock" / "case.json").write_text(json.dumps({**real, "slug": "zz-lock"}))
    lock, seen = "build-lock:zz-lock", []
    meta = {"built_at": "b", "data_version": "d", "config_hash": "h"}

    def fake_build(fail):
        def run(args):
            seen.append((DB.connection.get(lock), DB.connection.ttl(lock)))
            if fail:
                raise RuntimeError("boom")
            return {}, None, meta
        return run
    try:
        loader.CASES, sys.argv, loader.api_key = tmp, ["loader.py", "--case", "zz-lock", "--rebuild"], lambda: "x"
        for fail in (False, True):
            loader.build = fake_build(fail)
            try:
                loader.main()
            except RuntimeError:
                assert fail
            assert seen[-1][0] is not None and 0 < seen[-1][1] <= loader.BUILD_LOCK_TTL, seen
            assert DB.connection.get(lock) is None, f"lock not released (fail={fail})"
            s = json.loads((tmp / "zz-lock" / "status.json").read_text())
            assert s["status"] == ("failed" if fail else "ready"), s
    finally:
        loader.CASES, sys.argv, loader.api_key, loader.build = old
        DB.connection.delete(lock)
        loader.use_case("obesity-coke")
    assert loader.CASES == old[0] and loader.GRAPH == "research:obesity-coke"


def test_server_answers_from_the_new_graph_after_a_swap():
    drop(A)
    try:
        coauth = [work("W2", ["P1", "P3"]), work("W3", ["P1", "P3"])]
        build(A, [work("W1", ["P1"], funders=["F4320307807"])] + coauth)

        def run(tmp):
            research.clear_cache()
            research._seen_build.clear()
            status = tmp / "zz-test-a" / "status.json"
            status.write_text(json.dumps({"status": "ready", "built_at": "t1", "config_hash": "h"}))
            assert server.tie_report("P1", 1, case="zz-test-a")["data"]["tier"] == "A1"
            # P3's answer depends on the CACHED Tier-A map, so a stale cache would still say TIE_FOUND
            assert server.check_no_tie("P3", 3, 5, 1, case="zz-test-a")["data"]["result"] == "TIE_FOUND"
            build(A, [work("W1", ["P1"])] + coauth)  # swapped in: P1 is no longer Tier A
            status.write_text(json.dumps({"status": "ready", "built_at": "t2", "config_hash": "h"}))
            out = server.tie_report("P1", 1, case="zz-test-a")
            assert out["ok"] and out["data"]["tier"] == "NONE", out["data"]
            out = server.check_no_tie("P3", 3, 5, 1, case="zz-test-a")
            assert out["ok"] and out["data"]["result"] == "NO_TIE_FOUND", out["data"]
        with_cases(run)
    finally:
        research._seen_build.clear()
        drop(A)


# ---------- Reviewer tests, round 3 (Oct 16) ----------
def test_before_swap_runs_after_the_self_check_and_before_rename():
    drop(A)
    seen = []
    try:
        build(A, [work("W1", ["P1", "P2"])])
        old_built = DB.select_graph(A).ro_query("MATCH (p:Person) RETURN count(p)").result_set

        def record(meta):  # at this moment the live graph must still be the old one, and staging complete
            seen.append((meta["build_status"], DB.select_graph(A).ro_query("MATCH (p:Person) RETURN count(p)").result_set,
                         loader.staging_name(A) in DB.list_graphs()))
        rows = loader.build_rows({"W9": work("W9", ["P7", "P8", "P9"])}, set(), {})
        meta = {"graph": A, "data_version": "d", "years": [2021, 2026], "recent_year": 2021, "small_paper": 25,
                "load_scope": "x", "truncated_works": 0, **rows["stats"]}
        old_graph, old_check = loader.GRAPH, loader.self_check
        try:
            loader.GRAPH = A
            loader.write_graph(rows, meta, True, before_swap=record)
            assert seen == [("complete", old_built, True)], seen
            assert DB.select_graph(A).ro_query("MATCH (p:Person) RETURN count(p)").result_set == [[3]]

            def broken(g, counts):
                raise RuntimeError("self-check failed")
            loader.self_check = broken
            try:
                loader.write_graph(rows, meta, True, before_swap=record)
            except RuntimeError:
                pass
            assert len(seen) == 1, "before_swap ran for a build that failed its self-check"
        finally:
            loader.GRAPH, loader.self_check = old_graph, old_check
    finally:
        drop(A)


def test_cache_is_dropped_again_when_building_turns_ready():
    old = loader.CASES
    try:
        loader.CASES = Path(tempfile.mkdtemp())
        (loader.CASES / "zz-test-a").mkdir()
        status = loader.CASES / "zz-test-a" / "status.json"
        research._seen_build.clear()
        status.write_text(json.dumps({"status": "building", "built_at": "t2", "config_hash": "h"}))
        research.drop_cache_if_rebuilt("zz-test-a")
        research._cache[(A, "tier_a")] = ({"P1": "A1"}, [])  # refilled from the OLD graph before the RENAME
        status.write_text(json.dumps({"status": "ready", "built_at": "t2", "config_hash": "h"}))
        research.drop_cache_if_rebuilt("zz-test-a")
        assert (A, "tier_a") not in research._cache, "a cache filled before the swap survived the ready write"
    finally:
        loader.CASES = old
        research.clear_cache()
        research._seen_build.clear()


def test_lock_release_never_deletes_another_builds_lock():
    lock = "build-lock:zz-lock3"
    try:
        DB.connection.set(lock, "other", ex=60)
        assert DB.connection.eval(loader.RELEASE_LOCK, 1, lock, "mine") == 0
        assert DB.connection.get(lock) in ("other", b"other")
        assert DB.connection.eval(loader.RELEASE_LOCK, 1, lock, "other") == 1
        assert DB.connection.get(lock) is None
        assert DB.connection.eval(loader.RELEASE_LOCK, 1, lock, "other") == 0  # missing key: no error
    finally:
        DB.connection.delete(lock)


# ---------- runner ----------
if __name__ == "__main__":
    tests = [(n, f) for n, f in list(globals().items()) if n.startswith("test_") and callable(f)]
    server.memory.init()  # create the scratch newsroom first, so its first Event does not look like a new graph
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception as e:
            failed += 1
            line = traceback.extract_tb(e.__traceback__)[-1].lineno
            print(f"FAIL  {name}  (line {line}): {str(e)[:300] or type(e).__name__}")
    if "newsroom:zz-test-cases" in DB.list_graphs():  # the Events this suite logged
        DB.select_graph("newsroom:zz-test-cases").delete()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
