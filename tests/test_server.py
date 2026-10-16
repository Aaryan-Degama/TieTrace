"""Tests for server.py (checkpoint 5). Written by the Reviewer.

Run:  FALKORDB_PORT=6380 uv run python tests/test_server.py [name-substring ...]

The REAL server is spawned the way Claude Code spawns it (`uv run --directory <repo> python server.py`,
stdio) and driven with the mcp SDK client. Expected values are recomputed WITHOUT server.py: the
independent graph model in tests/test_research.py (raw nodes + plain Python) and direct Cypher.
A failing test means the server breaks the spec or CLAUDE.md; it is reported, not hidden, and tests
must not be weakened to make them pass.
"""
import asyncio
import contextlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("FALKORDB_PORT", "6379")

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

import research  # noqa: E402
import test_research as tr  # noqa: E402  (independent model of the graph: tr.expected, tr.sample, tr.q)

BANNED = re.compile(r"\b(independent|conflicted)\b", re.IGNORECASE)
ERROR_CODES = {"INVALID_ARG", "NOT_FOUND", "AMBIGUOUS", "TIMEOUT", "ROW_LIMIT", "VERIFIER_REJECT",
               "NOT_APPROVED", "READ_ONLY", "DB_ERROR", "INTERNAL", "NO_GRAPH"}  # NO_GRAPH: spec s4, checkpoint 5d
TOOLS = {"resolve_experts", "find_experts", "tie_report", "check_no_tie", "bridges", "check_case",
         "list_cases",  # list_cases: spec s5, checkpoint 5d
         "open_case", "recall", "recheck"}  # Track 2 memory tools: spec s5, checkpoint 6
# Checkpoint 6 (spec s4, s5, 9d): every tool takes the optional `agent` (spec s4 "Logging"); tie_report,
# check_no_tie and bridges take an optional case_id; check_case takes case_id OR expert_ids (spec s5 "In: case_id").
SPEC_INPUTS = {  # name -> (required, allowed) according to docs/mcp-spec.md sections 4, 5, 9b, 9c, 9d
    "resolve_experts": ({"names", "topic"}, {"names", "topic", "case", "agent"}),
    "find_experts": ({"topic"}, {"topic", "n", "exclude_target_staff", "case", "agent"}),
    "tie_report": ({"expert_id"}, {"expert_id", "expert_number", "case", "case_id", "agent"}),
    "check_no_tie": ({"expert_id"}, {"expert_id", "k", "years", "expert_number", "case", "case_id", "agent"}),
    "bridges": ({"expert_id"}, {"expert_id", "depth", "limit", "expert_number", "case", "case_id", "agent"}),
    "check_case": (set(), {"expert_ids", "public_case", "case", "case_id", "agent"}),
    "list_cases": (set(), {"agent"}),
    "open_case": ({"story", "company", "topic", "expert_ids"},
                  {"story", "company", "topic", "expert_ids", "public_case", "case", "agent"}),
    "recall": (set(), {"expert_id", "case_id", "session_id", "limit", "agent"}),
    "recheck": ({"check_id"}, {"check_id", "agent"}),
}
# Tools that save memory records (spec s5: tie_report/check_case with case_id save Checks); never destructive.
WRITE_TOOLS = {"tie_report", "check_case", "open_case", "recheck"}
GEBN = {"James O. Hill": "A5031318756", "Steven N. Blair": "A5105003630",
        "Gregory A. Hand": "A5046953968", "John C. Peters": "A5045252457"}
CMD = ["uv", "run", "--directory", str(ROOT), "python", "server.py"]
NEWSROOM = "zz-test-srv"  # a scratch newsroom (checkpoint 6: every call writes an Event); deleted at the end


def server_env(**over):
    env = dict(os.environ, TIETRACE_NEWSROOM=NEWSROOM, FALKORDB_PORT=os.environ["FALKORDB_PORT"])
    env.update(over)
    return {k: v for k, v in env.items() if v is not None}


# ---------- one long-lived client session, driven from plain sync tests ----------

class Client:
    """Spawns the server and runs the SDK client on its own event-loop thread."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.stack = contextlib.AsyncExitStack()
        t = time.time()
        self.session = self._run(self._open())
        self.init_seconds = time.time() - t

    async def _open(self):
        params = StdioServerParameters(command=CMD[0], args=CMD[1:], env=server_env())
        r, w = await self.stack.enter_async_context(stdio_client(params))
        s = await self.stack.enter_async_context(ClientSession(r, w))
        self.init_result = await s.initialize()
        return s

    def _run(self, coro, timeout=300):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def raw(self, name, args):
        """The SDK result object (is_error, structured_content, content)."""
        return self._run(self.session.call_tool(name, args))

    def call(self, name, args=None):
        """The envelope (dict). Fails the test if the SDK returned a protocol-level tool error."""
        res = self.raw(name, args or {})
        assert not res.is_error, f"{name}{args}: tool error {res.content[0].text[:200]}"
        return res.structured_content

    def start(self, name, args):
        """Fire a call without waiting; returns a concurrent future."""
        return asyncio.run_coroutine_threadsafe(self.session.call_tool(name, args), self.loop)

    def tools(self):
        return self._run(self.session.list_tools()).tools

    def close(self):
        with contextlib.suppress(Exception):
            self._run(self.stack.aclose(), 60)
        self.loop.call_soon_threadsafe(self.loop.stop)


_C = []


def client():
    if not _C:
        _C.append(Client())
    return _C[0]


def server_processes():
    """Command lines of running python processes started from this repo's server.py."""
    cmd = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(python|uv)' -and "
           "$_.CommandLine -like '*server.py*' } | ForEach-Object { $_.ProcessId }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=60).stdout
    return {int(x) for x in out.split()}


