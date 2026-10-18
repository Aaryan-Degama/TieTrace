"""Offline tests for web/agent/providers.py (checklist_agent.md A1, plan Task 1). Written by Abdul's Builder, Oct 17 2026.

Run:  uv run python tests/test_agent_providers.py [name-substring ...]
No network: each vendor's SDK client is replaced by a fake through the `client_factory` argument. The fake replies
have the field names the vendors' docs show (Gemini Interactions steps, OpenAI Responses output items, Anthropic
content blocks). Live checks are in tests/test_agent_live.py.
"""
import asyncio
import json
import sys
import time
import traceback
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from web.agent.providers import ProviderError, ToolCall, ToolSpec, get_provider  # noqa: E402

CANARY = "CANARY-KEY-123"
ARGS = {"names": ["A"], "topic": "t"}
TOOLS = [ToolSpec("resolve_experts", "find experts", {"type": "object", "properties": {"names": {"type": "array"}}})]
WANT = [ToolCall("call_1", "resolve_experts", ARGS)]


class Fake:
    """Stands in for the SDK client of any vendor: records kwargs, returns a canned reply or raises."""

    def __init__(self, reply=None, exc=None):
        self.reply, self.exc, self.sent = reply, exc, []
        call = self._create
        self.interactions = NS(create=call)  # gemini  (client.aio.interactions.create)
        self.responses = NS(create=call)     # openai  (client.responses.create)
        self.messages = NS(create=call)      # claude  (client.messages.create)
        self.chat = NS(completions=NS(create=call))  # groq (client.chat.completions.create)

    async def _create(self, **kw):
        self.sent.append(kw)
        if self.exc:
            raise self.exc
        return self.reply

    async def close(self):
        pass


class Dumpable(NS):
    def model_dump(self, **_):
        return {k: (v.model_dump() if isinstance(v, Dumpable) else v) for k, v in vars(self).items()}


def D(**kw):
    return Dumpable(**kw)


def gemini_reply(call=True, args=ARGS, text="hello"):
    steps = [D(type="thought", signature="sig")]
    steps.append(D(type="function_call", id="call_1", name="resolve_experts", arguments=args) if call
                 else D(type="model_output", content=[D(type="text", text=text)]))
    return D(steps=steps, usage=D(total_input_tokens=7, total_output_tokens=3))


def openai_reply(call=True, args=json.dumps(ARGS), text="hello"):
    out = [D(type="reasoning", id="rs_1")]
    out.append(D(type="function_call", call_id="call_1", name="resolve_experts", arguments=args) if call
               else D(type="message", content=[D(type="output_text", text=text)]))
    return D(output=out, status="completed", usage=D(input_tokens=7, output_tokens=3))


def claude_reply(call=True, args=ARGS, text="hello"):
    blocks = [D(type="tool_use", id="call_1", name="resolve_experts", input=args)] if call else [D(type="text", text=text)]
    return D(content=blocks, stop_reason="tool_use" if call else "end_turn", usage=D(input_tokens=7, output_tokens=3))


def groq_reply(call=True, args=json.dumps(ARGS), text="hello", finish=None):
    """SYNTHETIC Chat Completions reply, field names as on https://console.groq.com/docs/tool-use (Oct 18 2026)."""
    tcs = [D(id="call_1", type="function", function=D(name="resolve_experts", arguments=args))] if call else None
    msg = D(role="assistant", content=None if call else text, tool_calls=tcs)
    return D(choices=[D(index=0, message=msg, finish_reason=finish or ("tool_calls" if call else "stop"))],
             usage=D(prompt_tokens=7, completion_tokens=3, total_tokens=10))


REPLY = {"gemini": gemini_reply, "openai": openai_reply, "claude": claude_reply, "groq": groq_reply}


def run_turn(name, fake, messages=None, key=CANARY):
    p = get_provider(name, client_factory=lambda k: fake)
    return asyncio.run(p.turn("SYS", messages or [{"role": "user", "text": "hi"}], TOOLS, key))


def expect_error(name, fake):
    try:
        run_turn(name, fake)
    except ProviderError as e:
        return e
    raise AssertionError("no ProviderError raised")


class HttpError(Exception):
    """Duck-typed SDK status error: has status_code and a body that echoes the key (as a hostile provider could)."""

    def __init__(self, status):
        super().__init__(f"HTTP {status}: invalid key {CANARY}")
        self.status_code, self.body = status, {"error": f"bad key {CANARY}"}


