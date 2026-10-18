"""Tests for the web app's create-a-case wizard (decided by Aaryan, Oct 18 2026; written by Aaryan's Builder).

Run:  FALKORDB_PORT=6379 uv run python tests/test_web_case.py [name-substring ...]
No OpenAlex call and no graph build: discover's searches, the dry run, the RAM check and the build subprocess are
fakes (synthetic fixtures, test-only). Scratch cases are cases/zz-web-*; they are deleted at the end.
"""
import json
import os
import re
import shutil
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("FALKORDB_PORT", "6379")
os.environ["TIETRACE_NEWSROOM"] = "zz-test-web-case"  # never the real alpha newsroom

from fastapi.testclient import TestClient  # noqa: E402

import web.app as w  # noqa: E402

L = w.loader
ORIGIN = "http://127.0.0.1:8000"
CLIENT = TestClient(w.app, base_url=ORIGIN, headers={"Origin": ORIGIN})
BANNED = re.compile(r"\b(independent|conflicted|unbiased)\b|clean[\s-]+source", re.I)
KEY = "zzFAKEKEY0123456789"
PM = L.load_config("philip-morris-lung")

# ---------- fakes (synthetic, test-only) ----------
CALLS = []


def fake_funders(q):
    CALLS.append(("funders", q))
    return {"total_hits": 2, "candidates": [
        {"id": "F900001", "name": "Zz Test Company", "type": "funder", "country": "US", "works_count": 10, "description": None},
        {"id": "F900002", "name": "Zz Test Foundation", "type": "funder", "country": "US", "works_count": 5, "description": None}]}


def fake_institutions(q):
    CALLS.append(("institutions", q))
    return {"total_hits": 2, "candidates": [
        {"id": "I900001", "name": "Zz Test Company (United States)", "type": "company", "country": "US", "works_count": 20,
         "lineage": ["I900001"], "parents": [], "children": []},
        {"id": "I900002", "name": "Zz Test Company (Germany)", "type": "company", "country": "DE", "works_count": 4,
         "lineage": ["I900002"], "parents": [], "children": []}]}


def fake_topics(q):
    CALLS.append(("topics", q))
    return {"total_hits": 1, "candidates": [{"id": "T900001", "name": "Zz Test Topic", "type": "topic", "works_count": 99}],
            "keywords": [], "phrase": q, "phrase_works": 7}


class FakeProc:
    started = []

    def __init__(self, cmd, stdout=None, **kw):
        self.cmd, self.code = cmd, None
        FakeProc.started.append(self)
        stdout.write(f"fetching works\nOpenAlex url ...api_key={KEY}&x=1\nRAM check passed\n")

    def poll(self):
        return self.code

    @property
    def returncode(self):
        return self.code


def install_fakes():
    w.discover.funders, w.discover.institutions, w.discover.topics = fake_funders, fake_institutions, fake_topics
    L.api_key = lambda: KEY
    L.dry_run = lambda: {"case": L.SLUG, "works": 1234, "api_calls": 56, "author_calls": 30, "cost_usd": 0.006,
                         "graph_mb": 31, "max_api_calls": L.MAX_API_CALLS, "dry_run_calls": 3, "queries": []}
    w.ram_check = lambda slug: {"estimate_mb": 1000, "source": "rule of thumb (no earlier build)", "problem": None}
    w.other_build_running = lambda: False
    w.BUILD_POPEN = FakeProc
    w._build.update(proc=None, slug=None, log=None)


install_fakes()


BARE = TestClient(w.app, base_url=ORIGIN)  # sends no Origin header at all


def post(url, body, origin=ORIGIN):
    return CLIENT.post(url, json=body, headers={"Origin": origin}) if origin else BARE.post(url, json=body)


def slug(tag):
    return f"zz-web-{tag}"


def discovered(s):
    r = post("/api/case/discover", {"slug": s, "company": "Zz Test Company", "topic": "zz topic"})
    assert r.status_code == 200 and r.json()["ok"], r.text
    return r.json()


def write_body(s, **over):
    body = {"slug": s, "company": "Zz Test Company", "years": [2010, 2026], "reviewer": "Aaryan",
            "funders": [{"id": "F900001", "kind": "company"}, {"id": "F900002", "kind": "foundation"}],
            "institutions": [{"id": "I900001", "rel": "parent"}, {"id": "I900002", "rel": "subsidiary"}],
            "topics": [{"id": "T900001"}]}
    body.update(over)
    return body


def token(s):
    r = post("/api/confirm", {"action": "build_case", "params": {"slug": s}})
    assert r.status_code == 200 and r.json()["ok"], r.text
    return r.json()["token"]


