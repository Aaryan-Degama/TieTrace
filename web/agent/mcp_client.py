"""The MCP client for the web agent: starts the unchanged server.py over stdio and calls its tools.
Same command and env as the Claude path (web.app.mcp_config(), imported, not copied). A2, Abdul's Builder, Oct 17."""
import asyncio
import json
import os
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from web.agent.providers import ToolSpec
from web.app import SERVER_NAME, mcp_config

CALL_TIMEOUT_S = 120


def _error(code, message):
    return {"ok": False, "error": {"code": code, "message": message}}


class McpClient:
    async def __aenter__(self):
        cfg = mcp_config()["mcpServers"][SERVER_NAME]
        params = StdioServerParameters(command=cfg["command"], args=cfg["args"], env={**os.environ, **cfg["env"]})
        self._stack = AsyncExitStack()
        try:
            r, w = await self._stack.enter_async_context(stdio_client(params))
            self._session = await self._stack.enter_async_context(ClientSession(r, w))
            await self._session.initialize()
        except BaseException:
            await self._stack.aclose()
            raise
        return self

    async def __aexit__(self, *exc):
        await self._stack.aclose()

    async def tools(self):
        return [ToolSpec(t.name, t.description or "", t.input_schema) for t in (await self._session.list_tools()).tools]

    async def call(self, name, args):
        """The parsed envelope. If the server answers with plain text (for example its own argument-validation
        error), that text comes back inside an error envelope, so the model can read it and fix the call."""
        res = await asyncio.wait_for(self._session.call_tool(name, args), CALL_TIMEOUT_S)
        text = "".join(getattr(b, "text", "") for b in res.content)
        try:
            env = json.loads(text)
        except ValueError:
            env = None
        if isinstance(env, dict) and "ok" in env:
            return env
        return _error("TOOL_ERROR" if res.is_error else "BAD_RESULT", text[:500])