def chain_text(e):
    out, seen = [], set()
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        out += [str(e), repr(e), repr(getattr(e, "args", ""))]
        e = e.__cause__ or e.__context__
    return " ".join(out)


# ---------- the six plan tests, for every provider ----------

def test_each_provider_turns_a_tool_call_reply_into_a_Turn():
    for name in REPLY:
        t = run_turn(name, Fake(REPLY[name](call=True)))
        assert t.calls == WANT, (name, t.calls)
        assert t.stop == "tool_use" and (t.input_tokens, t.output_tokens) == (7, 3), (name, t)
        assert isinstance(t.raw, list) and t.raw, name


def test_each_provider_turns_a_plain_text_reply_into_a_Turn():
    for name in REPLY:
        t = run_turn(name, Fake(REPLY[name](call=False, text="the answer")))
        assert t.calls == [] and t.text == "the answer" and t.stop == "end", (name, t)


def test_a_401_becomes_BAD_KEY_and_the_message_never_contains_the_key():
    for name in REPLY:
        for status in (401, 403):
            e = expect_error(name, Fake(exc=HttpError(status)))
            assert e.code == "BAD_KEY", (name, e.code)
            assert CANARY not in chain_text(e), name
            assert e.__cause__ is None and e.__suppress_context__ and e.__context__ is None, name


def test_rate_limit_and_provider_down_codes():
    for name in REPLY:
        assert expect_error(name, Fake(exc=HttpError(429))).code == "RATE_LIMIT"
        assert expect_error(name, Fake(exc=HttpError(503))).code == "PROVIDER_DOWN"
        assert expect_error(name, Fake(exc=type("ConnectError", (Exception,), {})(CANARY))).code == "PROVIDER_DOWN"
        assert expect_error(name, Fake(exc=type("APITimeoutError", (Exception,), {})(CANARY))).code == "PROVIDER_DOWN"
        assert CANARY not in chain_text(expect_error(name, Fake(exc=HttpError(429))))


def test_a_provider_that_never_answers_ends_with_PROVIDER_DOWN_within_the_timeout():
    import time

    class Hang(Fake):
        async def _create(self, **kw):
            await asyncio.sleep(3600)

    for name in ("gemini", "openai", "claude", "groq"):
        fake = Hang()
        fake.interactions = fake.responses = fake.messages = NS(create=fake._create)
        fake.chat = NS(completions=NS(create=fake._create))
        p = get_provider(name, client_factory=lambda k: fake)
        p.timeout_s = 0.3
        t0 = time.time()
        try:
            asyncio.run(p.turn("SYS", [{"role": "user", "text": "hi"}], TOOLS, CANARY))
            raise AssertionError("no error")
        except ProviderError as e:
            assert e.code == "PROVIDER_DOWN" and CANARY not in str(e), e
        assert time.time() - t0 < 5, name


def test_a_429_is_RATE_LIMIT_at_once_and_the_sdk_clients_are_built_with_few_retries():
    import time
    t0 = time.time()
    for name in ("gemini", "openai", "claude", "groq"):
        assert expect_error(name, Fake(exc=HttpError(429))).code == "RATE_LIMIT"
    assert time.time() - t0 < 2
    from web.agent.providers import GeminiProvider, OpenAIProvider, ClaudeProvider, GroqProvider
    g = GeminiProvider._default_factory("k")  # real SDK clients, built offline (no request is made)
    assert g.interactions.sdk_configuration.retry_config.max_retries <= 1
    assert OpenAIProvider._default_factory("k").max_retries == 0
    assert ClaudeProvider._default_factory("k").max_retries == 0
    gq = GroqProvider._default_factory("k")
    assert gq.max_retries == 0 and str(gq.base_url).rstrip("/") == "https://api.groq.com/openai/v1"


def test_google_400_with_API_KEY_INVALID_becomes_BAD_KEY():
    e = HttpError(400)  # shape seen live on Oct 17: body is a list of {"error": {"details": [{"reason": ...}]}}
    e.body = [{"error": {"message": f"bad {CANARY}", "details": [{"reason": "API_KEY_INVALID"}]}}]
    err = expect_error("gemini", Fake(exc=e))
    assert err.code == "BAD_KEY" and CANARY not in chain_text(err)
    assert expect_error("gemini", Fake(exc=HttpError(400))).code == "BAD_REPLY"  # other 400s are not a key problem