def ready_case(tag):
    s = slug(tag)
    discovered(s)
    r = post("/api/case/write", write_body(s))
    assert r.status_code == 200 and r.json()["ok"], r.text
    return s


# ---------- tests ----------

def test_bad_slugs_are_refused_by_every_endpoint():
    for bad in ("a", "-ab", "Ab-c", "ab_c", "../etc", "x" * 41, "ab c", ""):
        assert post("/api/case/discover", {"slug": bad, "company": "Zz", "topic": "zz"}).status_code == 400, bad
        assert post("/api/case/write", write_body(bad)).status_code == 400, bad
        assert post("/api/case/estimate", {"slug": bad}).status_code == 400, bad
        assert post("/api/case/build", {"slug": bad, "confirm": "x"}).status_code == 400, bad
        assert CLIENT.get("/api/case/status", params={"slug": bad}).status_code == 400, bad
    assert not any(p.name.startswith(("..", "Ab")) for p in L.CASES.iterdir())


def test_an_existing_case_is_refused_and_never_overwritten():
    path = L.CASES / "philip-morris-lung" / "case.json"
    before, n = path.read_bytes(), len(CALLS)
    assert post("/api/case/discover", {"slug": "philip-morris-lung", "company": "Zz", "topic": "zz"}).status_code == 409
    assert len(CALLS) == n  # refused before any search
    assert post("/api/case/write", write_body("philip-morris-lung")).status_code == 409
    s = ready_case("twice")
    first = (L.CASES / s / "case.json").read_bytes()
    r = post("/api/case/write", write_body(s, reviewer="Abdul"))
    assert r.status_code == 409 and (L.CASES / s / "case.json").read_bytes() == first
    assert path.read_bytes() == before


def test_inputs_are_checked_strictly():
    s = slug("inputs")
    assert post("/api/case/discover", {"slug": s, "company": "", "topic": "zz"}).status_code == 400
    assert post("/api/case/discover", {"slug": s, "company": "Zz", "topic": "x" * 121}).status_code == 400
    assert post("/api/case/discover", {"slug": s, "company": "--", "topic": "zz"}).status_code == 400
    discovered(s)
    for over in ({"reviewer": ""}, {"reviewer": "   "}, {"reviewer": "Robert'); DROP"}, {"reviewer": "1abc"},
                 {"funders": [{"id": "F1; MATCH", "kind": "company"}]},
                 {"funders": [{"id": "F123", "kind": "company"}]},       # well formed but not found by discovery
                 {"topics": [{"id": "I900001"}]},                          # an institution id in the topic list
                 {"institutions": [{"id": "I900001", "rel": "parent"}, {"id": "I900002", "rel": "parent"}]},
                 {"institutions": [{"id": "I900002", "rel": "subsidiary"}]},  # no parent
                 {"funders": [{"id": "F900001", "kind": "sponsor"}]},
                 {"topics": []}, {"years": [2026, 2010]}, {"years": [2010, 2015]}):  # recent_year 2021 outside
        r = post("/api/case/write", write_body(s, **over))
        assert r.status_code in (400, 422), (over, r.status_code, r.text)
        assert not (L.CASES / s / "case.json").exists(), over


def test_discover_saves_candidates_and_preselects_nothing():
    s = slug("disc")
    j = discovered(s)
    assert [c["id"] for c in j["funders"]] == ["F900001", "F900002"] and j["topics"][0]["id"] == "T900001"
    assert j["cost_note"] and j["defaults"] == {k: PM[k] for k in w.CASE_DEFAULTS}
    assert not any("selected" in c or "checked" in c for k in ("funders", "institutions", "topics") for c in j[k])
    files = sorted(p.name for p in (L.CASES / s).glob("candidates_*.json"))
    assert files == ["candidates_funders_zz-test-company.json", "candidates_institutions_zz-test-company.json",
                     "candidates_topics_zz-topic.json"], files
    js = (w.STATIC / "case.js").read_text(encoding="utf-8")
    assert "checked = true" not in js and "checked: " not in js  # the page ticks nothing either


