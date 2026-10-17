"""A0 check: the mcp CLIENT can start server.py over stdio (as web.app.mcp_config() does) and list its 13 tools.

Run:  FALKORDB_PORT=6500 uv run python tests/test_agent_a0.py
Scratch newsroom zz-test-agent-a0 only (never alpha); deleted at the end. No provider is called, no key is used.
"""
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("FALKORDB_PORT", "6500")

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

NEWSROOM = "zz-test-agent-a0"
EXPECTED = {"resolve_experts", "find_experts", "tie_report", "check_no_tie", "bridges", "check_case", "list_cases",
            "open_case", "recall", "recheck", "record_verdict", "draft_note", "approve_note"}
HUMAN_ONLY = {"record_verdict", "approve_note"}


async def list_tools():
    env = {**os.environ, "TIETRACE_NEWSROOM": NEWSROOM}
    params = StdioServerParameters(command="uv", args=["run", "--directory", str(ROOT), "python", "server.py"], env=env)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            return [t.name for t in (await s.list_tools()).tools]


def test_client_lists_13_tools():
    names = asyncio.run(asyncio.wait_for(list_tools(), 90))
    assert len(names) == 13 and set(names) == EXPECTED, sorted(names)
    assert HUMAN_ONLY <= set(names)  # present on the server; the agent loop (A2) must filter them out


if __name__ == "__main__":
    try:
        test_client_lists_13_tools()
        print("PASS  test_client_lists_13_tools")
    finally:
        from research import _conn
        for g in _conn().list_graphs():
            if g.startswith(f"newsroom:{NEWSROOM}"):
                _conn().select_graph(g).delete()
