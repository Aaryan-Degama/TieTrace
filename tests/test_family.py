"""Offline tests for family.py (phase 2, P2-3). Written by Aaryan, Oct 17 2026.

Run:  uv run python tests/test_family.py
No network: family.http_json and family.openalex are replaced by replies trimmed from real ROR, Wikidata,
GLEIF and OpenAlex answers recorded on 2026-10-17 (tests/fixtures/family/coke.json). Uses the scratch
case folder cases/zz-test-family and deletes it; never writes the real cases.
"""
import hashlib
import json
import shutil
import sys
import traceback
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import family  # noqa: E402
import loader  # noqa: E402

import os  # noqa: E402

os.environ.pop("SEC_USER_AGENT", None)  # the suite never calls SEC; test_sec_exhibit21 sets it with a fake reply
SEC_FX = json.loads((Path(__file__).resolve().parent / "fixtures" / "family" / "sec_synthetic.json").read_text(encoding="utf-8"))
FX = json.loads((Path(__file__).resolve().parent / "fixtures" / "family" / "coke.json").read_text(encoding="utf-8"))
SLUG = "zz-test-family"
DIR = ROOT / "cases" / SLUG
REAL = ["obesity-coke", "obesity-coke-staged", "philip-morris-lung"]
FAILED = []


def fake_http(url, params=None):
    if url == family.WD_SPARQL:
        assert "wd:Q3295867" in params["query"] and "wd:Q43229" in params["query"]  # seed and the class filter
    return FX["http"][url]


def fake_openalex(path, params):
    return FX["openalex"][f"{path}?{params['filter']}"]


family.http_json, family.openalex = fake_http, fake_openalex


def digest(slug):
    return hashlib.sha256((ROOT / "cases" / slug / "case.json").read_bytes()).hexdigest()


BEFORE = {s: (digest(s), loader.config_hash(loader.load_config(s))) for s in REAL}


def fresh():
    shutil.rmtree(DIR, ignore_errors=True)
    return family.propose(SLUG, ror="00yc8qw13")


def by_name(out, name):
    return [r for r in out["rows"] if r["name"] == name]


