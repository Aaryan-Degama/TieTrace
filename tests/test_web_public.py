"""Public demo mode (TIETRACE_PUBLIC=1, W7; DEPLOY.md). Offline: TestClient, a fake provider and a fake MCP client;
no claude, no OpenAlex, no build, no FalkorDB needed. Scratch newsroom zz-test-web-public (never alpha).
Run:  uv run python tests/test_web_public.py
Written by Aaryan's Builder, Oct 18 2026."""
import os
import subprocess
import sys
import time
import traceback
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["TIETRACE_NEWSROOM"] = "zz-test-web-public"
for k in ("TIETRACE_PUBLIC", "TIETRACE_TRUST_PROXY", "TIETRACE_ALLOWED_HOSTS", "TIETRACE_DEPLOYED"):
    os.environ.pop(k, None)
os.environ["TIETRACE_CLAUDE"] = "zz-no-such-claude"  # /api/login and /api/auth can never start the real claude

from fastapi.testclient import TestClient  # noqa: E402

import web.app as w  # noqa: E402
from web.agent.providers import ToolSpec, Turn  # noqa: E402

ORIGIN = "http://127.0.0.1:8765"  # TestClient never opens a port; any number works
CLI = TestClient(w.app, base_url=ORIGIN, headers={"Origin": ORIGIN})
SITE = "https://tietrace.example.org"
STORY = {"provider": "gemini", "story": "Coca-Cola and obesity."}

# Every route public mode turns off, with a body that would pass validation when the flag is off.
BLOCKED = [
    ("POST", "/api/ask", {"story": "x"}, w.ASK_OFF),
    ("POST", "/api/login", {}, w.ASK_OFF),
    ("GET", "/api/auth", None, w.ASK_OFF),
    ("POST", "/api/confirm", {"action": "approve_note", "params": {"note_id": "n1", "reviewer": "R"}}, w.APPROVALS_OFF),
    ("POST", "/api/approve_note", {"note_id": "n1", "reviewer": "R"}, w.APPROVALS_OFF),
    ("POST", "/api/record_verdict", {"type": "PAPER_EXCLUDED", "target": "10.1/x", "reason": "r", "reviewer": "R"},
     w.APPROVALS_OFF),
    ("POST", "/api/case/discover", {"slug": "!bad", "company": "Acme", "topic": "x"}, w.CASES_OFF),
    ("POST", "/api/case/write", {"slug": "!bad", "company": "Acme", "years": [2010, 2020], "reviewer": "R"},
     w.CASES_OFF),
    ("POST", "/api/case/estimate", {"slug": "!bad"}, w.CASES_OFF),
    ("POST", "/api/case/build", {"slug": "!bad"}, w.CASES_OFF),
    ("GET", "/api/case/status?slug=!bad", None, w.CASES_OFF),
]


class FakeProvider:
    async def turn(self, system, messages, tools, key):
        return Turn("fine", [], "end", 1, 1)


class FakeClient:
    async def tools(self):
        return [ToolSpec("list_cases", "d", {"type": "object", "properties": {}})]

    async def call(self, name, args):
        return {"ok": True, "data": {}, "cypher": [], "scope": {}, "verdicts_applied": [], "warnings": []}


@contextmanager
def mode(env):
    """Env vars set and restored; fakes injected; the rate-limit tables emptied before and after."""
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    w.AGENT_PROVIDER_FACTORY, w.AGENT_CLIENT_FACTORY = (lambda n: FakeProvider()), FakeClient
    w._ip_runs.clear(), w._ip_active.clear()
    try:
        yield
    finally:
        w.AGENT_PROVIDER_FACTORY = w.AGENT_CLIENT_FACTORY = None
        w._ip_runs.clear(), w._ip_active.clear()
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def call(method, path, body, client=CLI, **headers):
    return client.request(method, path, json=body, headers=headers) if body is not None else \
        client.request(method, path, headers=headers)


def ask(client=CLI, **headers):
    return client.post("/api/agent/ask", json=STORY, headers={"X-Provider-Key": "CANARY-KEY-9", **headers})


PUBLIC = {"TIETRACE_PUBLIC": "1"}


def test_every_blocked_route_returns_403_in_public_mode():
    with mode(PUBLIC):
        for method, path, body, msg in BLOCKED:
            r = call(method, path, body)
            assert r.status_code == 403, (path, r.status_code)
            assert r.json()["error"]["message"] == msg, (path, r.text)
            r = call(method, path.split("?")[0] + "/", body)  # a trailing slash does not get around it
            assert r.status_code == 403, (path + "/", r.status_code)


def test_the_blocked_routes_are_unchanged_when_the_flag_is_off():
    for flag in ({}, {"TIETRACE_PUBLIC": "0"}):
        with mode(flag):
            for method, path, body, msg in BLOCKED:
                r = call(method, path, body)
                assert msg not in r.text, (path, r.text)
            assert call("POST", "/api/confirm", BLOCKED[3][2]).json()["ok"] is True  # a real token, as before
            assert call("GET", "/api/auth", None).json() == {"ok": True, "logged_in": None}
            assert call("POST", "/api/ask", {"story": "  "}).status_code == 400
            assert call("POST", "/api/approve_note", BLOCKED[4][2]).json()["error"]["message"].startswith("confirm on")
            assert call("POST", "/api/case/estimate", {"slug": "!bad"}).status_code == 400


