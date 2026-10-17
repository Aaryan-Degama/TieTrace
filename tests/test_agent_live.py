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


def live_agent():
    """A2: run_agent with real Gemini + the real McpClient + the real graph (FALKORDB_PORT=6500, newsroom
    zz-test-agent-a2, deleted after). Events are already redacted by web.app.Parser; say() checks for the key."""
    import json
    key = env_key("GEMINI_API_KEY")
    if not key:
        say("SKIP  agent live: not run: no key")
        return
    os.environ["TIETRACE_NEWSROOM"] = "zz-test-agent-a2"
    os.environ["FALKORDB_PORT"] = "6500"
    from web.agent.loop import run_agent
    from web.agent.mcp_client import McpClient
    BLAIR, HILL = "A5105003630", "A5031318756"
    story = ("Coca-Cola and obesity. The article quotes Steven Blair and James O. Hill. "
             "Check how connected each is to Coca-Cola.")

    async def go():
        evs = []
        session = {}
        async with McpClient() as c:
            async for ev in run_agent(get_provider("gemini"), key, story, max_turns=8, client=c, session=session):
                evs.append(ev)
            # "Steven Blair" has a split OpenAlex profile (golden test), so the model must stop at the picker.
            # Do what the page does when the reporter clicks "Use this author": same session, same sentence.
            pick = [d for e, d in evs if e == "ambiguous" and BLAIR in [x["expert_id"] for x in d["candidates"]]]
            if pick:
                say("  (reporter picks Blair's candidate in the picker, same session)")
                follow = f"For {pick[0]['name_ref']}, use OpenAlex author {BLAIR}. Continue the check."
                async for ev in run_agent(get_provider("gemini"), key, follow, max_turns=8, client=c, session=session):
                    evs.append(ev)
        return evs
    try:
        evs = asyncio.run(asyncio.wait_for(go(), 300))
    except ProviderError as e:
        say(f"FAIL  agent live: {e.code} {e}")
        sys.exit(2 if e.code == "RATE_LIMIT" else 1)
    for e, d in evs:
        if e == "step":
            say(f"  step {d.get('tool')} {'running' if d.get('status') else 'ok=' + str(d.get('ok'))}")
        elif e == "expert":
            say(f"  expert {d.get('label')} id={d.get('expert_id')} tier={d.get('tier')} dois={[x.get('doi') for x in d.get('evidence') or []][:3]}")
        else:
            say(f"  {e}: {json.dumps(d, ensure_ascii=False)[:300]}")
    errs = [d for e, d in evs if e == "error"]
    if errs and errs[0].get("code") == "RATE_LIMIT":
        say("429 received: stopping, no retry")
        sys.exit(2)
    assert not errs, errs
    used = {d.get("tool") for e, d in evs if e == "step"}
    assert "resolve_experts" in used and used & {"check_case", "tie_report"}, used
    assert not used & {"record_verdict", "approve_note"}
    tier = {d["expert_id"]: d for e, d in evs if e == "expert" and d.get("tier")}
    assert tier[BLAIR]["tier"] == "A1" and tier[HILL]["tier"] == "A2", {k: v.get("tier") for k, v in tier.items()}
    for x in (BLAIR, HILL):
        assert tier[x]["label"].startswith("Expert #")
    # check_case rows carry no evidence, so a DOI shows only if the model also called tie_report for Blair
    assert any(e == "expert" and d.get("expert_id") == BLAIR and any(x.get("doi") for x in d.get("evidence") or [])
               for e, d in evs), "no tie_report card with a DOI for Blair"
    dumped = json.dumps(evs, ensure_ascii=False)
    for secret in ("Steven Blair", "Blair", "Hill", "James O."):
        assert secret not in dumped, secret
    assert "orcid.org" not in dumped and evs[-1][0] == "done"
    say("PASS  agent live (Blair A1, Hill A2, labels Expert #n, no names, no key)")


if __name__ == "__main__":
    if os.environ.get("AGENT_LIVE") != "1":
        print("not run: set AGENT_LIVE=1")
        sys.exit(0)
    run("gemini", "GEMINI_API_KEY")
    wrong_key()
    live_agent()
    run("claude", "TEST_KEY_CLAUDE")
    run("openai", "TEST_KEY_OPENAI")
    say("done; no key value was printed")