def envelope_ok(out, where):
    assert isinstance(out, dict), (where, type(out))
    if out.get("ok") is True:
        assert set(out) >= {"ok", "data", "cypher", "scope", "verdicts_applied", "warnings"}, (where, sorted(out))
        assert isinstance(out["data"], dict) and isinstance(out["scope"], dict), where
        assert isinstance(out["verdicts_applied"], list) and isinstance(out["warnings"], list), where
    else:
        assert out.get("ok") is False, (where, out)
        err = out.get("error")
        assert isinstance(err, dict) and set(err) == {"code", "message"}, (where, out)
        assert err["code"] in ERROR_CODES, (where, err)
        assert isinstance(err["message"], str) and err["message"], where
    assert isinstance(out["cypher"], list), where
    for c in out["cypher"]:
        assert set(c) >= {"graph", "query", "params"}, (where, c)
        assert c["graph"] == research.GRAPH, (where, c["graph"])
        assert isinstance(c["query"], str) and c["query"].strip(), where


LEAK = re.compile(r"Traceback|File \"|\.py\"|C:\\|Users\\|OPENALEX_|api[_-]?key", re.IGNORECASE)


def check_text_clean(out, where):
    text = json.dumps(out, ensure_ascii=False)
    assert not LEAK.search(text), (where, LEAK.search(text).group(0))
    assert not BANNED.search(text), (where, BANNED.search(text).group(0))
    assert "newsroom" not in text.lower() and "alpha" not in text.lower().replace("alphabet", ""), where


def env_key_value():
    """The OpenAlex key (if .env exists), only to prove it is never echoed. Never printed."""
    p = ROOT / ".env"
    if p.exists():
        m = re.search(r"OPENALEX_API_KEY\s*=\s*(\S+)", p.read_text())
        if m and len(m.group(1)) > 6:
            return m.group(1).strip("'\"")
    return None


_SAMPLE = {}


def picked():
    """~18 experts spanning every tier (seeded), with the independently computed (tier, score)."""
    if not _SAMPLE:
        m = tr.model()
        by = {"A1": [], "A2": [], "B": [], "C": [], "NONE": []}
        for pid in tr.sample():
            by[tr.expected(pid)[0]].append(pid)
        ids = []
        for t, n in (("A1", 4), ("A2", 3), ("B", 4), ("C", 4), ("NONE", 4)):
            assert len(by[t]) >= n, (t, len(by[t]))
            ids += by[t][:n]
        _SAMPLE["ids"] = ids
        _SAMPLE["model"] = m
    return _SAMPLE["ids"]


# ---------- 1. tool list, schemas, descriptions ----------

def test_exactly_the_spec_tools_with_spec_inputs_and_read_only_hints():
    tools = {t.name: t for t in client().tools()}
    assert set(tools) == TOOLS, sorted(tools)
    for name, t in tools.items():
        req, allowed = SPEC_INPUTS[name]
        props = set(t.input_schema["properties"])
        assert props == allowed, (name, props)
        assert set(t.input_schema.get("required", [])) == req, name
        a = t.annotations
        assert a and a.read_only_hint is (name not in WRITE_TOOLS) and a.destructive_hint is False, name
        assert t.description and 80 < len(t.description) < 1800, (name, len(t.description))
        assert t.output_schema is not None, name


def test_no_tool_takes_a_newsroom_or_a_name():
    for t in client().tools():
        props = set(t.input_schema["properties"])
        # "agent" is allowed since checkpoint 6 (spec s4: attribution in the Event log only, never echoed)
        assert not props & {"newsroom", "newsroom_id", "name", "label", "graph"}, (t.name, props)
    s = client().raw("find_experts", {"topic": "obesity", "n": 2, "newsroom": "beta"})
    assert not s.is_error  # extra args are dropped, never used
    assert "beta" not in json.dumps(s.structured_content)


def test_descriptions_and_instructions_banned_words():
    """The banned words are an OUTPUT rule (CLAUDE.md). Descriptions may only mention them inside the one
    prohibition sentence; anything else is a leak. (Quoting them at all is flagged in the review report.)"""
    c = client()
    texts = [c.init_result.instructions or ""] + [t.description for t in c.tools()]
    for t in c.tools():
        texts.append(json.dumps(t.input_schema))
    for text in texts:
        rest = text.replace("Never use the words 'independent' or 'conflicted'.", "")
        assert not BANNED.search(rest), BANNED.search(rest).group(0)


