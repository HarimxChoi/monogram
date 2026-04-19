"""B3-B5 + v0.5.1 tests — add_wiki_entry MCP write tool (GitHub-backed queue)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monogram.mcp_pending import pop_pending
from monogram.mcp_writes import add_wiki_entry_pending, commit_wiki_entry


class _FakeRepo:
    def __init__(self):
        self.files: dict[str, str] = {}

    def get_contents(self, path):
        if path not in self.files:
            from github import UnknownObjectException
            raise UnknownObjectException(404, {"message": "Not Found"}, None)
        e = MagicMock()
        e.decoded_content = self.files[path].encode()
        e.sha = "sha_" + path
        e.path = path
        return e

    def create_file(self, path, msg, content): self.files[path] = content
    def update_file(self, path, msg, content, sha): self.files[path] = content
    def delete_file(self, path, msg, sha): self.files.pop(path, None)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    repo = _FakeRepo()

    def _read(p):
        return repo.files.get(p, "")

    def _write(p, c, m):
        repo.files[p] = c
        return True

    monkeypatch.setattr("monogram.github_store.read", _read)
    monkeypatch.setattr("monogram.github_store.write", _write)
    monkeypatch.setattr("monogram.github_store._repo", lambda: repo)


@patch("monogram.bot_notify.push_to_telegram", new_callable=AsyncMock)
def test_add_wiki_entry_enqueues_pending(mock_push):
    mock_push.return_value = True
    result = asyncio.run(add_wiki_entry_pending(
        slug="new-entry", title="New", body="some content", tags=["t1", "t2"],
    ))
    assert "Token:" in result
    token = result.rsplit(":", 1)[1].strip()
    entry = pop_pending(token)
    assert entry is not None
    assert entry.kind == "add_wiki_entry"
    assert entry.payload["slug"] == "new-entry"
    assert entry.payload["tags"] == ["t1", "t2"]
    mock_push.assert_awaited_once()


@patch("monogram.bot_notify.push_to_telegram", new_callable=AsyncMock)
def test_add_wiki_entry_rejects_bad_slug(mock_push):
    result = asyncio.run(add_wiki_entry_pending(
        slug="Bad Slug!", title="x", body="y",
    ))
    assert "Error" in result
    mock_push.assert_not_awaited()


@patch("monogram.bot_notify.push_to_telegram", new_callable=AsyncMock)
def test_add_wiki_entry_rejects_empty_body(mock_push):
    result = asyncio.run(add_wiki_entry_pending(
        slug="ok-slug", title="x", body="",
    ))
    assert "body is required" in result
    mock_push.assert_not_awaited()


@patch("monogram.mcp_writes.github_store")
@patch("monogram.mcp_writes.safe_read")
def test_commit_wiki_entry_writes_file_and_index(mock_safe_read, mock_store):
    mock_safe_read.side_effect = lambda p: "" if p != "wiki/index.md" else "# Wiki Index\n\n"
    mock_store.serialize_with_metadata.side_effect = (
        lambda m, b: f"---\n(meta)\n---\n\n{b}"
    )
    mock_store.write.return_value = True

    ok, summary = asyncio.run(commit_wiki_entry({
        "slug": "new-entry",
        "title": "New Entry",
        "body": "body content",
        "tags": ["pose"],
    }))
    assert ok is True
    # Two writes: the wiki file + index update
    assert mock_store.write.call_count == 2
    paths = [c[0][0] for c in mock_store.write.call_args_list]
    assert "wiki/new-entry.md" in paths
    assert "wiki/index.md" in paths


@patch("monogram.mcp_writes.github_store")
@patch("monogram.mcp_writes.safe_read")
def test_commit_wiki_entry_refuses_to_overwrite(mock_safe_read, mock_store):
    mock_safe_read.return_value = "---\nexisting\n---\n\n# old"
    ok, summary = asyncio.run(commit_wiki_entry({
        "slug": "existing", "title": "x", "body": "y", "tags": [],
    }))
    assert ok is False
    assert "already exists" in summary
    mock_store.write.assert_not_called()
