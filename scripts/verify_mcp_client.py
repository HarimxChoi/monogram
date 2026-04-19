"""B3 verification script — connects to `monogram mcp-serve` as a real MCP client.

Simulates what Claude Desktop / Cursor / OpenClaw do at startup: spawn the
server as a subprocess, speak the MCP protocol over stdio, list tools, call
one. Cross-platform (Windows / macOS / Linux) — no GUI client required.

Run:
    python scripts/verify_mcp_client.py

Exits 0 on full pass, 1 on any failure. Prints a one-line per check.
"""
from __future__ import annotations

import asyncio
import shutil
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXPECTED_TOOLS = [
    "read_project",
    "list_projects",
    "update_project",
    "today_brief",
    "recent_activity",
]


async def verify() -> int:
    monogram_bin = shutil.which("monogram")
    if not monogram_bin:
        print("FAIL  monogram binary not on PATH (run `pip install -e .` or `pipx install mono-gram`)")
        return 1
    print(f"OK    monogram binary: {monogram_bin}")

    params = StdioServerParameters(command=monogram_bin, args=["mcp-serve"])

    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                init_result = await session.initialize()
                server_name = getattr(init_result.serverInfo, "name", "?")
                print(f"OK    initialize  server={server_name!r}")

                tool_result = await session.list_tools()
                names = [t.name for t in tool_result.tools]
                if names != EXPECTED_TOOLS:
                    print(f"FAIL  list_tools mismatch:\n      expected: {EXPECTED_TOOLS}\n      got:      {names}")
                    return 1
                print(f"OK    list_tools  count={len(names)} names={names}")

                call_result = await session.call_tool(
                    "recent_activity", arguments={"hours": 12}
                )
                if not call_result.content:
                    print("FAIL  call_tool('recent_activity') returned no content")
                    return 1
                first = call_result.content[0]
                text = getattr(first, "text", "") or ""
                if not text or "12h" not in text:
                    print(f"FAIL  call_tool('recent_activity') text unexpected: {text!r}")
                    return 1
                print(f"OK    call_tool   recent_activity → {text!r}")
    except Exception as exc:
        print(f"FAIL  client session error: {type(exc).__name__}: {exc}")
        return 1

    print("PASS  all 3 client-side MCP checks green")
    return 0


def main() -> int:
    return asyncio.run(verify())


if __name__ == "__main__":
    sys.exit(main())
