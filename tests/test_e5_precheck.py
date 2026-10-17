"""Offline tests for eval/e5_precheck.py (no network, no FalkorDB). Last updated by Aaryan, Oct 17 2026.

Run:  uv run python tests/test_e5_precheck.py
The rule function gets hand-made fixture dicts (synthetic, test-only). The last test checks that the
suggestion counts in eval/results.md match the AI column in eval/e5_sample.md and that no human cell is filled.
"""
import re
import sys
import traceback
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
import e5_precheck as P  # noqa: E402

FAILED = []


def ev(**kw):
    base = {"works_count": 40, "orcid": True, "n_last_known": 1, "sample": 40, "missing": [], "years": (2005, 2024),
            "n_fields": 3, "orcid_listed": 30, "far_domains": [], "same_year_countries": (2020, ["US"]),
            "same_name": 2, "split_hits": [], "unchecked": 0}
    return {**base, **kw}


def test_ok():
    word, reason = P.suggest(ev())
    assert word == "ok" and "2005-2024" in reason, (word, reason)


def test_merged_span():
    word, reason = P.suggest(ev(years=(1940, 2024)))
    assert word == "merged" and "1940-2024" in reason
    assert P.suggest(ev(years=(1964, 2024)))[0] == "ok"  # exactly 60 years is not "longer than 60"


def test_merged_domains_and_countries():
    assert P.suggest(ev(far_domains=[("Health Sciences", "Physical Sciences")]))[0] == "merged"
    word, reason = P.suggest(ev(same_year_countries=(2019, ["DE", "NL", "US"])))
    assert word == "merged" and "3 countries in 2019" in reason
    assert P.suggest(ev(same_year_countries=(2019, ["NL", "US"])))[0] == "ok"  # two countries is normal


def test_split():
    word, reason = P.suggest(ev(split_hits=[("A1", "shares institution I9")]))
    assert word == "split" and "A1" in reason and "I9" in reason


def test_unclear():
    assert P.suggest(ev(years=(1900, 2024), split_hits=[("A1", "shares institution I9")]))[0] == "unclear"
    assert P.suggest(ev(missing=["no works returned"], years=None))[0] == "unclear"
    word, reason = P.suggest(ev(same_name=40, unchecked=15))
    assert word == "unclear" and "only 25 checked" in reason
    assert P.suggest(ev(missing=["no works returned"], years=(1900, 2024)))[0] == "merged"  # evidence still wins


def test_name_key():
    assert P.name_key("Steven N. Blair") == P.name_key("S. N. Blair") == ("blair", "s")
    assert P.name_key("José Müller") == ("muller", "j")
    assert P.name_key("Madonna") is None


def test_markdown_and_results_agree():
    md = (ROOT / "eval" / "e5_sample.md").read_text(encoding="utf-8")
    rows = [line.split("|")[1:-1] for line in md.splitlines() if re.match(r"\| \d+ \|", line)]
    assert len(rows) == 50
    assert all(r[7].strip() == "" and r[8].strip() == "" for r in rows), "human columns must stay empty"
    counts = Counter(r[9].strip().split(":")[0] for r in rows)
    assert set(counts) <= {"ok", "merged", "split", "unclear"}, counts
    sect = (ROOT / "eval" / "results.md").read_text(encoding="utf-8").split("## E5", 1)[1].split("\n## ", 1)[0]
    for word, n in counts.items():
        assert f"| {word} | {n} |" in sect, (word, n)
    assert "error rate" in sect.lower() and "no error rate" in sect.lower()


# --- Reviewer tests (Oct 17): gather() on synthetic fixtures, with get() replaced so nothing touches the network ---

def W(wid, domain, coauthors, me="A1", my_orcid=None):
    auths = [{"author": {"id": f"https://openalex.org/{me}", "orcid": my_orcid}}]
    auths += [{"author": {"id": (f"https://openalex.org/{c}" if c else None)}} for c in coauthors]
    return {"id": f"https://openalex.org/{wid}", "publication_year": 2020,
            "primary_topic": {"field": {"display_name": domain + " f"}, "domain": {"display_name": domain}},
            "authorships": auths}


