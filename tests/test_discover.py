"""Tests for discover.py (checkpoint 5e).

Run:  uv run python tests/test_discover.py
Offline by default: httpx.get is replaced by a fake that replays OpenAlex responses recorded from real
live calls on 2026-10-16 (tests/fixtures/discover/, one JSON file per request, the API key not stored).
  DISCOVER_RECORD=1  call the live API and (re)write the fixtures (costs about $0.01)
  DISCOVER_LIVE=1    also run a live smoke test against the real API (off by default)
"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import traceback
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import discover  # noqa: E402
import loader  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "discover"
FAKE_KEY = "FAKEKEY-must-never-be-printed-123"
RECORD = os.environ.get("DISCOVER_RECORD") == "1"
real_get = httpx.get


class Reply:
    def __init__(self, body):
        self.status_code, self.text, self._body = 200, json.dumps(body), body

    def json(self):
        return self._body


def fixture_path(url, params):
    """One file per request: path + params without the key, hashed to a short name."""
    req = {"path": url.removeprefix(loader.API), "params": {k: str(v) for k, v in params.items() if k != "api_key"}}
    return FIX / (hashlib.sha1(json.dumps(req, sort_keys=True).encode()).hexdigest()[:16] + ".json"), req


def fake_get(url, params=None, timeout=None):
    path, req = fixture_path(url, params)
    assert params["api_key"] == loader.KEY  # the key is sent to OpenAlex, nowhere else
    if RECORD:
        r = real_get(url, params=params, timeout=timeout)
        assert r.status_code == 200, (r.status_code, req["path"])
        FIX.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"request": req, "response": r.json()}, ensure_ascii=False) + "\n")
        return r
    assert path.exists(), f"no recorded fixture for {req}"
    return Reply(json.loads(path.read_text())["response"])


def run(*argv):
    """Run the CLI offline; return (result dict, stdout+stderr text)."""
    out = io.StringIO()
    httpx.get = fake_get
    key = loader.api_key
    if not RECORD:
        loader.api_key = lambda: FAKE_KEY
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            result = discover.main(list(argv))
    finally:
        httpx.get, loader.api_key = real_get, key
    return result, out.getvalue()


# ---------- pure functions ----------
def test_status_logic():
    one = [{"name_match": True}, {"name_match": False}]
    assert discover.author_status(0, []) == "not_found"
    assert discover.author_status(2, one) == "unique"
    assert discover.author_status(50, one) == "ambiguous"  # hits beyond the page were not seen
    assert discover.author_status(2, [{"name_match": True}, {"name_match": True}]) == "ambiguous"
    assert discover.author_status(1, [{"name_match": False}]) == "ambiguous"  # a hit, but not the name
    assert discover.author_status(2, one, siblings=[{"id": "A2"}]) == "ambiguous"  # a likely split profile
    assert discover.author_status(0, [], siblings=[{"id": "A2"}]) == "ambiguous"  # found only under an initial


def test_name_match():
    a = {"display_name": "José María García‐Mina", "display_name_alternatives": []}  # U+2010 dash
    assert discover.name_match("Maria Garcia", a)
    assert not discover.name_match("Maria Garcia", {"display_name": "Marcos Fernandez"})
    # initials fit full words, in either direction (fix 8)
    assert discover.name_match("Maria Garcia", {"display_name": "Isabel S", "display_name_alternatives": ["M. García"]})
    assert discover.name_match("Yoni S. Freedhoff", {"display_name": "Yoni Freedhoff"}) is False  # no "s..." word
    assert discover.name_match("Yoni S. Freedhoff", {"display_name": "Yoni Simon Freedhoff"})
    assert not discover.name_match("Maria Garcia", {"display_name": "Miguel Garcia"})
    assert discover.name_match("Maria Garcia", {"display_name": "X", "display_name_alternatives": ["María García"]})
    assert not discover.name_match("", {"display_name": "Anyone"})


def test_share_and_query_slug():
    assert discover.share(35, 200) == 0.175 and discover.share(0, 200) == 0.0 and discover.share(3, 0) is None
    assert discover.query_slug("Conor O’Brien") == "conor-o-brien"
    assert len(discover.query_slug("王伟")) == 10  # no ASCII letters: short hash, never empty
    assert discover.initial_form("Yoni S. Freedhoff") == "Y. Freedhoff" and discover.initial_form("Plato") is None


def test_plain_strips_filter_syntax():
    assert discover.plain('lung, "cancer" | screening') == "lung cancer screening"


def test_slug_validation():
    for bad in ["A", "x", "-abc", "Abc", "a_b", "a/../b", "a" * 41, "a b"]:
        try:
            run("funders", "philip morris", "--case", bad)
        except SystemExit as e:
            assert e.code == 2, bad
        else:
            raise AssertionError(f"slug {bad!r} accepted")
    for good in ["pm", "philip-morris-lung", "a" * 40]:
        assert discover.SLUG_RE.match(good), good


def test_bad_topic_id_rejected():
    try:
        run("authors", "Maria Garcia", "--topic", "lung")
    except SystemExit as e:
        assert e.code == 2
    else:
        raise AssertionError("bad topic accepted")
    try:
        run("funders", "philip morris", "--topic", "T10202")  # --topic means nothing for funders (fix 7)
    except SystemExit as e:
        assert e.code == 2
    else:
        raise AssertionError("--topic accepted for funders")


# ---------- recorded OpenAlex responses ----------
def test_funders_cards():
    r, out = run("funders", "philip morris")
    ids = {c["id"]: c for c in r["candidates"]}
    assert r["total_hits"] == 2 and set(ids) == {"F4320315260", "F4320318650"}, ids
    pmi = ids["F4320315260"]
    assert pmi["ror"] == "https://ror.org/005rxfw29" and pmi["country"] == "US" and pmi["works_count"] > 0
    assert pmi["description"] and pmi["roles"] and pmi["type"] == "funder"
    assert "status" not in r and r["api_calls"] == 1, r  # one search call only
    assert "F4320318650" in out


def test_institutions_parents_children():
    r, _ = run("institutions", "philip morris")
    ids = {c["id"]: c for c in r["candidates"]}
    us = ids["I4210088192"]
    assert {c["id"] for c in us["children"]} == {"I2800621710", "I4210114941"}, us
    assert [p["id"] for p in ids["I2800621710"]["parents"]] == ["I4210088192"]
    assert ids["I2800621710"]["lineage"] == ["I2800621710", "I4210088192"]
    assert all(c["type"] and "ror" in c and "country" in c for c in r["candidates"])


def test_topics_keywords_phrase():
    r, out = run("topics", "lung cancer screening")
    assert [c["id"] for c in r["candidates"]] == ["T10202"]
    assert r["keywords"] and r["keywords"][0]["id"] == "lung-cancer-screening"
    assert isinstance(r["phrase_works"], int) and 0 < r["phrase_works"] < r["candidates"][0]["works_count"]
    assert r["api_calls"] == 3 and "title/abstract" in out


def test_authors_ambiguous_with_coverage():
    r, _ = run("authors", "Maria Garcia", "--topic", "T10493")
    assert r["status"] == "ambiguous" and r["total_hits"] > 1000, r["total_hits"]
    cards = r["candidates"]
    assert len(cards) == discover.PER_PAGE
    for c in cards:
        assert {"id", "name", "orcid", "works_count", "last_institution", "top_topics", "topic_overlap"} <= set(c)
    covered = [c for c in cards if c["funder_coverage"]]
    assert 0 < len(covered) <= discover.COVERAGE_MAX and all(c["name_match"] for c in covered)
    for c in covered:
        f = c["funder_coverage"]
        assert 0 <= f["with_funder"] <= f["works"] and f["share"] == round(f["with_funder"] / f["works"], 3)
    # 1 search + 1 sibling search + 1 topic count per card + 2 coverage counts per covered card
    assert r["api_calls"] == 2 + len(cards) + 2 * len(covered), r["api_calls"]
    assert any(c["topic_overlap"]["works"] > 0 for c in cards)  # T10493 is the top hit's main topic
    assert "approximate" in r["funder_coverage_note"]


def test_authors_one_profile_plus_sibling():
    # Was "unique" before the Reviewer's sibling rule (fix 2): one profile for the full name, but the
    # initial search "Y. Freedhoff" finds a likely split profile, so the status is now "ambiguous".
    r, out = run("authors", "Yoni Freedhoff", "--topic", "T10010")
    assert r["status"] == "ambiguous" and len(r["candidates"]) == 1 and r["siblings"], r
    assert r["sibling_search"] == "Y. Freedhoff" and "sibling?" in out
    c = r["candidates"][0]
    assert c["name_match"] and c["funder_coverage"]["works"] > 0
    assert c["topic_overlap"]["works"] > 0, c["topic_overlap"]


def test_authors_not_found():
    r, out = run("authors", "Zxqv Plorkington")
    assert r["status"] == "not_found" and r["candidates"] == [] and r["siblings"] == [] and r["api_calls"] == 2
    assert "not_found" in out


def test_case_file_written():
    slug = "test-discover-tmp"
    try:
        r, _ = run("funders", "philip morris", "--case", slug)
        saved = json.loads((discover.CASES / slug / "candidates_funders_philip-morris.json").read_text())
        assert saved == r
    finally:
        shutil.rmtree(discover.CASES / slug, ignore_errors=True)


def test_json_stdout_stays_clean_on_retry():
    # loader.get prints "OpenAlex ... retry" to stdout on a network error; --json output must still parse (fix 6).
    failed = []

    def flaky(url, params=None, timeout=None):
        if not failed:
            failed.append(1)
            raise httpx.ConnectError("simulated")
        return fake_get(url, params, timeout)

    if RECORD:
        return
    out, err = io.StringIO(), io.StringIO()
    key = loader.api_key
    httpx.get, loader.api_key = flaky, (lambda: FAKE_KEY)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            discover.main(["funders", "philip morris", "--json"])
    finally:
        httpx.get, loader.api_key = real_get, key
    assert json.loads(out.getvalue())["candidates"] and "retry" in err.getvalue()


def test_key_never_in_output():
    if RECORD:
        return
    for argv in [("funders", "philip morris", "--json"), ("authors", "Maria Garcia", "--json"),
                 ("topics", "lung cancer screening")]:
        r, out = run(*argv)
        assert FAKE_KEY not in out and FAKE_KEY not in json.dumps(r)
    for f in FIX.glob("*.json"):  # fixtures were recorded with the real key: it must not be stored
        assert "api_key" not in f.read_text(), f


def test_live_smoke():
    if os.environ.get("DISCOVER_LIVE") != "1":
        print("  (live smoke test skipped; set DISCOVER_LIVE=1)")
        return
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        f = discover.main(["funders", "philip morris"])
        a = discover.main(["authors", "Maria Garcia"])
    assert any(c["id"] == "F4320315260" for c in f["candidates"])
    assert a["status"] == "ambiguous"
    assert loader.api_key() not in out.getvalue()


# ---------- Reviewer tests (checkpoint 5e review, Oct 16) ----------
def recorded_count(filter_):
    """Ground truth from a recorded real OpenAlex count call (same fixture mechanism as run())."""
    httpx.get, key = fake_get, loader.api_key
    if not RECORD:
        loader.api_key = lambda: FAKE_KEY
    loader.KEY = loader.api_key()
    try:
        return discover.count(filter_)
    finally:
        httpx.get, loader.api_key = real_get, key


def test_overlap_share_never_above_one():
    # Marcos Fernandez (A5107856111): 1,358 works, T10048 1,255 + T10224 1,045 in his top-topic counts.
    # A work can carry both topics, so summing the counts double-counts (real union: 1,272 works, live Oct 16).
    r, _ = run("authors", "Maria Garcia", "--topic", "T10048", "--topic", "T10224")
    for c in r["candidates"]:
        o = c["topic_overlap"]
        assert o["share"] is None or 0 <= o["share"] <= 1, (c["id"], o)
        assert o["works"] is None or o["works"] <= c["works_count"], (c["id"], o)


def test_overlap_counts_topics_outside_top_five():
    # OpenAlex lists only an author's top 5 topics. Maria Luisa García (A5100654036) has works in T10724,
    # her 6th topic: reporting 0 there is a false "no overlap". Unknown (None) or the real count is fine.
    truth = recorded_count("authorships.author.id:A5100654036,topics.id:T10724")
    assert truth > 0, truth
    r, _ = run("authors", "Maria Garcia", "--topic", "T10724")
    o = {c["id"]: c for c in r["candidates"]}["A5100654036"]["topic_overlap"]
    assert o["works"] in (None, truth), (o, truth)


def test_split_profile_sibling_shown():
    # Live Oct 16: search "Yoni Freedhoff" returns 1 profile (A5045483115), but "Y. Freedhoff" also returns
    # A5097454007 (3 works, no ORCID, name "Y. Freedhoff"): a likely split profile of the same person.
    # D2: "split risk hides ties, so show alternatives and sibling profiles". It must be visible in the result.
    r, _ = run("authors", "Yoni Freedhoff")
    assert "A5097454007" in json.dumps(r), (r["status"], [c["id"] for c in r["candidates"]])


def test_name_match_web_punctuation_in_query():
    # Names pasted from a web page often carry U+2010 hyphens or U+2019 apostrophes; matching must be symmetric.
    prof = {"display_name": "José María García‐Mina", "display_name_alternatives": []}
    assert discover.name_match("José María García‐Mina", prof)  # same string on both sides
    assert discover.name_match("Conor O'Brien", {"display_name": "Conor O’Brien"})
    assert discover.name_match("Conor O’Brien", {"display_name": "Conor O'Brien"})


def test_case_file_keeps_earlier_searches():
    # A case has several experts (5f has three) and several family searches (philip morris, altria).
    # Saving a second search to the same case must not destroy the candidates shown for the first.
    slug = "test-discover-tmp2"
    try:
        run("authors", "Maria Garcia", "--case", slug)
        run("authors", "Yoni Freedhoff", "--case", slug)
        saved = " ".join(p.read_text() for p in (discover.CASES / slug).rglob("*.json"))
        assert "A5100654036" in saved and "A5045483115" in saved, "first search overwritten"
    finally:
        shutil.rmtree(discover.CASES / slug, ignore_errors=True)


def test_query_that_is_only_punctuation_rejected():
    # plain(",") == "" -> filter title_and_abstract.search:"" returns count 0 (live Oct 16): a fake "0 works".
    try:
        run("topics", ", |")
    except SystemExit as e:
        assert e.code == 2
    else:
        raise AssertionError("punctuation-only query accepted")


def test_surname_first_query_never_unique():
    # Round 2 review: "Freedhoff, Yoni" (citation order) finds 1 profile, and its initial search ("F. Yoni")
    # cannot find the split profile A5097454007. Whatever the sibling logic does, it must never say "unique" here.
    r, _ = run("authors", "Freedhoff, Yoni")
    assert r["candidates"] and r["status"] != "unique", (r["status"], r.get("siblings"))


# ---------- round 2 should-fix (Builder) ----------
def test_clean_name_and_strength():
    assert discover.clean_name("Freedhoff, Yoni") == "Yoni Freedhoff"
    assert discover.clean_name("Dr. Yoni Freedhoff") == "Yoni Freedhoff"
    assert discover.clean_name("Prof Kelly D. Brownell") == "Kelly D. Brownell"
    st = discover.sibling_strength
    assert st("Yoni Freedhoff", {"display_name": "Y. Freedhoff"}) == "strong"
    assert st("Yoni Freedhoff", {"display_name": "Yoni S. Freedhoff"}) == "strong"
    assert st("Kelly Brownell", {"display_name": "Keith Brownell"}) == "weak"
    assert st("Kelly Brownell", {"display_name": "K. D. Brownell"}) == "weak"  # not exactly "K. Brownell"
    assert st("Kelly Brownell", {"display_name": "Keith B", "display_name_alternatives": ["K. Brownell"]}) == "weak"
    assert st("Kelly Brownell", {"display_name": "Brownell Kd", "display_name_alternatives": ["Kelly D. Brownell"]}) == "strong"
    assert discover.share(1300, 1272) == 1  # capped
    assert discover.author_status(2, [{"name_match": True}], sibling_truncated=True) == "ambiguous"


def test_comma_and_title_queries_find_sibling():
    for q in ["Freedhoff, Yoni", "Dr. Yoni Freedhoff"]:
        r, _ = run("authors", q)
        assert r["name_searched"] == "Yoni Freedhoff", r["name_searched"]
        sib = {c["id"]: c for c in r["siblings"]}
        assert "A5097454007" in sib and sib["A5097454007"]["match"] == "strong", (q, list(sib))
        assert r["status"] == "ambiguous"


def test_siblings_strong_first_and_truncation_flag():
    r, out = run("authors", "Kelly Brownell")
    labels = [c["match"] for c in r["siblings"]]
    assert labels == sorted(labels, key=lambda m: m != "strong"), labels
    m = {c["id"]: c["match"] for c in r["siblings"]}
    # live Oct 16: "K. Brownell" is strong; Keith / Kyle / Katharine Brownell fit only by initial: weak
    assert m.get("A5019067137") == "strong" and m.get("A5054340648") == "weak", m
    assert m.get("A5030205718") == "weak" and m.get("A5112808442") == "weak", m
    r2, out2 = run("authors", "Maria Garcia")
    assert r2["sibling_search_truncated"] is True and "only the first page was read" in out2
    assert r2["status"] == "ambiguous"


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