def test_malformed_tool_arguments_become_BAD_REPLY():
    bad = {"gemini": gemini_reply(args="not a dict"), "openai": openai_reply(args="{not json"),
           "claude": claude_reply(args="not a dict"), "groq": groq_reply(args="{not json")}
    for name, reply in bad.items():
        assert expect_error(name, Fake(reply)).code == "BAD_REPLY", name
    assert expect_error("openai", Fake(openai_reply(args="[1, 2]"))).code == "BAD_REPLY"  # JSON but not an object
    assert expect_error("groq", Fake(groq_reply(args="[1, 2]"))).code == "BAD_REPLY"
    assert expect_error("groq", Fake(D(choices=[], usage=None))).code == "BAD_REPLY"  # no choice at all
    t = run_turn("claude", Fake(D(content=[], stop_reason="end_turn", usage=None)))
    assert t.text == "" and t.calls == []  # an empty reply is a Turn; the loop (A3) turns it into a readable error


def test_the_key_goes_only_to_the_client_factory():
    for name in REPLY:
        fake, seen = Fake(REPLY[name]()), []
        p = get_provider(name, client_factory=lambda k: seen.append(k) or fake)
        msgs = [{"role": "user", "text": "hi"}, {"role": "assistant", "text": "", "calls": WANT},
                {"role": "tool", "call_id": "call_1", "name": "resolve_experts", "result": "{}"}]
        asyncio.run(p.turn("SYS", msgs, TOOLS, CANARY))
        assert seen == [CANARY]
        assert CANARY not in json.dumps(fake.sent, default=str), name
        assert CANARY not in json.dumps(p.__dict__, default=str) and CANARY not in repr(vars(type(p))), name


def test_unknown_provider_is_refused():
    try:
        get_provider("nope")
    except ValueError:
        return
    raise AssertionError("ValueError expected")


# ---------- multi-turn translation: the tool result is linked by the call id ----------

HISTORY = [{"role": "user", "text": "check A"},
           {"role": "assistant", "text": "", "calls": WANT},
           {"role": "tool", "call_id": "call_1", "name": "resolve_experts", "result": "RESULT-TEXT"}]


def test_gemini_multi_turn_translation():
    f = Fake(gemini_reply(call=False))
    run_turn("gemini", f, HISTORY)
    k = f.sent[0]
    assert k["store"] is False and k["system_instruction"] == "SYS"
    steps = k["input"]
    assert [s["type"] for s in steps] == ["user_input", "function_call", "function_result"], steps
    assert steps[1]["id"] == "call_1" and steps[1]["arguments"] == ARGS
    assert steps[2]["call_id"] == "call_1" and steps[2]["result"] == [{"type": "text", "text": "RESULT-TEXT"}]
    assert k["tools"][0]["type"] == "function" and k["tools"][0]["parameters"] == TOOLS[0].schema


def test_openai_multi_turn_translation():
    f = Fake(openai_reply(call=False))
    run_turn("openai", f, HISTORY)
    k = f.sent[0]
    assert k["instructions"] == "SYS"
    items = k["input"]
    assert items[0] == {"role": "user", "content": "check A"}
    assert items[1] == {"type": "function_call", "call_id": "call_1", "name": "resolve_experts", "arguments": json.dumps(ARGS)}
    assert items[2] == {"type": "function_call_output", "call_id": "call_1", "output": "RESULT-TEXT"}
    assert k["tools"][0]["type"] == "function" and k["tools"][0]["parameters"] == TOOLS[0].schema


def test_claude_multi_turn_translation():
    f = Fake(claude_reply(call=False))
    two = HISTORY + [{"role": "tool", "call_id": "call_2", "name": "x", "result": "R2"}]
    run_turn("claude", f, two)
    k = f.sent[0]
    assert k["system"] == "SYS" and k["max_tokens"] > 0
    m = k["messages"]
    assert [x["role"] for x in m] == ["user", "assistant", "user"], m  # both results in ONE user message
    assert m[1]["content"] == [{"type": "tool_use", "id": "call_1", "name": "resolve_experts", "input": ARGS}]
    assert m[2]["content"] == [{"type": "tool_result", "tool_use_id": "call_1", "content": "RESULT-TEXT"},
                               {"type": "tool_result", "tool_use_id": "call_2", "content": "R2"}]
    assert k["tools"][0]["input_schema"] == TOOLS[0].schema