def fake(author=None, works=(), search=(), cowork=(), years=(2010, 2020)):
    calls = []
    author = {"display_name": "Jane Q. Doe", "works_count": len(works), "affiliations": [], **(author or {})}

    def get(name, path, params):
        calls.append((name, params))
        kind = name.split("_")[0]
        return {"author": author, "years": {"group_by": [{"key": str(y)} for y in years]},
                "works": {"results": list(works)}, "search": {"results": list(search)},
                "cowork": {"results": list(cowork)}}[kind]
    return get, calls


def run_gather(**kw):
    get, calls = fake(**kw)
    old, P.get = P.get, get
    try:
        return P.gather("A1"), calls
    finally:
        P.get = old


def test_gather_domain_share_boundary():
    # 12 works: 9 Health with X, 3 Social with Y. 3 >= 25% of 12 and 3 >= MIN_WORKS -> a far pair.
    works = [W(f"W{i}", "Health", ["X"]) for i in range(9)] + [W(f"S{i}", "Social", ["Y"]) for i in range(3)]
    e, _ = run_gather(works=works)
    assert e["far_domains"] == [("Health", "Social")], e["far_domains"]
    # 13 works with 3 Social: 3 < 25% of 13 -> no pair
    e, _ = run_gather(works=works + [W("W99", "Health", ["X"])])
    assert e["far_domains"] == [], e["far_domains"]
    # 8 works, 2 Social: 25% but fewer than 3 works -> no pair
    works8 = [W(f"W{i}", "Health", ["X"]) for i in range(6)] + [W(f"S{i}", "Social", ["Y"]) for i in range(2)]
    assert run_gather(works=works8)[0]["far_domains"] == []
    # a shared co-author removes the pair
    works[-1] = W("S2", "Social", ["Y", "X"])
    assert run_gather(works=works)[0]["far_domains"] == []


def test_gather_missing_author_ids_are_not_shared():
    # authorships with no author id on both sides must not count as a shared co-author
    works = [W(f"W{i}", "Health", ["X", None]) for i in range(4)] + [W(f"S{i}", "Social", ["Y", None]) for i in range(4)]
    assert run_gather(works=works)[0]["far_domains"] == [("Health", "Social")]


def test_gather_countries_per_year():
    aff = [{"institution": {"id": "https://openalex.org/I1", "country_code": "US"}, "years": [2019, 2020]},
           {"institution": {"id": "https://openalex.org/I2", "country_code": "NL"}, "years": [2020]},
           {"institution": {"id": "https://openalex.org/I3", "country_code": None}, "years": [2020]},
           {"institution": {"id": "https://openalex.org/I4", "country_code": "DE"}, "years": [2019]}]
    e, _ = run_gather(author={"affiliations": aff}, works=[W("W1", "Health", ["X"])])
    assert e["same_year_countries"] in ((2019, ["DE", "US"]), (2020, ["NL", "US"])), e["same_year_countries"]
    aff.append({"institution": {"id": "https://openalex.org/I5", "country_code": "FR"}, "years": [2020]})
    e, _ = run_gather(author={"affiliations": aff}, works=[W("W1", "Health", ["X"])])
    assert e["same_year_countries"] == (2020, ["FR", "NL", "US"]), e["same_year_countries"]
    assert P.suggest(e)[0] == "merged"