def test_public_mode_refuses_to_start_unless_the_newsroom_starts_with_demo():
    def start(newsroom):
        env = {k: v for k, v in os.environ.items() if k != "TIETRACE_NEWSROOM"}
        env["TIETRACE_PUBLIC"] = "1"
        if newsroom is not None:
            env["TIETRACE_NEWSROOM"] = newsroom
        return subprocess.run([sys.executable, "-c", "import web.app"], cwd=ROOT, env=env, capture_output=True,
                              text=True, timeout=120)
    for bad in ("alpha", None, "zz-test-web-public", "xdemo"):  # None: unset, so the app's default alpha
        r = start(bad)
        assert r.returncode != 0 and "refusing to start" in r.stderr, (bad, r.returncode, r.stderr[-300:])
    r = start("demo-zz-test-web-public")
    assert r.returncode == 0, r.stderr[-500:]


def test_the_per_ip_limit_counts_runs_per_hour():
    with mode(PUBLIC):
        for i in range(w.RATE_PER_HOUR):
            assert ask().status_code == 200, i
        r = ask()
        assert r.status_code == 429 and r.json()["error"]["code"] == "RATE_LIMIT", r.text
        assert "per hour" in r.json()["error"]["message"]
    with mode({}):  # flag off: no limit, as before
        for i in range(w.RATE_PER_HOUR + 2):
            assert ask().status_code == 200, i


def test_x_forwarded_for_is_ignored_unless_trust_proxy_is_set():
    with mode(PUBLIC):
        for i in range(w.RATE_PER_HOUR):
            assert ask(**{"X-Forwarded-For": f"10.0.0.{i}"}).status_code == 200
        assert ask(**{"X-Forwarded-For": "10.9.9.9"}).status_code == 429  # same peer: a forged header does not help
    with mode({**PUBLIC, "TIETRACE_TRUST_PROXY": "1"}):
        for i in range(w.RATE_PER_HOUR):
            assert ask(**{"X-Forwarded-For": "6.6.6.6, 1.2.3.4"}).status_code == 200
        assert ask(**{"X-Forwarded-For": "6.6.6.6, 1.2.3.4"}).status_code == 429
        assert ask(**{"X-Forwarded-For": "1.2.3.4, 5.6.7.8"}).status_code == 200  # the last hop is the client
        assert set(w._ip_runs) == {"1.2.3.4", "5.6.7.8"}  # never the visitor-written first hop


def test_client_ip_takes_the_last_hop_only_when_trusted():
    class R:
        def __init__(self, xff):
            from starlette.datastructures import Headers
            self.headers = Headers(raw=[(b"x-forwarded-for", v.encode()) for v in xff])
            self.client = type("C", (), {"host": "9.9.9.9"})()
    with mode({}):
        assert w._client_ip(R(["1.1.1.1, 2.2.2.2"])) == "9.9.9.9"
    with mode({"TIETRACE_TRUST_PROXY": "1"}):
        assert w._client_ip(R(["1.1.1.1, 2.2.2.2"])) == "2.2.2.2"
        assert w._client_ip(R(["1.1.1.1", "3.3.3.3"])) == "3.3.3.3"  # two header lines: still the last hop
        assert w._client_ip(R([])) == "9.9.9.9"
        assert w._client_ip(R(["1.1.1.1, "])) == "9.9.9.9"  # an empty last hop falls back to the peer


def test_at_most_one_run_at_once_per_ip():
    assert w.RATE_AT_ONCE == 1 < w.MAX_RUNS  # one visitor can never hold every global slot
    with mode(PUBLIC):
        a, _ = w._rate_take("7.7.7.7")
        c, msg = w._rate_take("7.7.7.7")
        assert a and c is None and "already running" in msg
        assert w._rate_take("8.8.8.8")[0]  # another address is not affected
        w._rate_release("7.7.7.7", a)
        assert w._rate_take("7.7.7.7")[0]
        w._ip_active["7.7.7.7"] = [time.time() - 1]  # a run that never released has expired: its slot is free
        assert w._rate_take("7.7.7.7")[0]
        r = ask()  # a finished stream releases its slot
        assert r.status_code == 200 and not w._ip_active.get("testclient")


def test_the_service_turns_off_uvicorns_own_proxy_headers():
    """uvicorn's default trusts X-Forwarded-For from 127.0.0.1 (so also from an SSH tunnel) and rewrites
    request.client from it, which would make the per-IP limit forgeable without TIETRACE_TRUST_PROXY."""
    unit = (ROOT / "deploy/tietrace.service").read_text(encoding="utf-8")
    start = next(ln for ln in unit.splitlines() if ln.startswith("ExecStart="))
    assert "--no-proxy-headers" in start and " --proxy-headers" not in start, start
    assert "--host 127.0.0.1 --port 8000" in start
    for f in ("Caddyfile", "Caddyfile.ip-only"):  # no low-latency mode, and no secret read from the environment
        text = (ROOT / "deploy" / f).read_text(encoding="utf-8")
        assert "flush_interval" not in [ln.strip().split(" ")[0] for ln in text.splitlines()], f
        assert "{$" not in text and "USER_HERE HASH_HERE" in text, f