def test_raw_of_an_earlier_turn_is_sent_back_unchanged():
    for name in REPLY:  # gemini thought steps / openai reasoning items must round-trip
        first = run_turn(name, Fake(REPLY[name]()))
        hist = [{"role": "user", "text": "q"}, {"role": "assistant", "text": first.text, "calls": first.calls, "raw": first.raw},
                {"role": "tool", "call_id": "call_1", "name": "resolve_experts", "result": "R"}]
        f = Fake(REPLY[name](call=False))
        run_turn(name, f, hist)
        sent = f.sent[0]["messages" if name in ("claude", "groq") else "input"]
        flat = json.dumps(sent, default=str)
        assert ("thought" in flat) if name == "gemini" else True
        assert ("reasoning" in flat) if name == "openai" else True


# ---------- Reviewer additions (Oct 17): REAL SDK classes over a mock HTTP transport, log capture, client close ----------

def _real_factory(name, status, body):
    """The real vendor SDK client, its HTTP layer replaced by a mock transport. Returns (factory, requests-seen)."""
    seen = []

    def make(mod, tr_cls, resp_cls):
        def handler(req):
            seen.append((str(req.url), dict(req.headers), req.content.decode()))
            return resp_cls(status, json=body, headers={"content-type": "application/json"})
        return tr_cls(handler)

    def factory(key):
        if name == "claude":
            import anthropic
            import httpx2
            hc = anthropic.DefaultAsyncHttpxClient(transport=make(None, httpx2.MockTransport, httpx2.Response))
            return anthropic.AsyncAnthropic(api_key=key, http_client=hc, max_retries=0)
        import httpx
        hc = httpx.AsyncClient(transport=make(None, httpx.MockTransport, httpx.Response))
        if name == "openai":
            from openai import AsyncOpenAI
            return AsyncOpenAI(api_key=key, http_client=hc, max_retries=0)
        if name == "groq":
            from openai import AsyncOpenAI
            from web.agent.providers import GroqProvider
            return AsyncOpenAI(api_key=key, base_url=GroqProvider.BASE_URL, http_client=hc, max_retries=0)
        from google import genai
        return genai.Client(api_key=key, http_options={"httpx_async_client": hc}).aio
    return factory, seen


def test_real_sdk_error_classes_map_and_never_leak_the_key_even_with_debug_logging():
    import io
    import logging
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    root = logging.getLogger()
    old = (root.level, list(root.handlers))
    root.setLevel(logging.DEBUG)
    root.addHandler(h)
    try:
        for name in REPLY:
            for status, code in ((401, "BAD_KEY"), (403, "BAD_KEY"), (429, "RATE_LIMIT"), (500, "PROVIDER_DOWN"),
                                 (529, "PROVIDER_DOWN"), (400, "BAD_REPLY")):
                fac, seen = _real_factory(name, status, {"error": {"message": f"echo {CANARY}"}})
                p = get_provider(name, client_factory=fac)
                try:
                    asyncio.run(p.turn("S", [{"role": "user", "text": "hi"}], TOOLS, CANARY))
                except ProviderError as e:
                    assert e.code == code, (name, status, e.code)
                    assert CANARY not in chain_text(e) and e.__cause__ is None and e.__context__ is None
                else:
                    raise AssertionError((name, status, "no error"))
                url, headers, body = seen[-1]
                assert CANARY not in url and CANARY not in body, name  # the key travels in a header only
                assert [k for k, v in headers.items() if CANARY in v], name
    finally:
        root.removeHandler(h)
        root.setLevel(old[0])
    assert CANARY not in buf.getvalue(), "key appeared in DEBUG logs"


def test_real_sdk_timeout_and_connection_errors_are_PROVIDER_DOWN():
    import anthropic
    import httpx
    import openai
    req = httpx.Request("POST", "https://x")
    for exc in (openai.APITimeoutError(request=req), openai.APIConnectionError(request=req),
                anthropic.APITimeoutError(request=req), anthropic.APIConnectionError(request=req)):
        for name in ("openai", "claude"):
            assert expect_error(name, Fake(exc=exc)).code == "PROVIDER_DOWN", (name, type(exc))


