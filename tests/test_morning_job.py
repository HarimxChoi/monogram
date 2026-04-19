"""Morning job tests — mocked github_store + safe_read, no real GitHub."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from monogram.morning_job import (
    _infer_status,
    update_board,
    update_project_from_commits,
)
from monogram.vault_config import load_vault_config


@pytest.fixture(autouse=True)
def _clear_vault_cache(monkeypatch):
    # Force VaultConfig defaults; avoid real repo reads from safe_read chain
    monkeypatch.setattr("monogram.vault_config.github_store.read", lambda p: "")
    load_vault_config.cache_clear()
    yield
    load_vault_config.cache_clear()


def test_infer_status_active_with_commits():
    assert _infer_status("", "- commit abc") == "active"


def test_infer_status_inactive_without_commits():
    content = "## Status\nactive — last updated 2026-03-01 08:00\n"
    assert _infer_status(content, "") == "inactive"


def test_infer_status_done_stays_done():
    content = "---\nstatus: done\n---\n"
    assert _infer_status(content, "- commit abc") == "done"


@patch("monogram.safe_read.github_store")
@patch("monogram.morning_job.github_store")
def test_update_project_rewrites_auto_sections(mock_store, mock_safe_store):
    existing = (
        "---\nstatus: active\n---\n\n"
        "## Current focus\nuser-written text\n\n"
        "## Status\nactive — last updated 2026-04-16 08:00\n\n"
        "## Recent activity\n- old commit\n\n"
        "## Milestones\n- [ ] phase 0\n"
    )
    # morning_job reads via safe_read, which reads via monogram.safe_read.github_store
    mock_safe_store.read.return_value = existing
    mock_store.parse_metadata.return_value = ({"status": "active"}, existing)
    mock_store.write.return_value = True

    result = asyncio.run(
        update_project_from_commits(
            "projects/paper-a.md",
            "- 2026-04-17  commit abc  'phase 0 done'",
        )
    )
    assert result is True
    written = mock_store.write.call_args[0][1]
    assert "user-written text" in written
    assert "phase 0 done" in written
    assert "- [ ] phase 0" in written


@patch("monogram.safe_read.github_store")
@patch("monogram.morning_job.github_store")
def test_update_board_update_not_regenerate(mock_store, mock_safe_store):
    existing = (
        "# Board — 2026-04-16\n\n"
        "## Active\n"
        "- [paper-a](projects/paper-a.md) — old summary\n\n"
        "## Inactive\n"
        "- [project-b](projects/project-b.md) — 7 days\n\n"
    )
    mock_safe_store.read.return_value = existing
    mock_store.write.return_value = True

    projects = [
        {"name": "paper-a", "path": "projects/paper-a.md",
         "status": "active", "summary": "phase 1 started"},
        {"name": "project-b", "path": "projects/project-b.md",
         "status": "inactive", "summary": "14 days no commits"},
    ]
    result = asyncio.run(update_board(projects))
    assert result is True
    content = mock_store.write.call_args[0][1]
    assert "phase 1 started" in content
    assert "14 days" in content
    assert "old summary" not in content
