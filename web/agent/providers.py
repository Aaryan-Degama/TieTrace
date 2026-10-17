"""One neutral turn() for three vendors, each through its OFFICIAL SDK (docs: docs/test-keys.md).

The key is an argument of turn(): the SDK client is built per call and never stored. Errors are rebuilt from the
status KIND only, so no response body (which may echo the key) can reach a message, a log or the page.
Neutral messages: {"role":"user","text"} | {"role":"assistant","text","calls":[ToolCall],"raw"?} |
{"role":"tool","call_id","name","result":str}.  "raw" is the opaque Turn.raw of that assistant turn: Gemini thought
steps and OpenAI reasoning items must be sent back as they came, so the loop should copy Turn.raw into the message.
"""
import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MODELS = json.loads((Path(__file__).with_name("models.json")).read_text(encoding="utf-8"))


@dataclass
class ToolSpec:
    name: str
    description: str
    schema: dict


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class Turn:
    text: str
    calls: list
    stop: str  # "tool_use" | "end" | "length" | "other"
    input_tokens: int = 0
    output_tokens: int = 0
    raw: Any = field(default=None, repr=False)


class ProviderError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


_KIND = {"BAD_KEY": "key rejected", "RATE_LIMIT": "rate limit or quota reached",
         "PROVIDER_DOWN": "provider unreachable or failing", "BAD_REPLY": "unusable reply"}


def _err(provider: str, code: str, status=None) -> ProviderError:
    return ProviderError(code, f"{provider}: {_KIND[code]}" + (f" (HTTP {status})" if status else ""))


def _bad_key_reason(body) -> bool:
    """Google answers a wrong key with HTTP 400 + ErrorInfo.reason. Only that fixed enum is compared; nothing else is read."""
    if isinstance(body, dict):
        return body.get("reason") in ("API_KEY_INVALID", "API_KEY_EXPIRED") or any(_bad_key_reason(v) for v in body.values())
    if isinstance(body, (list, tuple)):
        return any(_bad_key_reason(v) for v in body)
    return False


def _classify(provider: str, e: Exception) -> ProviderError:
    """Status code and exception class name only; str(e) and e.body are never read."""
    status = getattr(e, "status_code", None)
    if not isinstance(status, int):
        status = None
    name = type(e).__name__
    if status in (401, 403) or (status == 400 and _bad_key_reason(getattr(e, "body", None))):
        code = "BAD_KEY"
    elif status == 429:
        code = "RATE_LIMIT"
    elif status is not None:
        code = "PROVIDER_DOWN" if status >= 500 else "BAD_REPLY"
    else:
        code = "PROVIDER_DOWN" if any(w in name for w in ("Connect", "Timeout", "Network", "Protocol")) else "BAD_REPLY"
    return _err(provider, code, status)


def _dump(x):
    return x.model_dump(mode="json", exclude_none=True) if hasattr(x, "model_dump") else x


def _args(provider: str, a) -> dict:
    if isinstance(a, str):
        try:
            a = json.loads(a)
        except ValueError:
            raise _err(provider, "BAD_REPLY") from None
    if not isinstance(a, dict):
        raise _err(provider, "BAD_REPLY")
    return a


async def _close(client):
    """google-genai's aio client has aclose(); openai/anthropic have close(). Try both, await if needed."""
    for attr in ("aclose", "close"):
        fn = getattr(client, attr, None)
        if callable(fn):
            try:
                r = fn()
                if hasattr(r, "__await__"):
                    await r
            except Exception:  # closing must never hide the real result or error
                pass
            return


class Provider:
    name = ""

    def __init__(self, client_factory=None):
        self._factory = client_factory or self._default_factory
        self.model = MODELS[self.name]["model"]
        self.max_tokens = MODELS[self.name]["turn_max_tokens"]
        self.timeout_s = MODELS[self.name].get("turn_timeout_s", 90)  # overall cap for one turn, retries included

    async def turn(self, system: str, messages: list, tools: list, key: str) -> Turn:
        client = None
        try:
            client = self._factory(key)  # the only place the key is used
            return await asyncio.wait_for(self._call(client, system, messages, tools), self.timeout_s)
        except ProviderError as e:
            err = e
        except asyncio.TimeoutError:  # a hang (SDK retry sleeps, slow network) must end as a readable error
            err = ProviderError("PROVIDER_DOWN", f"{self.name}: no answer within {self.timeout_s:g} s")
        except Exception as e:
            err = _classify(self.name, e)
        finally:
            if client is not None:
                await _close(client)
        raise err from None  # outside the except block: no __context__ keeps the SDK exception

    async def _call(self, client, system, messages, tools) -> Turn:
        raise NotImplementedError

    @staticmethod
    def _default_factory(key):
        raise NotImplementedError