def test_real_sdk_replies_parse_with_parallel_calls_and_round_trip_raw():
    oa = {"id": "r", "object": "response", "created_at": 1, "status": "completed", "model": "m", "parallel_tool_calls": True,
          "tool_choice": "auto", "tools": [], "output": [
              {"type": "reasoning", "id": "rs_1", "summary": []},
              {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "lookup", "arguments": "{\"a\":1}", "status": "completed"},
              {"type": "function_call", "id": "fc_2", "call_id": "call_2", "name": "lookup", "arguments": "{}", "status": "completed"}],
          "usage": {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10, "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0}}}
    cl = {"id": "m", "type": "message", "role": "assistant", "model": "m", "stop_reason": "tool_use", "stop_sequence": None,
          "usage": {"input_tokens": 7, "output_tokens": 3}, "content": [
              {"type": "tool_use", "id": "toolu_1", "name": "lookup", "input": {"a": 1}},
              {"type": "tool_use", "id": "toolu_2", "name": "lookup", "input": {}}]}
    gq = {"id": "chatcmpl-1", "object": "chat.completion", "created": 1, "model": "m", "choices": [  # SYNTHETIC
              {"index": 0, "finish_reason": "tool_calls", "message": {"role": "assistant", "content": None, "tool_calls": [
                  {"id": "call_1", "type": "function", "function": {"name": "lookup", "arguments": "{\"a\":1}"}},
                  {"id": "call_2", "type": "function", "function": {"name": "lookup", "arguments": "{}"}}]}}],
          "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}}
    for name, body in (("openai", oa), ("claude", cl), ("groq", gq)):
        fac, seen = _real_factory(name, 200, body)
        p = get_provider(name, client_factory=fac)
        t = asyncio.run(p.turn("S", [{"role": "user", "text": "hi"}], TOOLS, CANARY))
        assert [c.id for c in t.calls] == (["toolu_1", "toolu_2"] if name == "claude" else ["call_1", "call_2"]) and t.stop == "tool_use"
        assert (t.input_tokens, t.output_tokens) == (7, 3)
        hist = [{"role": "user", "text": "hi"}, {"role": "assistant", "text": t.text, "calls": t.calls, "raw": t.raw}]
        hist += [{"role": "tool", "call_id": c.id, "name": c.name, "result": "r"} for c in t.calls]
        asyncio.run(p.turn("S", hist, TOOLS, CANARY))
        sent = json.loads(seen[-1][2])
        flat = json.dumps(sent)
        assert all(c.id in flat for c in t.calls) and CANARY not in flat
        if name == "openai":
            assert '"reasoning"' in flat and flat.count("function_call_output") == 2
        if name == "groq":
            assert seen[-1][0] == "https://api.groq.com/openai/v1/chat/completions", seen[-1][0]
            assert [m["role"] for m in sent["messages"]] == ["system", "user", "assistant", "tool", "tool"]
            assert [m["tool_call_id"] for m in sent["messages"][3:]] == ["call_1", "call_2"]


def test_the_sdk_client_is_closed_after_every_call_with_whatever_close_method_it_has():
    # a per-call client that is never closed leaks sockets on a long-running server; google-genai's aio client has aclose(), not close()
    for name in REPLY:
        for reply, exc in ((REPLY[name](), None), (None, HttpError(401))):
            closed = []

            class Aclose(Fake):
                close = None  # hide close(): like google.genai's AsyncClient

                async def aclose(self):
                    closed.append(1)
            f = Aclose(reply, exc)
            if name != "gemini":
                class Close(Fake):
                    async def close(self):
                        closed.append(1)
                f = Close(reply, exc)
            try:
                run_turn(name, f)
            except ProviderError:
                pass
            assert closed, f"{name}: client not closed ({'error' if exc else 'ok'} path)"


def test_a_client_factory_error_becomes_a_ProviderError_without_the_key():
    def boom(k):
        raise RuntimeError(f"cannot build client with {k}")
    for name in REPLY:
        try:
            asyncio.run(get_provider(name, client_factory=boom).turn("S", [{"role": "user", "text": "x"}], TOOLS, CANARY))
        except ProviderError as e:
            assert CANARY not in chain_text(e), name
        else:
            raise AssertionError("ProviderError expected")


def test_gemini_function_call_with_no_arguments_is_an_empty_dict():
    t = run_turn("gemini", Fake(gemini_reply(args=None)))
    assert t.calls == [ToolCall("call_1", "resolve_experts", {})]


def test_openai_length_stop_with_empty_text_still_returns_a_Turn():
    r = D(output=[D(type="reasoning", id="rs")], status="incomplete", usage=D(input_tokens=5, output_tokens=2048))
    t = run_turn("openai", Fake(r))
    assert t.text == "" and t.calls == [] and t.stop == "length"


# ---------- Groq (Chat Completions), added by Aaryan's Builder, Oct 18 2026; all replies SYNTHETIC ----------

def test_groq_multi_turn_translation():
    f = Fake(groq_reply(call=False))
    run_turn("groq", f, HISTORY)
    k = f.sent[0]
    assert k["max_completion_tokens"] > 0 and k["model"] == "qwen/qwen3.8-27b" and k["extra_body"] == {"reasoning_format": "hidden"}
    m = k["messages"]
    assert m[0] == {"role": "system", "content": "SYS"} and m[1] == {"role": "user", "content": "check A"}
    assert m[2] == {"role": "assistant", "tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": "resolve_experts", "arguments": json.dumps(ARGS)}}]}
    assert m[3] == {"role": "tool", "tool_call_id": "call_1", "content": "RESULT-TEXT"}
    assert "name" not in m[3]  # the OpenAI-compat page says messages[].name returns a 400
    f2 = Fake(groq_reply(call=False))  # a text-plus-calls turn keeps its text
    run_turn("groq", f2, [HISTORY[0], {"role": "assistant", "text": "checking", "calls": WANT}, HISTORY[2]])
    a = f2.sent[0]["messages"][2]
    assert a["content"] == "checking" and a["tool_calls"][0]["id"] == "call_1"
    f3 = Fake(groq_reply(call=False))  # a text-only assistant turn keeps content too
    run_turn("groq", f3, [HISTORY[0], {"role": "assistant", "text": "", "calls": []}, {"role": "user", "text": "more"}])
    assert f3.sent[0]["messages"][2] == {"role": "assistant", "content": ""}
    assert k["tools"] == [{"type": "function", "function": {"name": "resolve_experts", "description": "find experts",
                                                            "parameters": TOOLS[0].schema}}]