def test_descriptions_do_not_quote_the_banned_words_at_all():
    """Strict reading: model-visible tool text should not contain the banned words even in a prohibition."""
    c = client()
    texts = [c.init_result.instructions or ""] + [t.description for t in c.tools()]
    for text in texts:
        assert not BANNED.search(text), "banned word appears in model-visible tool text: " + BANNED.search(text).group(0)


# ---------- 2. envelope on every tool, success and error paths ----------

def test_envelope_on_every_tool_success():
    ids = picked()
    c = client()
    calls = [("resolve_experts", {"names": ["Steven N. Blair", "Nobody Atall"], "topic": "obesity"}),
             ("find_experts", {"topic": "obesity", "n": 5}),
             ("find_experts", {"topic": "T10352", "n": 3, "exclude_target_staff": False}),
             ("tie_report", {"expert_id": ids[0]}), ("check_no_tie", {"expert_id": ids[-1]}),
             ("bridges", {"expert_id": ids[8], "depth": 3}), ("check_case", {"expert_ids": ids[:5]})]
    for name, args in calls:
        out = c.call(name, args)
        envelope_ok(out, (name, args))
        assert out["ok"] is True, (name, out.get("error"))
        assert out["cypher"], name  # the executed Cypher is listed
        check_text_clean(out, name)


def test_envelope_on_every_error_path():
    ids = picked()
    c = client()
    bad = [("resolve_experts", {"names": [], "topic": "obesity"}), ("resolve_experts", {"names": ["x"], "topic": " "}),
           ("find_experts", {"topic": ""}), ("find_experts", {"topic": "obesity", "n": 0}),
           ("find_experts", {"topic": "obesity", "n": 101}), ("find_experts", {"topic": "and"}),
           ("tie_report", {"expert_id": "A0"}), ("tie_report", {"expert_id": " "}),
           ("tie_report", {"expert_id": ids[0], "expert_number": 0}), ("tie_report", {"expert_id": ids[0], "expert_number": 100}),
           ("check_no_tie", {"expert_id": ids[0], "k": 4}), ("check_no_tie", {"expert_id": ids[0], "years": 9}),
           ("check_no_tie", {"expert_id": "A0"}), ("bridges", {"expert_id": ids[0], "depth": 4}),
           ("bridges", {"expert_id": ids[0], "limit": 0}), ("bridges", {"expert_id": "A0"}),
           ("check_case", {"expert_ids": []}), ("check_case", {"expert_ids": ["a", "a"]}),
           ("check_case", {"expert_ids": ["a", " "]}), ("check_case", {"expert_ids": [f"A{i}" for i in range(26)]})]
    for name, args in bad:
        out = c.call(name, args)
        envelope_ok(out, (name, args))
        assert out["ok"] is False, (name, args, "should be an error")
        check_text_clean(out, (name, args))
        want = "NOT_FOUND" if args.get("expert_id") == "A0" else "INVALID_ARG"
        assert out["error"]["code"] == want, (name, args, out["error"])


def test_wrong_typed_args_are_rejected_by_the_sdk_before_our_code():
    """Documented in spec 9b: pydantic rejects them (is_error) with a short message. Judge: acceptable, no leak."""
    c = client()
    for name, args in [("tie_report", {"expert_id": 123}), ("check_case", {"expert_ids": "abc"}),
                       ("find_experts", {}), ("resolve_experts", {"names": "Bob", "topic": "obesity"}),
                       ("tie_report", {"expert_id": "A1", "expert_number": "Dr X"}),
                       ("check_case", {"expert_ids": ["A1"], "public_case": "maybe"})]:
        r = c.raw(name, args)
        assert r.is_error, (name, args)
        text = r.content[0].text
        assert not re.search(r"Traceback|File \"|C:\\", text), text[:300]
        assert len(text) < 600, len(text)
    # lenient coercions the model will rely on: "5" -> 5 and "3" -> 3 are accepted
    assert c.call("find_experts", {"topic": "obesity", "n": "2"})["ok"]
    assert c.call("tie_report", {"expert_id": picked()[0], "expert_number": "3"})["data"]["label"] == "Expert #3"


def test_no_secret_in_any_output():
    key = env_key_value()
    ids = picked()
    c = client()
    blob = json.dumps([c.call("tie_report", {"expert_id": ids[0]}), c.call("find_experts", {"topic": "obesity"}),
                       c.call("tie_report", {"expert_id": "A0"})])
    if key:
        assert key not in blob
    assert "OPENALEX_" not in blob.upper()


# ---------- 3. anonymity and name smuggling ----------

