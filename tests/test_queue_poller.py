"""C11 tests — queue_poller (mocked GitHub + pipeline)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from monogram.queue_poller import (
    _extract_body,
    _list_queue_files,
    process_one,
)


def test_extract_body_strips_frontmatter():
    content = (
        "---\n"
        "captured_at: 2026-04-18T14:30:22+09:00\n"
        "source: obsidian-plugin\n"
        "version: 1\n"
        "---\n"
        "need wireless earbuds\n"
    )
    body = _extract_body(content)
    assert "need wireless earbuds" in body
    assert "captured_at" not in body


def test_extract_body_no_frontmatter_passes_through():
    body = _extract_body("just raw text")
    assert "just raw text" in body


@patch("monogram.queue_poller.github_store")
def test_list_queue_files_filters_by_pattern(mock_store):
    def _f(name, path, typ="file"):
        return SimpleNamespace(name=name, path=path, type=typ)
    fake_repo = MagicMock()
    fake_repo.get_contents.return_value = [
        _f("queue-1745054432-abc.md", "daily/2026-04-18/queue-1745054432-abc.md"),
        _f("drops.md", "daily/2026-04-18/drops.md"),           # not queue
        _f("queue-1745054999-xyz.md", "daily/2026-04-18/queue-1745054999-xyz.md"),
        _f("subdir", "daily/2026-04-18/subdir", typ="dir"),    # not a file
    ]
    mock_store._repo.return_value = fake_repo

    paths = _list_queue_files(date="2026-04-18")
    assert len(paths) == 2
    assert all("queue-" in p for p in paths)


@patch("monogram.queue_poller.handle_drop", new_callable=AsyncMock)
@patch("monogram.queue_poller.github_store")
@patch("monogram.queue_poller.safe_read")
def test_process_one_success_deletes_queue_file_and_sidecar(
    mock_safe_read, mock_store, mock_handle_drop,
):
    mock_safe_read.return_value = (
        "---\nsource: obsidian-plugin\n---\nmark paper-a phase 0 done\n"
    )
    mock_handle_drop.return_value = "`projects/paper-a.md` committed (high, 4 paths)"
    fake_entry = MagicMock(sha="abc123")
    fake_repo = MagicMock()
    fake_repo.get_contents.return_value = fake_entry
    mock_store._repo.return_value = fake_repo

    ok = asyncio.run(process_one("daily/2026-04-18/queue-x.md"))
    assert ok is True
    # v0.5.1: two deletes now — queue file + sidecar
    delete_paths = [c[0][0] for c in fake_repo.delete_file.call_args_list]
    assert "daily/2026-04-18/queue-x.md" in delete_paths
    assert "daily/2026-04-18/queue-x.md.processing" in delete_paths


@patch("monogram.queue_poller.handle_drop", new_callable=AsyncMock)
@patch("monogram.queue_poller.github_store")
@patch("monogram.queue_poller.safe_read")
def test_process_one_blocked_keeps_queue_releases_sidecar(
    mock_safe_read, mock_store, mock_handle_drop,
):
    """Blocked drops: leave the queue file for retry; release the sidecar."""
    mock_safe_read.return_value = "body text"
    mock_handle_drop.return_value = "blocked: verifier rejected"
    fake_repo = MagicMock()
    mock_store._repo.return_value = fake_repo

    ok = asyncio.run(process_one("daily/2026-04-18/queue-y.md"))
    assert ok is False
    # Queue file itself is NOT deleted — but sidecar IS released so retry works
    delete_paths = [c[0][0] for c in fake_repo.delete_file.call_args_list]
    assert "daily/2026-04-18/queue-y.md" not in delete_paths
    assert "daily/2026-04-18/queue-y.md.processing" in delete_paths


@patch("monogram.queue_poller.handle_drop", new_callable=AsyncMock)
@patch("monogram.queue_poller.github_store")
@patch("monogram.queue_poller.safe_read")
def test_process_one_empty_body_skipped(
    mock_safe_read, mock_store, mock_handle_drop,
):
    mock_safe_read.return_value = "---\nsource: obsidian-plugin\n---\n\n"
    # parse_metadata returns empty body → _extract_body returns "" → skip
    mock_store.parse_metadata.return_value = (
        {"source": "obsidian-plugin"}, "",
    )
    fake_repo = MagicMock()
    mock_store._repo.return_value = fake_repo

    ok = asyncio.run(process_one("daily/2026-04-18/queue-empty.md"))
    assert ok is False
    mock_handle_drop.assert_not_awaited()
    fake_repo.delete_file.assert_not_called()


@patch("monogram.queue_poller.handle_drop", new_callable=AsyncMock)
@patch("monogram.queue_poller.github_store")
@patch("monogram.queue_poller.safe_read")
def test_process_one_handles_pipeline_exception(
    mock_safe_read, mock_store, mock_handle_drop,
):
    """Pipeline raises: queue file retained, sidecar released for retry."""
    mock_safe_read.return_value = "body text"
    mock_handle_drop.side_effect = RuntimeError("pipeline boom")
    fake_repo = MagicMock()
    mock_store._repo.return_value = fake_repo

    ok = asyncio.run(process_one("daily/2026-04-18/queue-z.md"))
    assert ok is False
    delete_paths = [c[0][0] for c in fake_repo.delete_file.call_args_list]
    assert "daily/2026-04-18/queue-z.md" not in delete_paths
    # Sidecar is released on exception so next cycle can retry
    assert "daily/2026-04-18/queue-z.md.processing" in delete_paths
