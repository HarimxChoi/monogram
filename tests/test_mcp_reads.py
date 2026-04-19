"""B1-B2 tests — MCP read tools."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest

from monogram.mcp_reads import (
    current_project_state,
    get_board,
    get_morning_brief,
    query_life,
    search_wiki,
)
from monogram.vault_config import load_vault_config


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr("monogram.vault_config.github_store.read", lambda p: "")
    load_vault_config.cache_clear()
    yield
    load_vault_config.cache_clear()


@patch("monogram.mcp_reads.safe_read")
def test_search_wiki_substring_match(mock_read):
    mock_read.return_value = (
        "# Wiki Index\n\n"
        "- [[rtmpose]] — real-time pose estimation [#pose #inference] (2026-04-17)\n"
        "- [[conformal]] — conformal prediction [#calibration] (2026-04-16)\n"
        "- [[sleep]] — sleep consistency [#health] (2026-04-15)\n"
    )
    result = json.loads(asyncio.run(search_wiki("pose")))
    slugs = [m["slug"] for m in result["matches"]]
    assert "rtmpose" in slugs
    assert "conformal" not in slugs


@patch("monogram.mcp_reads.safe_read")
def test_search_wiki_matches_tags(mock_read):
    mock_read.return_value = (
        "# Wiki Index\n\n"
        "- [[rtmpose]] — x [#pose-estimation] (2026-04-17)\n"
        "- [[other]] — y [#math] (2026-04-17)\n"
    )
    result = json.loads(asyncio.run(search_wiki("pose")))
    slugs = [m["slug"] for m in result["matches"]]
    assert "rtmpose" in slugs


def test_search_wiki_empty_query():
    result = json.loads(asyncio.run(search_wiki("")))
    assert result["matches"] == []
    assert "error" in result


@patch("monogram.mcp_reads.safe_read")
def test_query_life_returns_recent(mock_read):
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    mock_read.return_value = (
        f"# life/shopping\n\n"
        f"## 2020-01-01 09:00 — old\n\n"
        f"## {today} 10:00 — earbuds\n\n"
    )
    result = json.loads(asyncio.run(query_life("shopping", days=7)))
    titles = [e["title"] for e in result["entries"]]
    assert "earbuds" in titles
    assert "old" not in titles


def test_query_life_credentials_always_blocked():
    result = json.loads(asyncio.run(query_life("credentials")))
    assert result["entries"] == []
    assert "blocked" in result["error"]


@patch("monogram.mcp_reads.safe_read")
def test_get_morning_brief_default_yesterday(mock_read):
    mock_read.return_value = "# Morning brief — 2026-04-17\n\nbody"
    out = asyncio.run(get_morning_brief())
    assert "Morning brief" in out


@patch("monogram.mcp_reads.safe_read")
def test_get_morning_brief_missing(mock_read):
    mock_read.return_value = ""
    out = asyncio.run(get_morning_brief("2026-01-01"))
    assert "No morning brief" in out


@patch("monogram.mcp_reads.safe_read")
def test_current_project_state_fallback_to_archive(mock_read):
    def fake_read(path):
        if path == "projects/ghost.md":
            return ""
        if path == "projects/archive/ghost.md":
            return "---\nstatus: done\n---\n\n# ghost"
        return ""
    mock_read.side_effect = fake_read
    out = asyncio.run(current_project_state("ghost"))
    assert "archived" in out
    assert "# ghost" in out


@patch("monogram.mcp_reads.safe_read")
def test_get_board(mock_read):
    mock_read.return_value = "# Board\n\n## Active\n- [paper-a]\n"
    out = asyncio.run(get_board())
    assert "# Board" in out