def names_for(ids):
    out = {}
    ids = sorted(set(ids))
    for i in range(0, len(ids), 1000):
        out.update({r[0]: r[1] for r in tr.q("MATCH (p:Person) WHERE p.oa_id IN $ids RETURN p.oa_id, p.name",
                                             {"ids": ids[i:i + 1000]})})
    return out


def assert_no_names(outs, extra_ids=(), strip=()):
    blob = json.dumps(outs, ensure_ascii=False)
    for s in strip:
        blob = blob.replace(s, "")
    shown = set(re.findall(r"A\d{6,}", blob)) | set(extra_ids)
    names = names_for(shown)
    assert len(names) >= 3, len(names)
    leaked = [(p, n) for p, n in names.items() if n and len(n) > 5 and n in blob]
    assert not leaked, f"{len(leaked)} names leaked, e.g. {leaked[:3]}"


def test_default_outputs_carry_no_names_only_expert_labels():
    ids = picked()
    c = client()
    outs = [c.call("find_experts", {"topic": "obesity", "n": 30}), c.call("check_case", {"expert_ids": ids})]
    for pid in ids:
        outs += [c.call("tie_report", {"expert_id": pid, "expert_number": 2}), c.call("check_no_tie", {"expert_id": pid}),
                 c.call("bridges", {"expert_id": pid, "depth": 3})]
    assert_no_names(outs, ids)
    for e in outs[0]["data"]["experts"]:
        assert re.fullmatch(r"Expert #\d+", e["label"]), e["label"]
    for r in outs[1]["data"]["results"]:
        assert re.fullmatch(r"Expert #\d+", r["label"]), r["label"]
    for o in outs[2:]:
        assert re.fullmatch(r"Expert #2|Expert #1", o["data"]["label"]), o["data"]["label"]


def test_name_smuggling_through_every_argument_is_inert():
    ids = picked()
    c = client()
    pid = ids[0]
    real = names_for([pid])[pid]
    evil = [("tie_report", {"expert_id": pid, "public_case": True, "label": real, "name": real, "newsroom": "beta"}),
            ("tie_report", {"expert_id": pid, "expert_number": 7, "topic": real, "names": [real]}),
            ("check_no_tie", {"expert_id": pid, "public_case": True, "label": real}),
            ("bridges", {"expert_id": ids[8], "public_case": True, "label": real, "depth": 3}),
            ("check_case", {"expert_ids": [pid], "label": real, "labels": [real], "name": real}),
            ("find_experts", {"topic": "obesity", "public_case": True, "label": real}),
            ("resolve_experts", {"names": [real], "topic": real, "public_case": True})]
    outs = []
    for name, args in evil:
        out = c.call(name, args)
        envelope_ok(out, name)
        outs.append(out)
    # the label never becomes the name
    assert outs[0]["data"]["label"] == "Expert #1" and outs[1]["data"]["label"] == "Expert #7"
    assert outs[2]["data"]["label"] == "Expert #1" and outs[3]["data"]["label"] == "Expert #1"
    assert outs[4]["data"]["results"][0]["label"] == "Expert #1"
    # a name passed as the expert_id or as expert_number is rejected or reported, never shown as a label
    r = c.call("tie_report", {"expert_id": real})
    assert r["ok"] is False and r["error"]["code"] == "NOT_FOUND"
    r = c.raw("tie_report", {"expert_id": pid, "expert_number": real})
    assert r.is_error
    # resolve_experts: the name the caller typed is echoed in name_in_input / warnings only; no database name leaks
    res = outs[6]
    strip = [real, real.lower(), real.upper()]
    blob = json.dumps(res, ensure_ascii=False)
    for s in strip:
        blob = blob.replace(s, "")
    db_names = names_for(re.findall(r"A\d{6,}", blob) + [pid])
    leaked = [n for n in db_names.values() if n and len(n) > 5 and n in blob]
    assert not leaked, leaked[:3]
    # no output reflects the injected text outside the documented echo fields
    for out in outs[:6]:
        assert real not in json.dumps(out, ensure_ascii=False), out["data"].get("label")


def test_resolve_experts_returns_the_spec_fields_and_no_names():
    c = client()
    out = c.call("resolve_experts", {"names": list(GEBN) + ["Maria Garcia", "Nobody Atall"], "topic": "obesity"})
    assert out["ok"]
    for m in out["data"]["matches"]:
        assert set(m) == {"name_in_input", "status", "expert_id", "candidates"}, sorted(m)
        assert m["status"] in {"MATCHED", "AMBIGUOUS", "NOT_FOUND"}
        for cand in m["candidates"]:
            assert set(cand) == {"expert_id", "works_on_topic", "orcid", "last_known_org"}, sorted(cand)
    blob = json.dumps(out, ensure_ascii=False)
    for s in GEBN:
        blob = blob.replace(s, "")
    shown = set(re.findall(r"A\d{6,}", blob))
    assert shown
    leaked = [n for n in names_for(shown).values() if n and len(n) > 5 and n in blob]
    assert not leaked, leaked


