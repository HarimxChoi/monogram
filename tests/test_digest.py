"""Digest tests — mocked PyGithub, no real GitHub."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from monogram.digest import _format_commits_block, _watch_repos, run_digest


def _fake_commit(sha, message, author="alice", when=None):
    when = when or datetime(2026, 4, 18, 10, 0, tzinfo=timezone.utc)
    return SimpleNamespace(
        sha=sha,
        commit=SimpleNamespace(
            message=message,
            author=SimpleNamespace(name=author, date=when),
        ),
    )


def test_format_commits_block_groups_by_repo():
    commits = [
        {"sha": "abc1234", "time": "2026-04-18 10:00", "author": "alice",
         "message": "fix bug", "repo": "me/a"},
        {"sha": "def5678", "time": "2026-04-18 11:00", "author": "bob",
         "message": "new feature", "repo": "me/b"},
    ]
    out = _format_commits_block(commits)
    assert "### me/a" in out
    assert "### me/b" in out
    assert "fix bug" in out
    assert "new feature" in out


def test_format_commits_block_empty_is_empty_string():
    assert _format_commits_block([]) == ""


@patch("monogram.digest._watch_repos", return_value=[])
@patch("monogram.digest.github_store")
def test_digest_no_watch_repos_is_noop_with_error(mock_store, mock_repos):
    mock_store.read.return_value = ""
    mock_store.write.return_value = True
    result = asyncio.run(run_digest())
    assert result["commits"] == 0
    assert result["repos_fetched"] == 0
    assert result["errors"]


@patch("monogram.digest._watch_repos", return_value=["me/a"])
@patch("monogram.digest.Github")
@patch("monogram.digest.github_store")
def test_digest_writes_daily_commits_md(mock_store, mock_github, mock_repos):
    mock_store.read.return_value = ""
    mock_store.write.return_value = True
    fake_repo = SimpleNamespace(
        get_commits=lambda since: [
            _fake_commit("abc1234", "phase 0 done"),
            _fake_commit("def5678", "baseline experiment"),
        ]
    )
    mock_github.return_value.get_repo.return_value = fake_repo

    result = asyncio.run(run_digest(since_hours=24))

    assert result["commits"] == 2
    assert result["repos_fetched"] == 1
    assert result["errors"] == []
    # commits.md was written
    write_calls = mock_store.write.call_args_list
    paths_written = [c[0][0] for c in write_calls]
    assert any(p.endswith("/commits.md") for p in paths_written)
    content = [c[0][1] for c in write_calls if c[0][0].endswith("/commits.md")][0]
    assert "phase 0 done" in content
    assert "baseline experiment" in content