def test_written_config_passes_check_config_in_the_reference_shape():
    s = ready_case("shape")
    cfg = json.loads((L.CASES / s / "case.json").read_text(encoding="utf-8"))
    L.check_config(cfg, s)
    assert L.load_config(s) == cfg
    assert set(cfg) == set(PM) - {"rejected"}  # same keys as philip-morris-lung, rejected is optional
    assert all(cfg[k] == PM[k] for k in w.CASE_DEFAULTS) and cfg["public_ids"] == []
    assert set(cfg["decided_by"]) == set(L.DECISIONS)
    assert all(re.fullmatch(r"Aaryan, [A-Z][a-z]{2} \d{1,2}", v) for v in cfg["decided_by"].values())
    assert cfg["funders"][0] == {"id": "F900001", "name": "Zz Test Company", "kind": "company"}  # name from discovery
    assert cfg["institutions"][0] == {"id": "I900001", "name": "Zz Test Company (United States)", "rel": "parent"}


def test_estimate_returns_the_dry_run_numbers_and_restores_the_loader_case():
    s = ready_case("est")
    r = post("/api/case/estimate", {"slug": s})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["estimate"]["case"] == s and j["estimate"]["works"] == 1234 and j["ram"]["problem"] is None
    assert L.SLUG == L.DEFAULT_CASE and L.GRAPH == f"research:{L.DEFAULT_CASE}"
    assert post("/api/case/estimate", {"slug": slug("nothing-here")}).status_code == 400


def test_build_needs_a_one_time_token_for_exactly_that_slug():
    s, other = ready_case("tok"), ready_case("tok2")
    FakeProc.started.clear()
    assert post("/api/case/build", {"slug": s}).status_code == 403
    assert post("/api/case/build", {"slug": s, "confirm": "made-up"}).status_code == 403
    assert post("/api/case/build", {"slug": s, "confirm": token(other)}).status_code == 403  # token of another case
    t = token(s)
    assert post("/api/case/build", {"slug": s, "confirm": t}, origin="https://evil.example").status_code == 403
    assert not FakeProc.started
    r = post("/api/case/build", {"slug": s, "confirm": t})
    assert r.status_code == 200 and r.json()["ok"], r.text
    assert len(FakeProc.started) == 1
    FakeProc.started[0].code = 0
    assert post("/api/case/build", {"slug": s, "confirm": t}).status_code == 403  # used once only
    assert post("/api/confirm", {"action": "build_cases", "params": {"slug": s}}).status_code == 400


def test_build_command_is_exact_and_never_refreshes():
    s = ready_case("cmd")
    FakeProc.started.clear()
    assert post("/api/case/build", {"slug": s, "confirm": token(s)}).status_code == 200
    cmd = FakeProc.started[0].cmd
    FakeProc.started[0].code = 0
    assert cmd == ["uv", "run", "--directory", str(ROOT), "python", "-u", "loader.py", "--case", s], cmd
    assert not any(a.startswith("--ref") for a in cmd) and "--rebuild" not in cmd
    assert not any(a.startswith("--ref") for a in w.build_cmd("zz-web-any"))


def test_one_build_at_a_time_and_a_failed_ram_check_refuses():
    s, s2 = ready_case("one"), ready_case("two")
    FakeProc.started.clear()
    assert post("/api/case/build", {"slug": s, "confirm": token(s)}).status_code == 200
    r = post("/api/case/build", {"slug": s2, "confirm": token(s2)})
    assert r.status_code == 409 and "one build at a time" in r.text and len(FakeProc.started) == 1
    FakeProc.started[0].code = 0  # the first one ends
    w.other_build_running = lambda: True  # a terminal build holds its Redis lock
    try:
        r = post("/api/case/build", {"slug": s2, "confirm": token(s2)})
        assert r.status_code == 409 and "lock" in r.text
    finally:
        w.other_build_running = lambda: False
    w.ram_check = lambda slug: {"estimate_mb": 1000, "source": "x", "problem": "not enough RAM: test"}
    try:
        r = post("/api/case/build", {"slug": s2, "confirm": token(s2)})
        assert r.status_code == 409 and "RAM check failed" in r.text
    finally:
        install_fakes()
    assert len(FakeProc.started) == 1


def test_status_hides_every_log_line_with_the_key():
    s = ready_case("log")
    FakeProc.started.clear()
    assert post("/api/case/build", {"slug": s, "confirm": token(s)}).status_code == 200
    (L.CASES / s / "status.json").write_text(json.dumps({"status": "failed", "error": f"RuntimeError: key {KEY}"}))
    j = CLIENT.get("/api/case/status", params={"slug": s}).json()
    assert j["running"] is True and j["returncode"] is None
    text = json.dumps(j)
    assert KEY not in text and "[line hidden: it held the API key]" in j["log"] and "fetching works" in j["log"]
    assert "RAM check passed" in j["log"] and KEY not in str(j["error"])
    FakeProc.started[0].code = 0
    real = w.server.list_cases
    w.server.list_cases = lambda: {"ok": True, "data": {"cases": [{"case": s, "graph_exists": True}]}}
    try:
        j = CLIENT.get("/api/case/status", params={"slug": s}).json()
    finally:
        w.server.list_cases = real
    assert j["running"] is False and j["returncode"] == 0 and j["listed"] is True