def test_public_case_true_puts_the_name_in_the_check_case_label_only():
    c = client()
    ids = list(GEBN.values())
    out = c.call("check_case", {"expert_ids": ids, "public_case": True})
    assert out["ok"]
    names = names_for(ids)
    by_id = {r["expert_id"]: r for r in out["data"]["results"]}
    for pid in ids:
        assert by_id[pid]["label"] == names[pid], (pid, by_id[pid]["label"])
    # but it does not leak co-author names (only listed experts are named)
    blob = json.dumps(out, ensure_ascii=False)
    for n in names.values():
        blob = blob.replace(n, "")
    others = names_for(re.findall(r"A\d{6,}", blob))
    assert not [n for p, n in others.items() if p not in ids and n and len(n) > 5 and n in blob]
    off = c.call("check_case", {"expert_ids": ids})
    assert all(re.fullmatch(r"Expert #\d+", r["label"]) for r in off["data"]["results"])


def test_public_case_is_only_honoured_for_the_documented_gebn_case():
    """CLAUDE.md: names only for the documented public case. A model-set flag must not unmask arbitrary experts."""
    c = client()
    ids = [p for p in picked() if p not in GEBN.values()][:3]
    out = c.call("check_case", {"expert_ids": ids, "public_case": True})
    names = names_for(ids)
    shown = [r["label"] for r in out["data"]["results"]]
    assert all(re.fullmatch(r"Expert #\d+", s) for s in shown), f"non-GEBN experts were named: {shown}"
    assert names


# ---------- 4. rules in CLAUDE.md, checked on the source ----------

def test_source_reads_only_with_ro_query_and_builds_no_cypher_from_input():
    for f in ("server.py", "research.py", "verifier.py"):
        src = (ROOT / f).read_text(encoding="utf-8")
        code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
        assert not re.search(r"\.query\(|GRAPH\.QUERY|execute_command|\.delete\(|\bMERGE\b|\bCREATE\b|\bSET\b ", code.replace("select_graph", "")), f
        if f == "research.py":
            assert code.count(".ro_query(") == 1 and "select_graph(graph).ro_query(query, params, timeout_ms)" in code
    srv = (ROOT / "server.py").read_text(encoding="utf-8")
    assert "falkordb" not in srv.lower().replace("falkordb_port", "").replace("falkordb port", "")  # no own DB access
    for bad in ("anthropic", "openai", "ANTHROPIC_API_KEY", "OPENALEX_API_KEY", "requests", "httpx", "subprocess", "claude -p"):
        assert bad not in srv, bad
    # no f-string / % / format building a query in server.py
    assert not re.search(r"f[\"'].*(MATCH|RETURN)", srv)


def test_newsroom_comes_from_env_only_and_is_validated_at_startup():
    base = {k: v for k, v in os.environ.items() if k != "TIETRACE_NEWSROOM"}
    for label, value in [("unset", None), ("empty", ""), ("Alpha!", "Alpha!"), ("../x", "../x"), ("space", "a b"),
                         ("newline", "alpha\n"), ("upper", "ALPHA")]:
        env = dict(base, FALKORDB_PORT=os.environ["FALKORDB_PORT"])
        if value is not None:
            env["TIETRACE_NEWSROOM"] = value
        t = time.time()
        p = subprocess.run(CMD, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
        assert p.returncode != 0, (label, "server started with a bad newsroom")
        assert "TIETRACE_NEWSROOM" in p.stderr, (label, p.stderr[-300:])
        assert p.stdout == "", (label, "stdout must stay empty", p.stdout[:100])
        assert "Traceback" not in p.stderr, label
        assert time.time() - t < 30
    # good values are accepted (checked by the raw-protocol test for the scratch newsroom)
    for good in ("zz-test-a_b-1",):
        p = subprocess.Popen(CMD, env=dict(base, TIETRACE_NEWSROOM=good, FALKORDB_PORT=os.environ["FALKORDB_PORT"]),
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        time.sleep(8)
        alive = p.poll() is None
        p.kill()
        p.wait()
        assert alive, good


# ---------- 5. protocol: stdout cleanliness, shutdown, no orphans ----------

def test_stdout_is_only_json_rpc_and_shutdown_is_clean_with_no_orphans():
    if os.name != "nt":  # server_processes() uses PowerShell/Win32_Process; runs in full on Windows
        print("      SKIP: needs Windows (PowerShell process listing)")
        return
    before = server_processes()
    p = subprocess.Popen(CMD, env=server_env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "find_experts", "arguments": {"topic": "obesity", "n": 3}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "tie_report", "arguments": {"expert_id": "A0"}}}]
    p.stdin.write(("\n".join(json.dumps(m) for m in msgs) + "\n").encode())
    p.stdin.flush()
    lines, deadline = [], time.time() + 60
    ids_seen = set()
    while time.time() < deadline and not {1, 2, 3, 4} <= ids_seen:
        line = p.stdout.readline()
        if not line:
            break
        lines.append(line)
        with contextlib.suppress(Exception):
            ids_seen.add(json.loads(line).get("id"))
    assert {1, 2, 3, 4} <= ids_seen, ids_seen
    during = server_processes() - before
    p.stdin.close()
    t = time.time()
    try:
        rc = p.wait(timeout=15)
    except subprocess.TimeoutExpired:
        p.kill()
        raise AssertionError("server did not exit after stdin closed")
    rest = p.stdout.read()
    err = p.stderr.read().decode(errors="replace")
    for line in lines + [l for l in rest.splitlines(True) if l.strip()]:
        msg = json.loads(line)  # every stdout line is JSON
        assert msg.get("jsonrpc") == "2.0", line[:100]
    assert "Traceback" not in err, err[-500:]
    assert f"newsroom={NEWSROOM}" in err
    assert time.time() - t < 15
    time.sleep(1.5)
    leftover = server_processes() - before
    assert not leftover, f"orphan server processes after exit: {sorted(leftover)} (were alive during: {sorted(during)}, rc={rc})"