def test_groq_round_trip_of_its_own_turn_is_the_same_as_a_rebuilt_one():
    first = run_turn("groq", Fake(groq_reply()))
    tail = [{"role": "tool", "call_id": "call_1", "name": "resolve_experts", "result": "R"}]
    with_raw, without = Fake(groq_reply(call=False)), Fake(groq_reply(call=False))
    run_turn("groq", with_raw, [{"role": "user", "text": "q"},
                                {"role": "assistant", "text": first.text, "calls": first.calls, "raw": first.raw}] + tail)
    run_turn("groq", without, [{"role": "user", "text": "q"},
                               {"role": "assistant", "text": first.text, "calls": first.calls}] + tail)
    assert with_raw.sent[0]["messages"] == without.sent[0]["messages"]


def test_groq_stop_and_usage_mapping():
    for finish, want in (("stop", "end"), ("length", "length"), ("content_filter", "other")):
        t = run_turn("groq", Fake(groq_reply(call=False, finish=finish)))
        assert t.stop == want and (t.input_tokens, t.output_tokens) == (7, 3), (finish, t)
    t = run_turn("groq", Fake(groq_reply(call=True, finish="stop")))  # calls win over the finish_reason
    assert t.stop == "tool_use" and t.calls == WANT
    t = run_turn("groq", Fake(D(choices=[D(message=D(content="", tool_calls=None), finish_reason="length")], usage=None)))
    assert t.text == "" and t.calls == [] and t.stop == "length" and t.input_tokens == 0


def test_groq_498_flex_capacity_is_RATE_LIMIT():
    for name in REPLY:  # 498 is not a standard HTTP status; no code in this repo uses it for anything else
        e = expect_error(name, Fake(exc=HttpError(498)))
        assert e.code == "RATE_LIMIT" and CANARY not in chain_text(e), (name, e.code)


def test_groq_key_never_reaches_a_Turn():
    t = run_turn("groq", Fake(groq_reply()))
    assert CANARY not in json.dumps([t.text, t.raw, [vars(c) for c in t.calls]], default=str)


if __name__ == "__main__":
    only = sys.argv[1:]
    tests = [(n, f) for n, f in list(globals().items()) if n.startswith("test_") and callable(f)
             and (not only or any(o in n for o in only))]
    failed = 0
    for name, fn in tests:
        t0 = time.time()
        try:
            fn()
            print(f"PASS  {name}  ({time.time() - t0:.1f}s)")
        except Exception as e:
            failed += 1
            lines = [f.lineno for f in traceback.extract_tb(e.__traceback__) if f.filename == __file__]
            print(f"FAIL  {name}  (line {lines[-1] if lines else '?'}): {(str(e) or type(e).__name__)[:500]}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed, {failed} failed")
    sys.exit(1 if failed else 0)
