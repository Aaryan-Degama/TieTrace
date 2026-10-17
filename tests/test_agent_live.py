"""LIVE provider checks (checklist_agent.md A1). Written by Abdul's Builder, Oct 17 2026.

Run:  AGENT_LIVE=1 uv run python tests/test_agent_live.py
Gemini reads GEMINI_API_KEY from the git-ignored .env (in-process, never printed). Claude and OpenAI run only if
TEST_KEY_CLAUDE / TEST_KEY_OPENAI are set in the shell, else "not run: no key". A 429 stops the whole run (no retry).
Everything printed goes through say(), which fails the run if a key value would appear.
"""
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from web.agent.providers import ProviderError, ToolSpec, get_provider  # noqa: E402

SECRETS: list = []
OUT: list = []


def say(msg):
    for s in SECRETS:
        assert s not in str(msg), "a key value would have been printed"
    OUT.append(str(msg))
    print(msg)


def env_key(var):
    """Value from the shell, else from .env (parsed here, no dependency). Registered as a secret."""
    v = os.environ.get(var, "").strip()
    if not v and (ROOT / ".env").exists():
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            k, _, val = line.partition("=")
            if k.strip() == var:
                v = val.strip().strip("'\"")
    if v:
        SECRETS.append(v)
    return v


TOOL = ToolSpec("lookup", "Look up a short fact about a person by name.",
                {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]})
SYSTEM = "You are terse. Use the lookup tool when asked about a person."


async def two_turns(name, key):
    p = get_provider(name)
    say(f"[{name}] model id: {p.model}")
    msgs = [{"role": "user", "text": "Use the lookup tool for the name Ada. Do not answer before calling it."}]
    t1 = await p.turn(SYSTEM, msgs, [TOOL], key)
    say(f"[{name}] turn 1: stop={t1.stop} calls={[(c.name, c.args) for c in t1.calls]} tokens={t1.input_tokens}/{t1.output_tokens}")
    assert t1.calls and t1.calls[0].name == "lookup" and isinstance(t1.calls[0].args, dict) and t1.calls[0].id
    c = t1.calls[0]
    msgs += [{"role": "assistant", "text": t1.text, "calls": t1.calls, "raw": t1.raw},
             {"role": "tool", "call_id": c.id, "name": c.name, "result": '{"fact": "Ada wrote the first program."}'}]
    t2 = await p.turn(SYSTEM, msgs, [TOOL], key)
    say(f"[{name}] turn 2: stop={t2.stop} text={t2.text[:120]!r} tokens={t2.input_tokens}/{t2.output_tokens}")
    assert t2.text.strip() and not t2.calls
    # same history without raw (rebuild path): must still be accepted by the vendor
    msgs[1] = {"role": "assistant", "text": t1.text, "calls": t1.calls}
    try:
        t3 = await p.turn(SYSTEM, msgs, [TOOL], key)
        say(f"[{name}] turn 2 without raw: accepted, text={t3.text[:60]!r}")
    except ProviderError as e:
        say(f"[{name}] turn 2 without raw: REJECTED {e.code} {e}")


def run(name, var):
    key = env_key(var)
    if not key:
        say(f"SKIP  {name}: not run: no key")
        return
    try:
        asyncio.run(two_turns(name, key))
        say(f"PASS  {name}")
    except ProviderError as e:
        say(f"FAIL  {name}: {e.code} {e}")
        if e.code == "RATE_LIMIT":
            say("429 received: stopping, no retry")
            sys.exit(2)
        sys.exit(1)


def wrong_key():
    canary = "CANARY-KEY-123"
    SECRETS.append(canary)
    try:
        asyncio.run(get_provider("gemini").turn(SYSTEM, [{"role": "user", "text": "hi"}], [TOOL], canary))
    except ProviderError as e:
        say(f"[gemini] wrong key -> {e.code}: {e}")
        assert canary not in str(e)
        return
    raise AssertionError("a wrong key was accepted")


if __name__ == "__main__":
    if os.environ.get("AGENT_LIVE") != "1":
        print("not run: set AGENT_LIVE=1")
        sys.exit(0)
    run("gemini", "GEMINI_API_KEY")
    wrong_key()
    run("claude", "TEST_KEY_CLAUDE")
    run("openai", "TEST_KEY_OPENAI")
    say("done; no key value was printed")
