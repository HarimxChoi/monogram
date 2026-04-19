"""MCP server tests.

Tool listing + handler dispatch are pure-ish (no network when handler is the
placeholder). GitHub-touching and LLM-touching handlers are gated.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from monogram.mcp_server import (
    TOOLS,
    _list_projects,
    _read_project,
    _recent_activity,
    _today_brief,
    _update_project,
    call_tool,
)


# ── Static surface ───────────────────────────────────────────────────────


def test_tools_list_includes_core_and_v04_tools():
    """v0.4a + v0.4b: legacy 5 + 5 reads + 1 write + 2 LLM-config = 13 total."""
    names = [t.name for t in TOOLS]
    for expected in (
        # legacy
        "read_project", "list_projects", "update_project",
        "today_brief", "recent_activity",
        # v0.4b reads
        "search_wiki", "query_life", "get_morning_brief",
        "current_project_state", "get_board",
        # v0.4b write
        "add_wiki_entry",
        # v0.4a LLM config
        "get_llm_config", "set_llm_config",
    ):
        assert expected in names, f"missing tool: {expected}"
    assert len(names) == 13


def test_every_tool_has_description_and_schema():
    for tool in TOOLS:
        assert tool.description, f"{tool.name} missing description"
        assert tool.inputSchema.get("type") == "object", f"{tool.name} bad schema"


def test_recent_activity_handler_no_network():
    """Placeholder handler — must not require network."""
    msg = asyncio.run(_recent_activity(12))
    assert "12h" in msg
    assert "v0.2" in msg


def test_call_tool_unknown_returns_message():
    out = asyncio.run(call_tool("nope_not_a_tool", {}))
    assert len(out) == 1
    assert out[0].type == "text"
    assert "Unknown tool" in out[0].text


def test_call_tool_dispatches_recent_activity():
    out = asyncio.run(call_tool("recent_activity", {"hours": 6}))
    assert "6h" in out[0].text


# ── Network-gated ────────────────────────────────────────────────────────


def _has_real_pat() -> bool:
    try:
        from monogram.config import load_config

        pat = load_config().github_pat
    except Exception:
        return False
    return bool(pat) and pat.lower().startswith(("ghp_", "github_pat_"))


def _has_real_gemini() -> bool:
    try:
        from monogram.config import load_config

        key = load_config().gemini_api_key
    except Exception:
        return False
    return bool(key) and not key.lower().startswith(("test", "dummy", "fake"))


network = pytest.mark.skipif(
    not _has_real_pat(), reason="real GITHUB_PAT not set"
)
network_and_llm = pytest.mark.skipif(
    not (_has_real_pat() and _has_real_gemini()),
    reason="GITHUB_PAT + GEMINI_API_KEY both required",
)


@network
def test_list_projects_returns_text_via_call_tool():
    out = asyncio.run(call_tool("list_projects", {}))
    assert len(out) == 1
    assert out[0].type == "text"
    assert len(out[0].text) > 0


@network
def test_read_project_missing_returns_helpful_text():
    out = asyncio.run(call_tool("read_project", {"project": "definitely-not-a-project"}))
    assert "not found" in out[0].text


@network_and_llm
@pytest.mark.live_llm
def test_today_brief_returns_substantial_text():
    out = asyncio.run(call_tool("today_brief", {}))
    assert len(out) == 1
    assert len(out[0].text) > 20