# ---------- 6. check_case against the independent recomputation ----------

def test_check_case_tiers_scores_ranking_counts_and_labels():
    ids = picked()
    order = ids[:]
    import random
    random.Random(7).shuffle(order)  # input order must not matter for correctness; labels follow input order
    c = client()
    out = c.call("check_case", {"expert_ids": order})
    envelope_ok(out, "check_case")
    assert out["ok"]
    rows = out["data"]["results"]
    assert len(rows) == len(order) and out["data"]["not_checked"] == []
    assert {r["expert_id"] for r in rows} == set(order)
    label_of = {pid: f"Expert #{i}" for i, pid in enumerate(order, 1)}
    rank = {"A1": 0, "A2": 1, "B": 2, "C": 3, "NONE": 4}
    for r in rows:
        tier, score = tr.expected(r["expert_id"])
        assert r["label"] == label_of[r["expert_id"]], (r["expert_id"], r["label"])
        assert r["tier"] == tier, (r["expert_id"], r["tier"], tier)
        assert abs(r["score"] - score) < 1e-3, (r["expert_id"], r["score"], score)
        direct = research.tie_report(r["expert_id"], r["label"])["data"]
        assert (direct["tier"], direct["score"]) == (r["tier"], r["score"])
        if tier == "NONE":
            assert r["no_tie"] == "NO_TIE_FOUND", r
            assert research.check_no_tie(r["expert_id"])["data"]["result"] == r["no_tie"]
        else:
            assert r["no_tie"] is None
        assert r["check_id"] is None
    keys = [(rank[r["tier"]], -r["score"]) for r in rows]
    assert keys == sorted(keys), "rows are not ranked tier then score desc"
    exp_counts = {t: sum(tr.expected(p)[0] == t for p in order) for t in rank}
    assert out["data"]["counts"] == exp_counts and sum(exp_counts.values()) == len(order)
    assert all(exp_counts[t] > 0 for t in rank), exp_counts
    # the Cypher: every query once; scope merged; warnings unique
    keyset = [(x["query"], str(x["params"])) for x in out["cypher"]]
    assert len(keyset) == len(set(keyset))
    assert out["scope"].get("years") and out["scope"].get("load_scope")
    assert len(out["warnings"]) == len(set(out["warnings"]))


def test_check_case_unknown_id_duplicates_empty_and_over_limit():
    ids = picked()
    c = client()
    out = c.call("check_case", {"expert_ids": [ids[0], "A0", ids[5]]})
    assert out["ok"]
    rows = out["data"]["results"]
    bad = [r for r in rows if r["expert_id"] == "A0"][0]
    assert bad["tier"] is None and bad["score"] is None and bad["error"]["code"] == "NOT_FOUND" and bad["label"] == "Expert #2"
    assert rows[-1] is bad  # failed rows rank last
    assert sum(out["data"]["counts"].values()) == 2
    for args in ({"expert_ids": []}, {"expert_ids": [ids[0], ids[0]]}, {"expert_ids": [ids[0], ""]},
                 {"expert_ids": [f"A{i}" for i in range(26)]}):
        r = c.call("check_case", args)
        assert r["ok"] is False and r["error"]["code"] == "INVALID_ARG", (args, r)
    ok25 = c.call("check_case", {"expert_ids": [f"A{i}" for i in range(25)]})
    assert ok25["ok"] and len(ok25["data"]["results"]) == 25  # exactly 25 is allowed


def test_check_case_time_budget_stops_starting_new_experts():
    os.environ["TIETRACE_NEWSROOM"] = NEWSROOM
    import server  # imported, not run: we patch it in this process
    ids = picked()[:6]
    real = research.tie_report
    server.BATCH_BUDGET_S = 0.5

    def slow(*a, **k):
        time.sleep(0.35)
        return real(*a, **k)

    server.research.tie_report = slow
    try:
        out = server._check_case(ids, False)
    finally:
        server.research.tie_report = real
        server.BATCH_BUDGET_S = 90
    assert out["ok"]
    done = [r["expert_id"] for r in out["data"]["results"]]
    skipped = out["data"]["not_checked"]
    assert 1 <= len(done) < 6 and sorted(done + skipped) == sorted(ids), (done, skipped)
    assert skipped == [p for p in ids if p in skipped] and any("time budget" in w for w in out["warnings"])
    assert sum(out["data"]["counts"].values()) == len(done)