def test_a_log_cut_inside_the_key_never_shows_a_fragment():
    # Reviewer round 1 must-fix: the tail cut can land mid-key, leaving a fragment that `key in line` misses.
    s = ready_case("cut")
    FakeProc.started.clear()
    assert post("/api/case/build", {"slug": s, "confirm": token(s)}).status_code == 200
    half = len(KEY) // 2
    head = ("x" * 100 + KEY + "\n").encode()
    after_cut = len(KEY) - half + 1           # rest of the key plus its newline
    line_ = b"fetching works ok " + b"." * 982 + b"\n"   # long lines, so the cut line is among the last 200
    filler = line_ * ((w.LOG_TAIL - after_cut) // len(line_))
    filler += b"y" * (w.LOG_TAIL - after_cut - len(filler))   # so exactly LOG_TAIL bytes follow the cut
    data = b"z" * 50 + b"\n" + head + filler
    cut, k0 = len(data) - w.LOG_TAIL, data.find(KEY.encode())
    assert k0 < cut < k0 + len(KEY), (k0, cut)
    w._build["log"].write_bytes(data)
    j = CLIENT.get("/api/case/status", params={"slug": s}).json()
    log = "\n".join(j["log"])   # the log comes back as a list of lines
    assert KEY[half:] not in log and KEY[-8:] not in log and "fetching works ok" in log
    FakeProc.started[0].code = 0


def test_errors_never_carry_the_key():
    def boom(q):
        raise RuntimeError(f"OpenAlex said no to {KEY}")
    w.discover.funders = boom
    try:
        r = post("/api/case/discover", {"slug": slug("err"), "company": "Zz", "topic": "zz"})
    finally:
        install_fakes()
    assert r.status_code == 502 and KEY not in r.text


def test_other_origins_cannot_reach_the_wizard():
    s = slug("origin")
    for url, body in (("/api/case/discover", {"slug": s, "company": "Zz", "topic": "zz"}), ("/api/case/write", write_body(s)),
                      ("/api/case/estimate", {"slug": s}), ("/api/case/build", {"slug": s, "confirm": "x"})):
        for origin in (None, "null", "https://evil.example", "http://127.0.0.1:9999"):
            assert post(url, body, origin=origin).status_code == 403, (url, origin)
    assert not (L.CASES / s).exists()


def test_the_models_tools_are_unchanged_and_no_build_tool_is_exposed():
    assert w.ALLOWED == ["resolve_experts", "find_experts", "tie_report", "check_no_tie", "bridges", "check_case",
                         "list_cases", "open_case", "recall", "recheck", "draft_note"]
    assert w.HUMAN_ONLY == ["record_verdict", "approve_note"] and "build_case" not in w.ALLOWED + w.HUMAN_ONLY
    cmd = " ".join(w.claude_cmd("/tmp/x.json"))
    assert not re.search(r"build|discover|case/|loader", cmd.replace("check_case", "").replace("list_cases", "")
                         .replace("open_case", ""), re.I), cmd
    import server
    assert not any(n in dir(server) for n in ("build_case", "create_case", "write_case"))
    from web.agent import loop
    assert loop.ALLOWED is w.ALLOWED


def test_no_banned_words_on_the_new_ui():
    index = CLIENT.get("/").text
    assert 'id="new-case"' in index and 'id="wizard"' in index and "case.js" in index
    for url in ("/", "/case.js", "/style.css"):
        r = CLIENT.get(url)
        assert r.status_code == 200 and not BANNED.search(r.text), url


def render_wizard(reply):
    """Runs the real page (from files, in headless Chrome) with a fake /api/case/discover that answers `reply`, opens
    the wizard, fills step 1 and submits. Returns what the page drew, or None when Chrome is not installed."""
    import html as _html
    import os as _os
    import tempfile
    chrome = next((c for c in ("google-chrome", "chromium", "chromium-browser") if shutil.which(c)), None)
    if not chrome:
        return None
    d = Path(tempfile.mkdtemp(prefix="zz-web-case-"))
    try:
        for n in ("index.html", "app.js", "case.js", "style.css", "landing.css", "landing.js"):
            shutil.copy(w.STATIC / n, d / n)
        (d / "pre.js").write_text("window.__bodies = []; window.__errors = [];\n"
                                  "addEventListener('error', (e) => window.__errors.push(String(e.message)));\n"
                                  f"const REPLY = {json.dumps(reply)};\n"
                                  "window.fetch = async (url, opt) => { window.__bodies.push(url);\n"
                                  "  return new Response(JSON.stringify(url === '/api/case/discover' ? REPLY : {ok: false, providers: []})); };\n",
                                  encoding="utf-8")
        (d / "harness.js").write_text(r"""
document.getElementById("new-case").click();
const set = (id, v) => { const e = document.getElementById(id); e.value = v; e.dispatchEvent(new Event("input")); };
set("wz-company", "Philip Morris"); set("wz-topic", "lung cancer");
const suggested = document.getElementById("wz-slug").value;
set("wz-slug", "zz-web-render");
document.getElementById("wz-describe").requestSubmit();
setTimeout(() => {
  const pick = document.getElementById("wz-pick");
  const out = document.createElement("pre"); out.id = "RESULT";
  out.textContent = JSON.stringify({ wizard: !document.getElementById("wizard").hidden,
    thread_hidden: getComputedStyle(document.getElementById("messages")).display === "none", suggested,
    y1: document.getElementById("wz-y1").value, cards: pick.querySelectorAll(".wz-cand").length,
    checked: pick.querySelectorAll("input:checked").length, parents: [...pick.querySelectorAll("select")].filter((s) => s.value === "parent").length,
    text: pick.textContent, errors: window.__errors, calls: window.__bodies });
  document.body.append(out);
}, 500);
""", encoding="utf-8")
        page = (d / "index.html").read_text(encoding="utf-8")
        page = page.replace('<script src="app.js"></script>', '<script src="pre.js"></script><script src="app.js"></script>')
        page = page.replace('<script src="landing.js"></script>', '<script src="landing.js"></script><script src="harness.js"></script>')
        (d / "test.html").write_text(page, encoding="utf-8")
        dom = _os.popen(f"timeout 60 {chrome} --headless=new --disable-gpu --no-sandbox --user-data-dir={d / 'profile'} "
                        f"--window-size=1280,900 --virtual-time-budget=5000 --dump-dom file://{d / 'test.html'} 2>/dev/null").read()
    finally:
        shutil.rmtree(d, ignore_errors=True)
    m = re.search(r'<pre id="RESULT">(.*?)</pre>', dom, re.S)
    assert m, dom[-800:]
    return json.loads(_html.unescape(m.group(1)))


def test_the_wizard_draws_the_candidates_with_nothing_ticked():
    reply = {"ok": True, "slug": "zz-web-render", "funders": fake_funders("x")["candidates"],
             "institutions": fake_institutions("x")["candidates"], "topics": fake_topics("x")["candidates"],
             "phrase_works": 7, "api_calls": 5, "cost_usd": 0.004, "cost_note": w.COST_NOTE, "defaults": w.CASE_DEFAULTS}
    got = render_wizard(reply)
    if got is None:
        print("  (no Chrome: skipped)")
        return
    assert got["wizard"] and got["thread_hidden"] and not got["errors"], got
    assert got["suggested"] == "philip-morris-lung" and got["y1"] == str(time.localtime().tm_year)
    assert got["cards"] == 5 and got["checked"] == 0 and got["parents"] == 0, got
    assert "Zz Test Foundation" in got["text"] and "about $0.004" in got["text"] and not BANNED.search(got["text"])
    assert got["calls"].count("/api/case/discover") == 1


# ---------- runner ----------

if __name__ == "__main__":
    only = sys.argv[1:]
    tests = [(n, f) for n, f in list(globals().items()) if n.startswith("test_") and callable(f)
             and (not only or any(o in n for o in only))]
    failed = 0
    try:
        for name, fn in tests:
            t0 = time.time()
            try:
                fn()
                print(f"PASS  {name}  ({time.time() - t0:.1f}s)")
            except Exception as e:
                failed += 1
                lines = [f.lineno for f in traceback.extract_tb(e.__traceback__) if f.filename == __file__]
                print(f"FAIL  {name}  (line {lines[-1] if lines else '?'}): {(str(e) or type(e).__name__)[:500]}")
    finally:
        for d in L.CASES.glob("zz-web-*"):
            shutil.rmtree(d, ignore_errors=True)
        for f in w.RUN_DIR.glob("build-zz-web-*.log"):
            f.unlink(missing_ok=True)
    print(f"\n{len(tests) - failed}/{len(tests)} passed, {failed} failed")
    sys.exit(1 if failed else 0)