def test_the_https_origin_matches_behind_the_proxy_only_with_trust_proxy():
    """With --no-proxy-headers the app sees http from Caddy; the browser's Origin is https. X-Forwarded-Proto
    (last hop) is used for the Origin check only with TIETRACE_TRUST_PROXY=1."""
    plain = TestClient(w.app, base_url="http://tietrace.example.org", headers={"Origin": SITE})
    hosts = {"TIETRACE_ALLOWED_HOSTS": "tietrace.example.org"}
    with mode({**PUBLIC, **hosts}):
        assert ask(client=plain, **{"X-Forwarded-Proto": "https"}).status_code == 403  # header not trusted
    with mode({**PUBLIC, **hosts, "TIETRACE_TRUST_PROXY": "1"}):
        assert ask(client=plain, **{"X-Forwarded-Proto": "https"}).status_code == 200
        assert ask(client=plain, **{"X-Forwarded-Proto": "https, http"}).status_code == 403  # the last hop counts
        assert ask(client=plain).status_code == 403  # no header: the connection's own http
        assert ask(client=plain, **{"X-Forwarded-Proto": "gopher"}).status_code == 403


def test_allowed_hosts():
    with mode({}):
        assert w._allowed_hosts() == {"127.0.0.1", "localhost"}
    with mode({"TIETRACE_ALLOWED_HOSTS": " TieTrace.example.org, 203.0.113.7 ,*,, "}):
        assert w._allowed_hosts() == {"127.0.0.1", "localhost", "tietrace.example.org", "203.0.113.7"}
    site = TestClient(w.app, base_url=SITE, headers={"Origin": SITE})
    with mode(PUBLIC):
        assert ask(client=site).status_code == 403  # not allowed yet
        assert site.get("/api/agent/providers").status_code == 403
        assert ask().status_code == 200  # localhost (the SSH tunnel) stays allowed
    with mode({**PUBLIC, "TIETRACE_ALLOWED_HOSTS": "tietrace.example.org"}):
        assert ask(client=site).status_code == 200
        assert site.get("/api/agent/providers").status_code == 200
        assert ask(client=site, Origin="https://evil.example").status_code == 403
        assert ask(client=site, Origin="http://tietrace.example.org").status_code == 403  # scheme must match
        evil = TestClient(w.app, base_url="https://evil.example", headers={"Origin": "https://evil.example"})
        assert ask(client=evil).status_code == 403 and evil.get("/api/agent/providers").status_code == 403
    with mode({**PUBLIC, "TIETRACE_ALLOWED_HOSTS": "*"}):
        assert ask(client=site).status_code == 403  # "*" never allows everything


def test_the_providers_list_says_no_local_claude_and_no_host_key():
    with mode({**PUBLIC, "TIETRACE_HOST_GEMINI": "1", "GEMINI_API_KEY": "HOST-SECRET-1",
               "TIETRACE_CLAUDE": sys.executable}):  # an existing command: local_claude would be true without the flag
        j = CLI.get("/api/agent/providers").json()
        assert j["local_claude"] is False and j["public"] is True and j["host_gemini"] is False
        r = CLI.post("/api/agent/ask", json=STORY)  # no key: the host key is never used on the public demo
        assert r.status_code == 400 and r.json()["error"]["code"] == "NO_KEY"
    with mode({"TIETRACE_CLAUDE": sys.executable}):
        j = CLI.get("/api/agent/providers").json()
        assert j["local_claude"] is True and j["public"] is False


def test_the_page_has_the_banner_and_hides_the_public_off_buttons():
    page = (ROOT / "web/static/index.html").read_text(encoding="utf-8")
    js = (ROOT / "web/static/app.js").read_text(encoding="utf-8")
    assert 'id="public-banner" role="note" hidden>Public demo: bring your own API key. Your key and story are not ' \
           'saved on the server; check results go into a shared demo memory that may be reset.</p>' in page
    assert '$("public-banner").hidden = !publicDemo' in js and '$("new-case").hidden = publicDemo' in js
    assert "Approvals are turned off on the public demo" in js and "if (!publicDemo) li.append(excludeButton" in js
    assert 'd.status === "verified" && publicDemo' in js


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
    finally:  # the scratch newsrooms only (zz-test-web-public, demo-zz-test-web-public)
        if w.falkordb_up():
            from research import _conn
            for g in _conn().list_graphs():
                if g.startswith("newsroom:") and g.endswith("zz-test-web-public"):
                    _conn().select_graph(g).delete()
    print(f"\n{len(tests) - failed}/{len(tests)} passed, {failed} failed")
    sys.exit(1 if failed else 0)