def base_case():
    """A scratch case.json shaped like philip-morris-lung, with an empty family for apply to fill."""
    cfg = loader.load_config("philip-morris-lung")
    cfg.update(slug=SLUG, company="The Coca-Cola Company", funders=[], institutions=[], rejected=[], notes="test")
    (DIR / "case.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def test_norm():
    assert family.norm("Nestlé S.A.") == "nestle"
    assert family.norm("The Coca-Cola Company (Australia)") == "coca cola"
    assert family.norm("Coca Cola (United States)") == "coca cola"
    assert family.norm("Swedish Match AB (publ) (Sweden)") == "swedish match"
    assert family.norm("Philip Morris GmbH") == "philip morris"
    assert family.norm("COCA-COLA HOLDINGS AFRICA LIMITED") == "coca cola holdings africa"


def test_match_type():
    assert family.match_type("X Ltd", "Coca-Cola", True) == "id"
    assert family.match_type("THE COCA-COLA COMPANY", "Coca Cola (United States)", False) == "exact_norm"
    assert family.match_type("Coca-Cola Bottling Company of Saudi Arabia", "The Coca-Cola Company", False) == "fuzzy"
    assert family.match_type("BlackRock", "The Coca-Cola Company", False) == "none"


def test_seed_by_id_and_sources():
    out = fresh()
    assert out["seed"] == {"name": "The Coca-Cola Company", "ror": "00yc8qw13", "qid": "Q3295867",
                           "lei": "UWJKFUJFZ02DKWI3RY53", "cik": None}
    seed = out["rows"][0]
    assert seed["id_source"] == "seed" and seed["rel_suggested"] == "parent" and seed["openalex_id"] == "I1312732213"
    assert out["counts"] == {"ror": 0, "wikidata_raw_items": 5, "wikidata": 3, "gleif": 4, "sec": 0}, out["counts"]
    assert all(r["status"] in family.STATUSES for r in out["rows"])
    assert (DIR / "candidates.json").exists()
    assert any("SEC" in w and "skipped" in w and "SEC_USER_AGENT" in w for w in out["warnings"])


def test_sec_exhibit21():
    """Synthetic EDGAR replies: latest 10-K (not the 8-K, not the older 10-K), header and duplicate rows skipped."""
    seed = {"name": "The Coca-Cola Company", "cik": "21344"}
    calls = []
    family.sec_get = lambda url: calls.append(url) or SEC_FX[url]
    warnings = []
    os.environ["SEC_USER_AGENT"] = "TieTrace test (no contact)"
    try:
        rows = family.from_sec(seed, warnings)
        assert family.from_sec(dict(seed, cik=None), warnings) == [] and "no SEC CIK" in warnings[-1]
    finally:
        del os.environ["SEC_USER_AGENT"]
    assert [r["name"] for r in rows] == ["Example Beverages Holdings Ltd.", "Example Bottling Company"], rows
    assert [r["rel_suggested"] for r in rows] == ["subsidiary", "bottler"]
    assert all(r["id_source"] == "sec-ex21" and r["status"] == "proposed" and r["openalex_id"] is None
               and r["source_url"].endswith("ko-12312025xex21.htm") and "0000021344-26-000002" in r["evidence"]
               for r in rows)
    n = len(calls)
    assert family.from_sec(seed, warnings) == [] and "SEC_USER_AGENT" in warnings[-1] and len(calls) == n  # unset: no call


def test_wikidata_class_filter_drops_brand_and_place():
    out = fresh()
    names = {r["name"] for r in out["rows"]}
    assert "Dasani" not in names                              # brand / drink
    assert "Coca-Cola Baltimore Branch Factory" not in names  # a building
    hbc = by_name(out, "Coca-Cola HBC")[0]
    assert hbc["match_type"] == "id" and hbc["rel_suggested"] == "subsidiary" and hbc["openalex_id"] is None
    br = by_name(out, "BlackRock")[0]  # owner (P127) of the seed: a shareholder, so no rel is suggested
    assert br["rel_suggested"] is None and br["openalex_id"] == "I55884533"
    assert br["status"] == "needs_human_source" and br["reason"] == family.SHAREHOLDER


def test_gleif_same_name_collision():
    out = fresh()
    au = [r for r in out["rows"] if r["source_id"] == "8945007BLGJ517U74C54"]
    assert len(au) == 1 and au[0]["status"] == "same_name_collision" and au[0]["match_type"] == "exact_norm"
    assert au[0]["rel_suggested"] is None and "no ID link" in au[0]["evidence"]
    assert not [r for r in out["rows"] if r["id_source"] == "gleif-name" and r["lei"] == "UWJKFUJFZ02DKWI3RY53"]
    costa = [r for r in out["rows"] if r["source_id"] == "213800U2NC79HDGLZN36"][0]
    assert costa["status"] == "proposed" and costa["match_type"] == "id"


def test_foundation_kind_kept_and_needs_human_source():
    out = fresh()
    co, fdn = by_name(out, "Coca-Cola")[0], by_name(out, "Coca-Cola Foundation")[0]
    assert co["entity"] == fdn["entity"] == "funder" and co["openalex_id"] != fdn["openalex_id"]  # same ROR, two rows
    assert co["kind"] == "company" and fdn["kind"] == "foundation" and co["ror"] == fdn["ror"] == "00yc8qw13"
    assert by_name(out, "Beverage Institute for Health and Wellness")[0]["kind"] == "foundation"
    trust = by_name(out, "The Coca-Cola Company Master Retirement Trust")[0]
    # an institution row gets no kind: "Trust" in a retirement trust's name does not make it a foundation (round 2)
    assert trust["status"] == "needs_human_source" and trust["kind"] is None and trust["rel_suggested"] is None
    assert all(r["kind"] is None for r in out["rows"] if r["entity"] == "institution")
    assert all(r["kind"] in loader.KINDS for r in out["rows"] if r["entity"] == "funder")


def test_quoted_label_rule_and_banned_words():
    rows = family.map_openalex([family.row("wikidata", "Q106505816", "Foundation for a Smoke-Free World", "Philip Morris",
                                           None, "test", None, ror="03p2hmv20")], "Philip Morris")
    fs = [r for r in rows if r["entity"] == "funder"][0]
    assert fs["kind"] == "foundation"
    assert ('described in https://api.openalex.org/funders/F4320318650 as '
            '"front organization of tobacco corporation Philip Morris"') in fs["evidence"]
    outside = fs["evidence"].replace('"front organization of tobacco corporation Philip Morris"', "")
    assert "front" not in outside.lower()  # the label appears only inside the quote
    for r in fresh()["rows"] + rows:
        assert not family.own_text_problems(r), r
    bad = dict(fs, evidence=fs["evidence"] + " looks independent")
    assert family.own_text_problems(bad)
    ok = dict(fs, evidence=fs["evidence"] + ' | described in https://x.org as "an independent body"')
    assert not family.own_text_problems(ok)  # a quoted source phrase is the source's words


def test_add_and_set_rules():
    fresh()
    family.add(SLUG, "Some Beverage Association", "named in a story; no formal link found")
    rows = family.read(SLUG)["rows"]
    i = len(rows) - 1
    assert rows[i]["status"] == "needs_human_source" and rows[i]["id_source"] == "manual"
    for kwargs in ({"status": "approved"},                     # needs_human_source: a human source first
                   {"status": "rejected"},                     # no reason
                   {"status": "approved", "rel": "owner"}):    # not a loader rel
        try:
            family.set_row(SLUG, i if kwargs.get("rel") is None else 0, **kwargs)
            raise AssertionError(f"accepted {kwargs}")
        except SystemExit:
            pass
    assert family.read(SLUG)["rows"][i]["status"] == "needs_human_source"


def approve_some():
    out = fresh()
    idx = {r["name"]: n for n, r in enumerate(out["rows"])}
    family.set_row(SLUG, 0, "approved", rel="parent")
    family.set_row(SLUG, idx["Coca-Cola"], "approved")
    family.set_row(SLUG, idx["Coca-Cola Foundation"], "approved")
    family.set_row(SLUG, idx["Coca-Cola HBC"], "approved", rel="bottler")          # unmapped: must not be copied
    family.set_row(SLUG, idx["BlackRock"], "rejected", reason="shareholder, not a parent")
    au = [n for n, r in enumerate(out["rows"]) if r["source_id"] == "8945007BLGJ517U74C54"][0]  # same name as the seed
    family.set_row(SLUG, au, "rejected", reason="different company, Australia")
    base_case()


def test_apply_copies_only_approved_and_mapped():
    approve_some()
    added = family.apply(SLUG, "Aaryan")
    cfg = loader.load_config(SLUG)  # passes check_config
    assert cfg["institutions"] == [{"id": "I1312732213", "name": "Coca Cola (United States)", "rel": "parent"}]
    assert {(f["id"], f["kind"]) for f in cfg["funders"]} == {("F4320307807", "company"), ("F4320308599", "foundation")}
    assert [r["id"] for r in cfg["rejected"]] == ["I55884533"]  # the AU collision has no OpenAlex id
    assert cfg["decided_by"]["institutions"] == f"Aaryan, {date.today():%b} {date.today().day}"
    assert added == {"funders": 2, "institutions": 1, "rejected": 1}


def test_apply_needs_by_and_respects_built_graph():
    approve_some()
    before = (DIR / "case.json").read_text(encoding="utf-8")
    for argv in (["apply", "--case", SLUG], ["apply", "--case", SLUG, "--by", ""]):
        try:
            family.main(argv)
            raise AssertionError("apply ran without --by")
        except SystemExit:
            pass
    (DIR / "status.json").write_text("{}", encoding="utf-8")
    try:
        family.apply(SLUG, "Aaryan")
        raise AssertionError("apply changed a built case without --force")
    except SystemExit:
        pass
    assert (DIR / "case.json").read_text(encoding="utf-8") == before
    family.apply(SLUG, "Aaryan", force=True)
    assert loader.load_config(SLUG)["institutions"]


def test_apply_refuses_approved_row_without_rel():
    out = fresh()
    base_case()
    data = family.read(SLUG)
    data["rows"][0]["status"] = "approved"  # edited by hand, rel left empty
    (DIR / "candidates.json").write_text(json.dumps(data), encoding="utf-8")
    try:
        family.apply(SLUG, "Aaryan")
        raise AssertionError("approved row without rel was copied")
    except SystemExit:
        pass
    assert out


def expect_exit(fn, *a, **kw):
    try:
        fn(*a, **kw)
    except SystemExit as e:
        return str(e)
    raise AssertionError(f"{fn.__name__} did not refuse")


def shareholder_approved(**set_kw):
    out = fresh()
    base_case()
    i = [n for n, r in enumerate(out["rows"]) if r["name"] == "BlackRock"][0]
    family.set_row(SLUG, 0, "approved", rel="parent")
    return i


def test_shareholder_cannot_be_approved_by_one_command():
    """The Reviewer's round-1 must-fix: set --status approved --rel subsidiary on BlackRock, then apply."""
    i = shareholder_approved()
    expect_exit(family.set_row, SLUG, i, "approved", rel="subsidiary")  # needs --source
    data = family.read(SLUG)
    data["rows"][i].update(status="approved", rel="subsidiary")  # even if someone edits the JSON by hand
    (DIR / "candidates.json").write_text(json.dumps(data), encoding="utf-8")
    before = (DIR / "case.json").read_bytes()
    assert "shareholder" in expect_exit(family.apply, SLUG, "Aaryan")
    assert (DIR / "case.json").read_bytes() == before
    # with a human source it goes through, and the source is recorded
    shareholder_approved()
    family.set_row(SLUG, i, "approved", rel="subsidiary", source="https://example.org/control-filing")
    family.apply(SLUG, "Aaryan")
    assert "I55884533" in {x["id"] for x in loader.load_config(SLUG)["institutions"]}


def test_shareholder_funder_inherits_status():
    """A funder found through a shareholder's ROR is a shareholder row too (in-test OpenAlex reply)."""
    seed = {"qid": "Q3295867", "name": "The Coca-Cola Company"}
    items = {"Q1": {"name": "Some Asset Manager", "dirs": {"parent"}, "props": {"P127"}, "classes": {"company"},
                    "ror": "0aaaaaaa1", "lei": None, "is_org": True}}
    fake = {"institutions?ror:0aaaaaaa1": {"results": []},
            "funders?ror:0aaaaaaa1": {"results": [{"id": "https://openalex.org/F1", "display_name": "Some Asset Manager"}]}}
    old, family.openalex = family.openalex, lambda path, params: fake[f"{path}?{params['filter']}"]
    try:
        rows = family.map_openalex(family.from_wikidata(seed, items), seed["name"])
    finally:
        family.openalex = old
    f = [r for r in rows if r["entity"] == "funder"][0]
    assert f["status"] == "needs_human_source" and f["reason"] == family.SHAREHOLDER and f["kind"] == "company"
    # and apply refuses it without a source
    fresh()
    base_case()
    data = family.read(SLUG)
    data["rows"].append(dict(f, status="approved"))
    (DIR / "candidates.json").write_text(json.dumps(data), encoding="utf-8")
    assert "shareholder" in expect_exit(family.apply, SLUG, "Aaryan")


def test_shareholder_flag_survives_reject_then_hand_edit():
    """Round 3: set --status rejected --reason shareholder replaced the reason; a hand edit to approved then got through."""
    i = shareholder_approved()
    family.set_row(SLUG, i, "rejected", reason="shareholder")
    r = family.read(SLUG)["rows"][i]
    assert r["reason"] == "shareholder" and r["flag"] == "shareholder"  # set never writes the flag
    data = family.read(SLUG)
    data["rows"][i].update(status="approved", rel="subsidiary")
    (DIR / "candidates.json").write_text(json.dumps(data), encoding="utf-8")
    before = (DIR / "case.json").read_bytes()
    assert "shareholder" in expect_exit(family.apply, SLUG, "Aaryan")
    assert (DIR / "case.json").read_bytes() == before


def map_with(items, fake):
    """from_wikidata + map_openalex on in-test items and an in-test OpenAlex reply (made up, test data only)."""
    old, family.openalex = family.openalex, lambda path, params: fake[f"{path}?{params['filter']}"]
    try:
        seed = {"qid": "Q3295867", "name": "The Coca-Cola Company"}
        return family.map_openalex(family.from_wikidata(seed, items), seed["name"])
    finally:
        family.openalex = old


def item(name, d, props, ror):
    return {"name": name, "dirs": {d}, "props": set(props), "classes": {"company"}, "ror": ror, "lei": None, "is_org": True}


def test_merge_keeps_the_more_careful_status():
    """A family row and a shareholder row that map to the same OpenAlex institution: the merged row is marked."""
    items = {"Q10": item("Family Unit", "child", ["P355"], "0aaaaaaa2"),
             "Q11": item("Holder Unit", "parent", ["P127"], "0aaaaaaa3")}  # family first, so the merge branch decides
    fake = {"institutions?ror:0aaaaaaa2": {"results": [{"id": "https://openalex.org/I9", "display_name": "Same Body"}]},
            "institutions?ror:0aaaaaaa3": {"results": [{"id": "https://openalex.org/I9", "display_name": "Same Body"}]},
            "funders?ror:0aaaaaaa2": {"results": []}, "funders?ror:0aaaaaaa3": {"results": []}}
    rows = [r for r in map_with(items, fake) if r["openalex_id"] == "I9"]
    assert len(rows) == 1 and rows[0]["source_id"] == "Q10", rows
    assert rows[0]["status"] == "needs_human_source" and rows[0]["flag"] == "shareholder"
    assert "Q11" in rows[0]["evidence"] or "Holder Unit" in rows[0]["evidence"]


def test_funder_via_family_and_shareholder_ror_keeps_shareholder_mark():
    items = {"Q12": item("Family Unit", "child", ["P355"], "0aaaaaaa4"),
             "Q13": item("Holder Unit", "parent", ["P127"], "0aaaaaaa5")}  # family ROR seen first
    funder = {"results": [{"id": "https://openalex.org/F9", "display_name": "Shared Funder"}]}
    fake = {"institutions?ror:0aaaaaaa4": {"results": []}, "institutions?ror:0aaaaaaa5": {"results": []},
            "funders?ror:0aaaaaaa4": funder, "funders?ror:0aaaaaaa5": funder}
    f = [r for r in map_with(items, fake) if r["openalex_id"] == "F9"]
    assert len(f) == 1 and f[0]["status"] == "needs_human_source" and f[0]["flag"] == "shareholder"


def test_review_hints_foundation_like_institution():
    import contextlib
    import io
    fresh()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        family.review(SLUG)
    lines = buf.getvalue().splitlines()
    trust = [x for x in lines if "Master Retirement Trust" in x][0]
    assert "(foundation-like name)" in trust
    assert not [x for x in lines if "(foundation-like name)" in x and " funder " in x]  # institutions only
    assert "(foundation-like name)" not in [x for x in lines if "BlackRock" in x][0]
    assert "flag: shareholder" in [x for x in lines if "BlackRock" in x][0]


def test_p127_only_child_says_stake_unknown():
    seed = {"qid": "Q3295867", "name": "The Coca-Cola Company"}
    items = {"Q2": {"name": "Monster Beverage", "dirs": {"child"}, "props": {"P127"}, "classes": {"company"},
                    "ror": None, "lei": None, "is_org": True},
             "Q3": {"name": "Coca-Cola HBC", "dirs": {"child"}, "props": {"P127", "P355"}, "classes": {"company"},
                    "ror": None, "lei": None, "is_org": True}}
    m, h = family.from_wikidata(seed, items)
    assert family.STAKE in m["evidence"] and family.STAKE not in h["evidence"]
    assert not family.own_text_problems(m)


def test_propose_keeps_human_decisions():
    fresh()
    family.set_row(SLUG, 0, "approved", rel="parent")
    expect_exit(family.propose, SLUG, ror="00yc8qw13")
    assert family.read(SLUG)["rows"][0]["status"] == "approved"
    family.propose(SLUG, ror="00yc8qw13", force=True)
    assert family.read(SLUG)["rows"][0]["status"] == "proposed"


def test_apply_two_parents_leaves_case_json_unchanged():
    out = fresh()
    base_case()
    idx = {r["name"]: n for n, r in enumerate(out["rows"])}
    family.set_row(SLUG, 0, "approved", rel="parent")
    family.set_row(SLUG, idx["BlackRock"], "approved", rel="parent", source="https://example.org/x")
    before = (DIR / "case.json").read_bytes()
    try:
        family.apply(SLUG, "Aaryan")
        raise AssertionError("two rel=parent rows were written")
    except ValueError as e:
        assert "parent" in str(e)
    assert (DIR / "case.json").read_bytes() == before


def test_apply_refuses_id_both_included_and_rejected():
    fresh()
    base_case()
    cfg = json.loads((DIR / "case.json").read_text(encoding="utf-8"))
    cfg["rejected"] = [{"id": "I1312732213", "name": "Coca Cola (United States)", "reason": "test"}]
    (DIR / "case.json").write_text(json.dumps(cfg), encoding="utf-8")
    before = (DIR / "case.json").read_bytes()
    family.set_row(SLUG, 0, "approved", rel="parent")
    assert "both included and rejected" in expect_exit(family.apply, SLUG, "Aaryan")
    assert (DIR / "case.json").read_bytes() == before


def test_banned_word_in_own_text_raises_systemexit():
    """A plain raise (SystemExit), not an assert, and nothing is written."""
    old = family.from_ror
    family.from_ror = lambda seed: [family.row("ror", "x", "X", seed["name"], None, "looks independent", None)]
    try:
        shutil.rmtree(DIR, ignore_errors=True)
        assert "banned" in expect_exit(family.propose, SLUG, ror="00yc8qw13")
    finally:
        family.from_ror = old
    assert not (DIR / "candidates.json").exists()


def test_real_cases_untouched():
    for s in REAL:
        assert (digest(s), loader.config_hash(loader.load_config(s))) == BEFORE[s], s
        assert not (ROOT / "cases" / s / "candidates.json").exists()


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    tests.sort(key=lambda t: t[0] == "test_real_cases_untouched")  # the untouched check runs last
    try:
        for name, fn in tests:
            try:
                fn()
                print("PASS", name)
            except Exception:
                FAILED.append(name)
                print("FAIL", name)
                traceback.print_exc(limit=3)
    finally:
        shutil.rmtree(DIR, ignore_errors=True)
    print(f"\n{len(tests) - len(FAILED)}/{len(tests)} passed")
    sys.exit(1 if FAILED else 0)