def test_check_case_25_size_and_time():
    ids = tr.sample()
    pick = [p for p in ids if tr.expected(p)[0] in ("NONE", "C", "B")][:12] + \
           [p for p in ids if tr.expected(p)[0] in ("A1", "A2")][:13]
    assert len(pick) == 25
    c = client()
    t = time.time()
    raw = c.raw("check_case", {"expert_ids": pick})
    dt = time.time() - t
    size = sum(len(x.text) for x in raw.content)  # what the model reads (pretty-printed JSON text)
    out = raw.structured_content
    assert out["ok"] and len(out["data"]["results"]) == 25
    print(f"      check_case(25): {dt:.1f}s, text ~{size / 1000:.0f} KB, "
          f"cypher entries {len(out['cypher'])}, text blocks {len(raw.content)}")
    assert dt < 30
    # Claude Code caps an MCP tool result at 25,000 tokens by default (about 3.5 characters per token of JSON):
    # keep one result well under that, ~50 KB
    assert size < 50_000, f"{size} bytes in one tool result (about {size // 3500}k tokens)"


def test_check_case_cypher_is_fully_recoverable_after_collapsing():
    ids = picked()[:12]
    out = client().call("check_case", {"expert_ids": ids})
    texts = [c["query"] for c in out["cypher"]]
    assert len(texts) == len(set(texts)), "one entry per query text"
    run_count = {}
    sets = {}
    for c in out["cypher"]:
        assert set(c) >= {"graph", "query", "params", "runs", "other_params"}, sorted(c)
        sets[c["query"]] = [json.dumps(c["params"], sort_keys=True)] + [json.dumps(p, sort_keys=True) for p in c["other_params"]]
        run_count[c["query"]] = c["runs"]
    expected_runs = {}
    for i, pid in enumerate(ids, 1):  # replay every call directly and compare what was executed
        envs = [research.tie_report(pid, f"Expert #{i}")]
        if envs[0]["data"]["tier"] == "NONE":
            envs.append(research.check_no_tie(pid, label=f"Expert #{i}"))
        for e in envs:
            for c in e["cypher"]:
                key = json.dumps(c["params"], sort_keys=True)
                assert c["query"] in sets, ("query missing from collapsed cypher", c["query"][:80])
                assert key in sets[c["query"]], ("param set dropped", c["query"][:80], key[:80])
                expected_runs[c["query"]] = expected_runs.get(c["query"], 0) + 1
    assert expected_runs == run_count, "run counts differ from the queries actually executed"
    # single-expert tools keep the plain format (no runs / other_params)
    one = client().call("tie_report", {"expert_id": ids[0]})
    assert all("runs" not in c for c in one["cypher"])


def test_public_case_mixed_subset_duplicate_and_odd_ids():
    c = client()
    g = list(GEBN.values())
    other = [p for p in picked() if p not in g]
    names = names_for(g + other[:2])

    def labels(ids, flag=True):
        out = c.call("check_case", {"expert_ids": ids, "public_case": flag})
        blob = json.dumps(out, ensure_ascii=False)
        return out, blob

    for ids, named in [(g[:3] + other[:1], False), (other[:3], False), (g[:2], True), (g, True), ([g[0]], True)]:
        out, blob = labels(ids)
        got = [r["label"] for r in out["data"]["results"]]
        if named:
            assert sorted(got) == sorted(names[i] for i in ids), got
            assert not any("public_case ignored" in w for w in out["warnings"])
        else:
            assert all(re.fullmatch(r"Expert #\d+", x) for x in got), got
            assert any("public_case ignored" in w for w in out["warnings"]), out["warnings"]
            assert not [n for i, n in names.items() if n in blob], "a name leaked after the flag was ignored"
    # duplicates are rejected, odd spellings are not GEBN ids (and find nothing), so no name appears
    for ids in ([g[0], g[0]], [g[0].lower()], [" " + g[0]], [g[0] + chr(10)], [g[0], g[1].lower()]):
        out, blob = labels(ids)
        if out["ok"]:
            assert not [n for n in names.values() if n in blob], ids
            assert all(re.fullmatch(r"Expert #\d+", r["label"]) for r in out["data"]["results"]), ids
    # no other tool can return a name
    for name, args in [("tie_report", {"expert_id": g[0], "public_case": True}), ("check_no_tie", {"expert_id": g[0], "public_case": True}),
                       ("bridges", {"expert_id": g[0], "public_case": True, "depth": 3})]:
        assert names[g[0]] not in json.dumps(c.call(name, args), ensure_ascii=False), name


