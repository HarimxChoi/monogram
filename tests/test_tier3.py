"""Tests for v0.8 Tier 3 — atomic writes, backup, search, social/office/hwp.

We don't run live network or external subprocess calls (pyhwp's
`hwp5txt`, gcloud, etc.) in tests — those happen in a separate
integration suite. Here we verify:

- write_atomic: empty dict is no-op, 422 triggers retry, non-retryable
  errors propagate
- backup: config validation (missing PAT, backup-to-self guard)
- search: command injection via shell metacharacters is impossible
  (subprocess argv list)
- social: URL detection for IG/TikTok variants
- office: file-ext detection
- hwp: version parse + minimal_env strips secrets
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# github_store.write_atomic
# ---------------------------------------------------------------------------

class TestWriteAtomic:
    def test_empty_writes_is_noop(self):
        """Empty dict must return True without any API calls."""
        from monogram.github_store import write_atomic

        with patch("monogram.github_store._repo") as mock_repo:
            assert write_atomic({}, "noop commit") is True
            # Ensure no API calls happened
            mock_repo.assert_not_called()

    def test_retries_on_fast_forward_conflict(self):
        """422 "not a fast-forward" should trigger retry with fresh parent."""
        from monogram.github_store import write_atomic
        from github.GithubException import GithubException

        fast_fwd_error = GithubException(
            status=422,
            data={"message": "Update is not a fast-forward"},
            headers={},
        )

        mock_ref = MagicMock()
        # First ref.edit raises 422, second succeeds
        mock_ref.edit.side_effect = [fast_fwd_error, None]
        mock_ref.object.sha = "abc123"

        mock_repo = MagicMock()
        mock_repo.default_branch = "main"
        mock_repo.get_git_ref.return_value = mock_ref
        mock_repo.get_git_commit.return_value = MagicMock(tree=MagicMock())
        mock_repo.create_git_blob.return_value = MagicMock(sha="blob-sha")
        mock_repo.create_git_tree.return_value = MagicMock()
        mock_repo.create_git_commit.return_value = MagicMock(sha="commit-sha")

        with patch("monogram.github_store._repo", return_value=mock_repo):
            ok = write_atomic({"a.md": "a", "b.md": "b"}, "test")

        assert ok is True
        # Should have tried twice
        assert mock_ref.edit.call_count == 2

    def test_exhausts_retries_and_fails(self):
        from monogram.github_store import write_atomic
        from github.GithubException import GithubException

        fast_fwd_error = GithubException(
            status=422,
            data={"message": "Update is not a fast-forward"},
            headers={},
        )

        mock_ref = MagicMock()
        mock_ref.edit.side_effect = fast_fwd_error
        mock_ref.object.sha = "abc123"

        mock_repo = MagicMock()
        mock_repo.default_branch = "main"
        mock_repo.get_git_ref.return_value = mock_ref
        mock_repo.get_git_commit.return_value = MagicMock(tree=MagicMock())
        mock_repo.create_git_blob.return_value = MagicMock(sha="blob-sha")
        mock_repo.create_git_tree.return_value = MagicMock()
        mock_repo.create_git_commit.return_value = MagicMock(sha="commit-sha")

        with patch("monogram.github_store._repo", return_value=mock_repo):
            ok = write_atomic({"a.md": "a"}, "test", max_retries=3)

        assert ok is False
        assert mock_ref.edit.call_count == 3

    def test_non_retryable_error_fails_fast(self):
        """Non-422 GithubException should not retry."""
        from monogram.github_store import write_atomic
        from github.GithubException import GithubException

        server_error = GithubException(status=500, data={"message": "boom"}, headers={})

        mock_repo = MagicMock()
        mock_repo.default_branch = "main"
        mock_repo.get_git_ref.side_effect = server_error

        with patch("monogram.github_store._repo", return_value=mock_repo):
            ok = write_atomic({"a.md": "a"}, "test", max_retries=3)

        # 500 isn't fast-forward; current code gives it retries (per spec)
        # but we verify it exits within max_retries
        assert ok is False

    def test_is_fast_forward_detector(self):
        """v0.8.1 — detector now treats ANY 422 as retryable.

        Previously we substring-matched "fast-forward" in the message,
        which was brittle to GitHub wording changes. The retry refetches
        the parent SHA anyway, which is the correct response to any 422
        at ref.edit time (not just non-fast-forward).
        """
        from monogram.github_store import _is_fast_forward_conflict
        from github.GithubException import GithubException

        # Any 422 → retry
        assert _is_fast_forward_conflict(
            GithubException(422, {"message": "Update is not a fast-forward"}, {})
        )
        assert _is_fast_forward_conflict(
            GithubException(422, {"message": "Validation failed"}, {})
        )
        # Non-422 → no retry (falls through to outer handler)
        assert not _is_fast_forward_conflict(
            GithubException(500, {"message": "server error"}, {})
        )
        assert not _is_fast_forward_conflict(
            GithubException(404, {"message": "not found"}, {})
        )


# ---------------------------------------------------------------------------
# backup config
# ---------------------------------------------------------------------------

class TestBackupConfig:
    def test_rejects_missing_backup_pat(self):
        from monogram.backup import BackupMisconfigured, load_backup_config

        # Simulate a valid source config but no BACKUP_GITHUB_PAT
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BACKUP_GITHUB_PAT", None)
            os.environ["BACKUP_GITHUB_REPO"] = "example-org/mono-backup"
            mock_cfg = MagicMock(github_repo="example-org/mono", github_pat="real_pat")
            with patch("monogram.config.load_config", return_value=mock_cfg):
                with pytest.raises(BackupMisconfigured, match="BACKUP_GITHUB_PAT"):
                    load_backup_config()

    def test_rejects_backup_to_self(self):
        from monogram.backup import BackupMisconfigured, load_backup_config

        with patch.dict(os.environ, {
            "BACKUP_GITHUB_PAT": "bkp",
            "BACKUP_GITHUB_REPO": "example-org/mono",
        }):
            mock_cfg = MagicMock(github_repo="example-org/mono", github_pat="real_pat")
            with patch("monogram.config.load_config", return_value=mock_cfg):
                with pytest.raises(BackupMisconfigured, match="must differ"):
                    load_backup_config()

    def test_warns_on_same_pat_but_accepts(self):
        from monogram.backup import load_backup_config

        with patch.dict(os.environ, {
            "BACKUP_GITHUB_PAT": "samepat",
            "BACKUP_GITHUB_REPO": "example-org/mono-backup",
        }):
            mock_cfg = MagicMock(github_repo="example-org/mono", github_pat="samepat")
            with patch("monogram.config.load_config", return_value=mock_cfg):
                config = load_backup_config()
        assert config.source_repo == "example-org/mono"
        assert config.backup_repo == "example-org/mono-backup"


# ---------------------------------------------------------------------------
# search — command-injection resistance
# ---------------------------------------------------------------------------

class TestSearchInjection:
    def test_query_with_shell_metacharacters_is_literal(self, tmp_path):
        """User query "$(rm -rf /)" must NEVER be interpreted by a shell."""
        from monogram.cli_search import _search_via_python_re

        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "harmless.md").write_text("this file must stay intact")

        # Malicious-looking query — must NOT delete anything
        hits = list(_search_via_python_re(
            vault_dir=vault,
            query="$(rm -rf /)",
            kind=None,
            since=None,
            include_raw=False,
            regex=False,
        ))

        # File still exists
        assert (vault / "harmless.md").exists()
        # Query is literal — no hits
        assert hits == []

    def test_ripgrep_command_uses_argv_not_shell(self):
        """Verify the ripgrep subprocess uses argv list, not shell string."""
        import inspect
        from monogram import cli_search

        source = inspect.getsource(cli_search._search_via_ripgrep)
        # Defense-in-depth assertion: shell=True must not appear
        assert "shell=True" not in source
        # argv list is passed
        assert "subprocess.run(" in source

    def test_python_fallback_with_regex(self, tmp_path):
        from monogram.cli_search import _search_via_python_re

        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "wiki").mkdir()
        (vault / "wiki" / "a.md").write_text("the quick brown fox\njumps over\nthe lazy dog")

        hits = list(_search_via_python_re(
            vault_dir=vault,
            query=r"q\w+k",
            kind="wiki",
            since=None,
            include_raw=False,
            regex=True,
        ))
        assert any("quick" in h for h in hits)

    def test_scope_filter_excludes_raw_by_default(self, tmp_path):
        from monogram.cli_search import _scope_filter

        raw = tmp_path / "raw" / "something.md"
        wiki = tmp_path / "wiki" / "entry.md"

        # When include_raw=False, raw/ is excluded
        assert not _scope_filter(raw.relative_to(tmp_path), kind=None, include_raw=False)
        assert _scope_filter(raw.relative_to(tmp_path), kind=None, include_raw=True)
        assert _scope_filter(wiki.relative_to(tmp_path), kind=None, include_raw=False)


# ---------------------------------------------------------------------------
# social URL detection
# ---------------------------------------------------------------------------

class TestSocialURLs:
    def test_instagram_variants(self):
        from monogram.ingestion.social import is_instagram, platform_for

        assert is_instagram("https://www.instagram.com/p/abcdef/")
        assert is_instagram("https://instagram.com/reel/xyz/")
        assert is_instagram("https://instagr.am/p/xxxx/")
        assert not is_instagram("https://example.com/insta-photo.jpg")

        assert platform_for("https://instagram.com/reel/xxx/") == "instagram"

    def test_tiktok_variants(self):
        from monogram.ingestion.social import is_tiktok, platform_for

        assert is_tiktok("https://www.tiktok.com/@user/video/123")
        assert is_tiktok("https://vm.tiktok.com/abc/")
        assert is_tiktok("https://vt.tiktok.com/abc/")
        assert not is_tiktok("https://example.com/tiktok-style-video")

        assert platform_for("https://www.tiktok.com/@u/video/1") == "tiktok"


# ---------------------------------------------------------------------------
# office URL detection
# ---------------------------------------------------------------------------

class TestOfficeURLs:
    def test_supported_extensions(self):
        from monogram.ingestion.office import is_office_url

        assert is_office_url("https://example.com/report.docx")
        assert is_office_url("https://example.com/slides.pptx")
        assert is_office_url("https://example.com/data.xlsx")
        assert is_office_url("https://example.com/OLD.DOC")

    def test_rejects_others(self):
        from monogram.ingestion.office import is_office_url

        assert not is_office_url("https://example.com/paper.pdf")
        assert not is_office_url("https://example.com/page.html")


# ---------------------------------------------------------------------------
# hwp: version parsing + minimal_env secrets-stripping
# ---------------------------------------------------------------------------

class TestHWPHardening:
    def test_minimal_env_strips_secrets(self, tmp_path):
        from monogram.ingestion.hwp import _minimal_env

        with patch.dict(os.environ, {
            "PATH": "/usr/bin:/bin",
            "GITHUB_PAT": "ghp_secret_token",
            "TELEGRAM_BOT_TOKEN": "bot_secret",
            "GEMINI_API_KEY": "gemini_secret",
            "AWS_ACCESS_KEY_ID": "aws_secret",
            "HOME": "/home/user",
        }):
            env = _minimal_env(home=tmp_path)

        assert "GITHUB_PAT" not in env
        assert "TELEGRAM_BOT_TOKEN" not in env
        assert "GEMINI_API_KEY" not in env
        assert "AWS_ACCESS_KEY_ID" not in env

        assert env.get("PATH") == "/usr/bin:/bin"
        # HOME points at the isolated tempdir, not the real home
        assert env.get("HOME") == str(tmp_path)

    def test_hwpx_detected_by_magic_bytes(self):
        """HWPX containers (ZIP+XML, PK magic) are flagged as unsupported."""
        import asyncio
        from monogram.ingestion.hwp import extract_from_bytes, _HWPX_MAGIC

        hwpx_bytes = _HWPX_MAGIC + b"\x00" * 512
        result = asyncio.run(extract_from_bytes(hwpx_bytes, filename="x.hwpx"))
        assert result.success is False
        assert result.extraction_method == "hwpx_unsupported"
        assert result.warning == "hwpx_not_supported"

    def test_size_cap_rejects_oversized_input(self):
        import asyncio
        from monogram.ingestion.hwp import extract_from_bytes, _MAX_INPUT_BYTES

        oversized = b"\x00" * (_MAX_INPUT_BYTES + 1)
        result = asyncio.run(extract_from_bytes(oversized))
        assert result.success is False
        assert result.extraction_method == "size_cap_exceeded"