class GeminiProvider(Provider):
    """Interactions API (the only one the function-calling page shows), stateless: store=False plus full history."""
    name = "gemini"

    @staticmethod
    def _default_factory(key):
        from google import genai
        from google.genai import types
        # attempts=1: the SDK still makes one retry (its minimum for the Interactions API); the turn timeout bounds it
        return genai.Client(api_key=key, http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(attempts=1))).aio

    def _input(self, messages):
        steps = []
        for m in messages:
            if m["role"] == "user":
                steps.append({"type": "user_input", "content": [{"type": "text", "text": m["text"]}]})
            elif m["role"] == "assistant":
                if isinstance(m.get("raw"), list):
                    steps += m["raw"]
                    continue
                if m.get("text"):
                    steps.append({"type": "model_output", "content": [{"type": "text", "text": m["text"]}]})
                steps += [{"type": "function_call", "id": c.id, "name": c.name, "arguments": c.args} for c in m.get("calls", [])]
            else:
                steps.append({"type": "function_result", "name": m["name"], "call_id": m["call_id"],
                              "result": [{"type": "text", "text": m["result"]}]})
        return steps

    async def _call(self, client, system, messages, tools):
        r = await client.interactions.create(
            model=self.model, input=self._input(messages), system_instruction=system, store=False, timeout=60,
            tools=[{"type": "function", "name": t.name, "description": t.description, "parameters": t.schema} for t in tools])
        text, calls = [], []
        for s in r.steps or []:
            if s.type == "function_call":
                calls.append(ToolCall(s.id, s.name, _args(self.name, {} if s.arguments is None else s.arguments)))
            elif s.type == "model_output":
                text += [c.text for c in (s.content or []) if getattr(c, "type", "") == "text"]
        u = r.usage
        return Turn("".join(text), calls, "tool_use" if calls else "end",
                    getattr(u, "total_input_tokens", 0) or 0, getattr(u, "total_output_tokens", 0) or 0,
                    [_dump(s) for s in r.steps or []])


class OpenAIProvider(Provider):
    """Responses API: items in response.output are sent back as input, plus function_call_output items."""
    name = "openai"

    @staticmethod
    def _default_factory(key):
        from openai import AsyncOpenAI
        return AsyncOpenAI(api_key=key, max_retries=0)

    def _input(self, messages):
        items = []
        for m in messages:
            if m["role"] == "user":
                items.append({"role": "user", "content": m["text"]})
            elif m["role"] == "assistant":
                if isinstance(m.get("raw"), list):
                    items += m["raw"]
                    continue
                if m.get("text"):
                    items.append({"role": "assistant", "content": m["text"]})
                items += [{"type": "function_call", "call_id": c.id, "name": c.name, "arguments": json.dumps(c.args)}
                          for c in m.get("calls", [])]
            else:
                items.append({"type": "function_call_output", "call_id": m["call_id"], "output": m["result"]})
        return items

    async def _call(self, client, system, messages, tools):
        r = await client.responses.create(
            model=self.model, instructions=system, input=self._input(messages), timeout=60,
            max_output_tokens=self.max_tokens,
            tools=[{"type": "function", "name": t.name, "description": t.description, "parameters": t.schema} for t in tools])
        text, calls = [], []
        for it in r.output or []:
            if it.type == "function_call":
                calls.append(ToolCall(it.call_id, it.name, _args(self.name, it.arguments)))
            elif it.type == "message":
                text += [getattr(c, "text", None) or getattr(c, "refusal", "") for c in it.content or []]
        u = r.usage
        stop = "tool_use" if calls else ("length" if getattr(r, "status", "") == "incomplete" else "end")
        return Turn("".join(text), calls, stop, getattr(u, "input_tokens", 0) or 0, getattr(u, "output_tokens", 0) or 0,
                    [_dump(i) for i in r.output or []])


class ClaudeProvider(Provider):
    """Messages API tool use: tool_use blocks out, tool_result blocks (in one user message) back."""
    name = "claude"

    @staticmethod
    def _default_factory(key):
        from anthropic import AsyncAnthropic
        return AsyncAnthropic(api_key=key, max_retries=0)

    def _messages(self, messages):
        out = []
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["text"]})
            elif m["role"] == "assistant":
                if isinstance(m.get("raw"), list):
                    out.append({"role": "assistant", "content": m["raw"]})
                    continue
                blocks = ([{"type": "text", "text": m["text"]}] if m.get("text") else [])
                blocks += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.args} for c in m.get("calls", [])]
                out.append({"role": "assistant", "content": blocks})
            else:
                block = {"type": "tool_result", "tool_use_id": m["call_id"], "content": m["result"]}
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                    out[-1]["content"].append(block)  # parallel results must share one user message
                else:
                    out.append({"role": "user", "content": [block]})
        return out

    async def _call(self, client, system, messages, tools):
        r = await client.messages.create(
            model=self.model, max_tokens=self.max_tokens, system=system, messages=self._messages(messages), timeout=60,
            tools=[{"name": t.name, "description": t.description, "input_schema": t.schema} for t in tools])
        text, calls = [], []
        for b in r.content or []:
            if b.type == "tool_use":
                calls.append(ToolCall(b.id, b.name, _args(self.name, b.input)))
            elif b.type == "text":
                text.append(b.text)
        stop = {"tool_use": "tool_use", "end_turn": "end", "max_tokens": "length"}.get(r.stop_reason, "other")
        u = r.usage
        return Turn("".join(text), calls, stop, getattr(u, "input_tokens", 0) or 0, getattr(u, "output_tokens", 0) or 0,
                    [_dump(b) for b in r.content or []])


_PROVIDERS = {"gemini": GeminiProvider, "openai": OpenAIProvider, "claude": ClaudeProvider}


def get_provider(name: str, client_factory=None) -> Provider:
    if name not in _PROVIDERS:
        raise ValueError(f"unknown provider: {name!r}")
    return _PROVIDERS[name](client_factory)