def test_check_case_rows_always_have_an_error_key():
    out = client().call("check_case", {"expert_ids": [picked()[0], "A0"]})
    assert all("error" in r for r in out["data"]["results"])
    assert [r["error"] is None for r in out["data"]["results"]] == [True, False]
    desc = {t.name: t.description for t in client().tools()}["check_case"]
    assert "input order" in desc.lower() or "ties" in desc.lower(), desc


def test_db_down_gives_db_error_and_recovers():
    """Stops and restarts the falkordb container. Only runs with TIETRACE_TEST_DOCKER=1."""
    if os.environ.get("TIETRACE_TEST_DOCKER") != "1":
        print("      skipped (set TIETRACE_TEST_DOCKER=1 to stop and restart the falkordb container)")
        return
    c = Client()
    pid = picked()[0]
    try:
        assert c.call("tie_report", {"expert_id": pid})["ok"]
        subprocess.run(["docker", "stop", "falkordb"], capture_output=True, timeout=120)
        try:
            for name, args in [("tie_report", {"expert_id": pid}), ("find_experts", {"topic": "obesity"}),
                               ("check_no_tie", {"expert_id": pid}), ("bridges", {"expert_id": pid}),
                               ("resolve_experts", {"names": ["a b"], "topic": "obesity"}),
                               ("check_case", {"expert_ids": [pid, picked()[1]]})]:
                out = c.call(name, args)
                envelope_ok(out, name)
                assert out["ok"] is False and out["error"]["code"] == "DB_ERROR", (name, out.get("error"), out["ok"])
                check_text_clean(out, name)
            # partial failure: an unknown id is a row error, not a DB error, so the batch stays ok=true
        finally:
            subprocess.run(["docker", "start", "falkordb"], capture_output=True, timeout=120)
        ok = False
        for _ in range(10):
            time.sleep(4)
            ok = c.call("tie_report", {"expert_id": pid})["ok"]
            if ok:
                break
        assert ok, "no recovery after the database came back"
        assert c.call("find_experts", {"topic": "obesity", "n": 3})["data"]["experts"], "graph is empty after restart"
    finally:
        c.close()


# ---------- 7. timing gate and concurrency ----------

def test_each_tool_under_3_seconds_in_a_fresh_process_first_call_included():
    ids = picked()
    c = Client()  # a fresh process, nothing warmed by earlier tests except the server's own warm-up
    try:
        print(f"      spawn + initialize: {c.init_seconds:.1f}s")
        assert c.init_seconds < 20
        calls = [("tie_report", {"expert_id": ids[-1]}), ("find_experts", {"topic": "obesity", "n": 20}),
                 ("check_no_tie", {"expert_id": ids[-2]}), ("bridges", {"expert_id": ids[8], "depth": 3}),
                 ("resolve_experts", {"names": ["Steven N. Blair", "Maria Garcia"], "topic": "obesity"}),
                 ("check_case", {"expert_ids": ids[:5]})]
        for name, args in calls:
            t = time.time()
            out = c.call(name, args)
            dt = time.time() - t
            print(f"      {name}: {dt:.2f}s")
            assert out["ok"] and dt < 3, (name, dt)
    finally:
        c.close()


def test_parallel_calls_do_not_corrupt_or_deadlock_and_list_tools_stays_responsive():
    ids = picked()
    c = client()
    seq = {pid: c.call("tie_report", {"expert_id": pid, "expert_number": 3}) for pid in ids[:8]}
    big = c.start("check_case", {"expert_ids": ids})
    futs = [(pid, c.start("tie_report", {"expert_id": pid, "expert_number": 3})) for pid in ids[:8] for _ in range(2)]
    futs += [("fe", c.start("find_experts", {"topic": "obesity", "n": 10})) for _ in range(3)]
    t = time.time()
    tools = c.tools()  # must not wait for the batch
    assert len(tools) == len(TOOLS) and time.time() - t < 3, (len(tools), time.time() - t)
    for pid, f in futs:
        r = f.result(120)
        assert not r.is_error
        out = r.structured_content
        envelope_ok(out, pid)
        if pid != "fe":
            assert out["data"] == seq[pid]["data"], pid
    big_out = big.result(180).structured_content
    envelope_ok(big_out, "check_case")
    assert len(big_out["data"]["results"]) == len(ids)
    for r in big_out["data"]["results"]:
        assert (r["tier"], abs(r["score"] - tr.expected(r["expert_id"])[1]) < 1e-3) == (tr.expected(r["expert_id"])[0], True)


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
                msg = str(e) or type(e).__name__
                lines = [f.lineno for f in traceback.extract_tb(e.__traceback__) if f.filename == __file__]
                print(f"FAIL  {name}  (line {lines[-1] if lines else '?'}): {msg[:500]}")
    finally:
        for c in _C:
            c.close()
        for g in research._conn().list_graphs():  # the scratch newsrooms this suite created
            if g.startswith("newsroom:zz-test-"):
                research._conn().select_graph(g).delete()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