def test_gather_split_hits_and_name_filter():
    inst = lambda i: {"institution": {"id": f"https://openalex.org/{i}"}}
    me = {"affiliations": [inst("I1")]}
    search = [{"id": "https://openalex.org/A1", "display_name": "Jane Q. Doe", "affiliations": [inst("I1")]},  # self
              {"id": "https://openalex.org/A2", "display_name": "J. Doe", "affiliations": [inst("I1")]},  # same key
              {"id": "https://openalex.org/A3", "display_name": "John Doe", "affiliations": [inst("I9")]},
              {"id": "https://openalex.org/A4", "display_name": "Mary Doe", "affiliations": [inst("I1")]}]  # other initial
    works = [W(f"W{i}", "Health", ["X"]) for i in range(4)]
    cowork = [{"id": "https://openalex.org/W77", "authorships": [{"author": {"id": "https://openalex.org/A3"}},
                                                                  {"author": {"id": "https://openalex.org/X"}}]}]
    e, calls = run_gather(author=me, works=works, search=search, cowork=cowork)
    assert e["same_name"] == 2, e["same_name"]  # A2, A3; not self, not Mary
    hits = dict(e["split_hits"])
    assert set(hits) == {"A2", "A3"} and "I1" in hits["A2"] and "W77" in hits["A3"], hits
    flt = [p for n, p in calls if n.startswith("cowork")][0]["filter"]
    assert "A4" not in flt and "X" in flt, flt
    assert P.suggest(e)[0] == "split"


def test_gather_too_many_candidates_unclear():
    search = [{"id": f"https://openalex.org/A{100 + i}", "display_name": "J. Doe", "affiliations": []} for i in range(30)]
    e, _ = run_gather(works=[W("W1", "Health", ["X"])], search=search)
    assert e["same_name"] == 30 and e["unchecked"] == 5
    assert P.suggest(e) == ("unclear", "30 same-name profiles, only 25 checked")
    # the 26th candidate is not checked, even if it shares an institution
    me = {"affiliations": [{"institution": {"id": "https://openalex.org/I1"}}]}
    search[25]["affiliations"] = [{"institution": {"id": "https://openalex.org/I1"}}]
    e, calls = run_gather(author=me, works=[W("W1", "Health", ["X"])], search=search)
    assert e["split_hits"] == [], e["split_hits"]
    flt = [p for n, p in calls if n.startswith("cowork")][0]["filter"]
    assert flt.split(",")[0].count("A1") == 25, flt


def test_gather_frequent_coauthors_only():
    # with >= 3 sampled works, a co-author seen once is not "frequent"; a co-work without one is no hit
    works = [W(f"W{i}", "Health", ["X"]) for i in range(3)] + [W("W9", "Health", ["Once"])]
    search = [{"id": "https://openalex.org/A2", "display_name": "J. Doe", "affiliations": []}]
    cowork = [{"id": "https://openalex.org/W5", "authorships": [{"author": {"id": "https://openalex.org/A2"}},
                                                                 {"author": {"id": "https://openalex.org/Once"}}]}]
    e, calls = run_gather(works=works, search=search, cowork=cowork)
    flt = [p for n, p in calls if n.startswith("cowork")][0]["filter"]
    assert flt.endswith("author.id:X"), flt
    assert e["split_hits"] == [], e["split_hits"]


def test_http_error_text_has_no_key():
    import tempfile
    import httpx
    secret = "SENTINEL-KEY-123"

    class R:
        status_code, text = 404, "not found"
    seen = []
    old = (P.api_key, P.CACHE, httpx.get, P.SLEEP)
    P.api_key, P.CACHE, P.SLEEP = (lambda: secret), Path(tempfile.mkdtemp()), 0
    httpx.get = lambda url, params, timeout: (seen.append(params), R())[1]
    try:
        P.get("x", "authors/A1", {})
        raise AssertionError("expected RuntimeError")
    except RuntimeError as e:
        assert secret not in repr(e) and secret not in str(e), e
    finally:
        P.api_key, P.CACHE, httpx.get, P.SLEEP = old
    assert seen and seen[0]["api_key"] == secret  # the key goes only into the request params


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
            print("PASS", name)
        except Exception:
            FAILED.append(name)
            print("FAIL", name)
            traceback.print_exc(limit=3)
    print(f"\n{len(tests) - len(FAILED)}/{len(tests)} passed")
    sys.exit(1 if FAILED else 0)
