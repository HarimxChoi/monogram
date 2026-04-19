"""End-to-end MCP integration test — spawns `monogram mcp-serve` and talks to
it as a client over stdio. Mirrors scripts/verify_mcp_client.py but in
pytest form. Cross-platform; no Claude Desktop / Cursor required.
"""
from __future__ import annotations

import asyncio
import shutil

import pytest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXPECTED_TOOLS = [
    "read_project",
    "list_projects",
    "update_project",
    "today_brief",
    "recent_activity",
    "search_wiki",
    "query_life",
    "get_morning_brief",
    "current_project_state",
    "get_board",
    "add_wiki_entry",
    "get_llm_config",
    "set_llm_config",
]

monogram_bin = shutil.which("monogram")

pytestmark = pytest.mark.skipif(
    monogram_bin is None,
    reason="monogram binary not on PATH (skip MCP client integration)",
)


async def _connect_and_run():
    params = StdioServerParameters(command=monogram_bin, args=["mcp-serve"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            call = await session.call_tool(
                "recent_activity", arguments={"hours": 12}
            )
            return tools, call


def test_client_lists_core_tools_and_calls_one():
    tools, call = asyncio.run(_connect_and_run())

    names = [t.name for t in tools.tools]
    assert names == EXPECTED_TOOLS, names

    assert call.content, "no content returned from recent_activity"
    text = getattr(call.content[0], "text", "") or ""
    assert "12h" in text, text
