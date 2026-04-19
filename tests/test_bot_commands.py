"""Bot /done /revive command tests — pure helper logic, no Telegram."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from monogram.bot import (
    _extract_slug,
    _flip_status_frontmatter,
    _move_project,
)


def test_extract_slug_parses_command():
    assert _extract_slug("/done paper-a") == "paper-a"
    # Whole argument is slugified; user sees "not found" if typo
    assert _extract_slug("/done project-b extra") == "project-b-extra"


def test_extract_slug_normalizes_input():
    assert _extract_slug("/done Paper A") == "paper-a"
    assert _extract_slug("/done foo_bar") == "foo-bar"


def test_extract_slug_none_when_missing_arg():
    assert _extract_slug("/done") is None
    assert _extract_slug("") is None
    assert _extract_slug(None) is None


def test_flip_status_rewrites_existing():
    content = (
        "---\nstatus: active\ngithub_repos: [me/x]\n---\n\n"
        "# paper-a\n\nbody"
    )
    out = _flip_status_frontmatter(content, "done")
    assert "status: done" in out
    assert "status: active" not in out
    assert "github_repos: [me/x]" in out
    assert "# paper-a" in out


def test_flip_status_adds_if_missing():
    content = "---\ngithub_repos: [me/x]\n---\n\nbody"
    out = _flip_status_frontmatter(content, "done")
    assert "status: done" in out


def test_flip_status_adds_frontmatter_to_plain_file():
    content = "# plain file\nno frontmatter"
    out = _flip_status_frontmatter(content, "done")
    assert out.startswith("---\nstatus: done\n---")


@patch("monogram.bot.github_store")
def test_move_project_to_archive(mock_store):
    mock_store.read.return_value = (
        "---\nstatus: active\n---\n\n# paper-a\n"
    )
    mock_store.write.return_value = True
    fake_repo = MagicMock()
    fake_repo.get_contents.return_value = MagicMock(sha="abc123")
    mock_store._repo.return_value = fake_repo

    reply = _move_project("paper-a", to_archive=True)

    assert "projects/archive/paper-a.md" in reply
    assert "done" in reply
    # Ensure write targeted archive path with status=done
    write_args = mock_store.write.call_args[0]
    assert write_args[0] == "projects/archive/paper-a.md"
    assert "status: done" in write_args[1]
    fake_repo.delete_file.assert_called_once()


@patch("monogram.bot.github_store")
def test_move_project_missing_returns_error(mock_store):
    mock_store.read.return_value = ""
    reply = _move_project("ghost-project", to_archive=True)
    assert "not found" in reply
    mock_store.write.assert_not_called()


@patch("monogram.bot.github_store")
def test_revive_flips_status_back_to_active(mock_store):
    mock_store.read.return_value = (
        "---\nstatus: done\n---\n\n# old-project\n"
    )
    mock_store.write.return_value = True
    fake_repo = MagicMock()
    fake_repo.get_contents.return_value = MagicMock(sha="zzz")
    mock_store._repo.return_value = fake_repo

    reply = _move_project("old-project", to_archive=False)

    assert "projects/old-project.md" in reply
    assert "active" in reply
    write_args = mock_store.write.call_args[0]
    assert write_args[0] == "projects/old-project.md"
    assert "status: active" in write_args[1]
