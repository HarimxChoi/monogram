"""Tests for v0.8.1 security hardening fixes.

Covers every fix in the pre-release validation:
- #1 redirect SSRF (pdf/office) — every hop re-validated
- #2 CGNAT (100.64/10) + IPv4-mapped IPv6 blocked
- #3 social extractor calls require_safe_url before yt-dlp handoff
- #4 publish workflow tag-version match (smoke-tested via file inspection)
- #5 case-insensitive backup-to-self guard
- #6 ff-conflict detector accepts any 422
- #7 arxiv shared client singleton (concurrency rate-limit)
- #8 latency warmup annotation for n<10
- #9 .gitignore backup before migrate apply
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# #2 — CGNAT + exotic ranges blocked
# ---------------------------------------------------------------------------

class TestCGNATBlock:
    def test_alibaba_metadata_endpoint_blocked(self):
        from monogram.ingestion.base import is_safe_url
        # 100.100.100.200 is Alibaba Cloud's metadata endpoint in
        # RFC 6598 carrier-grade NAT space. Python's is_private misses it.
        with patch("monogram.ingestion.base._socket.getaddrinfo",
                   return_value=[(0, 0, 0, "", ("100.100.100.200", 0))]):
            ok, reason = is_safe_url("http://attacker.example.com/")
        assert not ok
        assert "100.64.0.0/10" in reason or "100.100.100.200" in reason

    def test_regular_cgnat_ip_blocked(self):
        from monogram.ingestion.base import is_safe_url
        with patch("monogram.ingestion.base._socket.getaddrinfo",
                   return_value=[(0, 0, 0, "", ("100.64.1.1", 0))]):
            ok, _ = is_safe_url("http://foo.example.com/")
        assert not ok

    def test_public_ip_still_passes(self):
        from monogram.ingestion.base import is_safe_url
        with patch("monogram.ingestion.base._socket.getaddrinfo",
                   return_value=[(0, 0, 0, "", ("8.8.8.8", 0))]):
            ok, reason = is_safe_url("http://dns.google/")
        assert ok, reason


# ---------------------------------------------------------------------------
# #1 — Redirect SSRF via safe_stream_bytes
# ---------------------------------------------------------------------------

class TestRedirectSSRF:
    def test_safe_stream_rejects_unsafe_initial(self):
        from monogram.ingestion.base import safe_stream_bytes
        with patch("monogram.ingestion.base._socket.getaddrinfo",
                   return_value=[(0, 0, 0, "", ("127.0.0.1", 0))]):
            result = safe_stream_bytes("http://localhost/evil", max_bytes=1024)
        assert result is None

    def test_safe_stream_rejects_redirect_to_internal(self):
        """Public URL → 302 → http://127.0.0.1/admin must be blocked."""
        from monogram.ingestion.base import safe_stream_bytes

        call_count = {"n": 0}
        def fake_getaddrinfo(host, *a, **kw):
            call_count["n"] += 1
            # First hop: public IP. Second hop: loopback.
            if call_count["n"] == 1:
                return [(0, 0, 0, "", ("8.8.8.8", 0))]
            return [(0, 0, 0, "", ("127.0.0.1", 0))]

        mock_redirect_resp = MagicMock()
        mock_redirect_resp.status_code = 302
        mock_redirect_resp.headers = {"location": "http://internal.evil/"}
        mock_redirect_resp.__enter__ = lambda self: self
        mock_redirect_resp.__exit__ = lambda self, *a: None

        with patch("monogram.ingestion.base._socket.getaddrinfo",
                   side_effect=fake_getaddrinfo), \
             patch("httpx.stream", return_value=mock_redirect_resp):
            result = safe_stream_bytes(
                "http://public.example.com/file.pdf", max_bytes=1024
            )
        # Redirect target was internal → rejected → None
        assert result is None
        # Both hops were checked (DNS called at least twice)
        assert call_count["n"] >= 2

    def test_safe_stream_respects_max_redirects(self):
        from monogram.ingestion.base import safe_stream_bytes

        mock_resp = MagicMock()
        mock_resp.status_code = 302
        mock_resp.headers = {"location": "http://example.com/next"}
        mock_resp.__enter__ = lambda self: self
        mock_resp.__exit__ = lambda self, *a: None

        with patch("monogram.ingestion.base._socket.getaddrinfo",
                   return_value=[(0, 0, 0, "", ("8.8.8.8", 0))]), \
             patch("httpx.stream", return_value=mock_resp):
            result = safe_stream_bytes(
                "http://example.com/a", max_bytes=1024, max_redirects=3
            )
        # Infinite redirect loop → None
        assert result is None


# ---------------------------------------------------------------------------
# #3 — social extractor blocks unsafe URLs before yt-dlp
# ---------------------------------------------------------------------------

class TestSocialSSRFGate:
    def test_fake_tiktok_pointing_at_localhost_blocked(self):
        """An Instagram/TikTok-shaped URL that resolves to loopback must
        not reach yt-dlp."""
        from monogram.ingestion import social

        # Craft a URL that matches the IG host matcher
        with patch("monogram.ingestion.base._socket.getaddrinfo",
                   return_value=[(0, 0, 0, "", ("127.0.0.1", 0))]):
            result = asyncio.run(social.extract(
                "https://www.instagram.com/p/abc/"
            ))

        assert not result.success
        assert result.extraction_method == "blocked"
        assert "unsafe URL" in result.text or "blocked" in result.text


# ---------------------------------------------------------------------------
# #5 — case-insensitive backup-to-self
# ---------------------------------------------------------------------------

class TestBackupCaseInsensitive:
    def test_different_case_same_repo_rejected(self):
        import os
        from monogram.backup import BackupMisconfigured, load_backup_config

        with patch.dict(os.environ, {
            "BACKUP_GITHUB_PAT": "bkp",
            "BACKUP_GITHUB_REPO": "example-org/mono",  # lowercase
        }):
            mock_cfg = MagicMock(
                github_repo="example-org/mono",  # different case
                github_pat="real_pat",
            )
            with patch("monogram.config.load_config", return_value=mock_cfg):
                with pytest.raises(BackupMisconfigured, match="must differ"):
                    load_backup_config()


# ---------------------------------------------------------------------------
# #6 — ff-conflict detector accepts any 422
# ---------------------------------------------------------------------------

class TestFastForwardDetector:
    def test_any_422_treated_retryable(self):
        from monogram.github_store import _is_fast_forward_conflict
        from github.GithubException import GithubException

        # Old behavior: only matched "fast-forward" substring
        # New behavior: any 422 (more robust to wording changes)
        assert _is_fast_forward_conflict(
            GithubException(422, {"message": "Validation failed"}, {})
        )
        assert _is_fast_forward_conflict(
            GithubException(422, {"message": "whatever"}, {})
        )
        assert not _is_fast_forward_conflict(
            GithubException(500, {"message": "boom"}, {})
        )
        assert not _is_fast_forward_conflict(
            GithubException(404, {"message": "not found"}, {})
        )


# ---------------------------------------------------------------------------
# #7 — arxiv shared client singleton
# ---------------------------------------------------------------------------

class TestArxivSharedClient:
    def test_client_is_singleton(self):
        import monogram.ingestion.arxiv_source as mod

        # Reset module state
        mod._arxiv_client = None

        fake_arxiv = MagicMock()
        fake_arxiv.Client = MagicMock(return_value=MagicMock(name="client_instance"))

        c1 = mod._get_arxiv_client(fake_arxiv)
        c2 = mod._get_arxiv_client(fake_arxiv)
        c3 = mod._get_arxiv_client(fake_arxiv)

        assert c1 is c2 is c3
        # arxiv.Client() constructor called exactly once
        assert fake_arxiv.Client.call_count == 1


# ---------------------------------------------------------------------------
# #8 — warmup annotation for small samples
# ---------------------------------------------------------------------------

class TestLatencyWarmupAnnotation:
    def test_markdown_has_warmup_note_below_10(self):
        from monogram.pipeline_stats import (
            LatencySummary, PipelineStats,
        )
        stats = PipelineStats(
            window_days=7,
            total_runs=3,
            error_rate=0.0,
            escalation_rate=0.0,
            latency=LatencySummary(3, 100, 200, 300, 150, 100, 300),
            per_stage=[],
            by_target_kind={},
            computed_at="2026-04-25T00:00:00+00:00",
        )
        md = stats.to_markdown()
        assert "sparse" in md or "unreliable" in md

    def test_markdown_no_warmup_note_above_10(self):
        from monogram.pipeline_stats import (
            LatencySummary, PipelineStats,
        )
        stats = PipelineStats(
            window_days=7,
            total_runs=50,
            error_rate=0.0,
            escalation_rate=0.0,
            latency=LatencySummary(50, 100, 200, 300, 150, 100, 500),
            per_stage=[],
            by_target_kind={},
            computed_at="2026-04-25T00:00:00+00:00",
        )
        md = stats.to_markdown()
        assert "sparse" not in md and "unreliable" not in md


# ---------------------------------------------------------------------------
# #9 — .gitignore backup before mutation
# ---------------------------------------------------------------------------

class TestMigrationBackup:
    def test_gitignore_backup_created_before_mutation(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("# existing\n*.pyc\n")
        original_content = gitignore.read_text()

        from monogram.cli_migrate import _apply_gitignore_migration
        _apply_gitignore_migration()

        # Original content still recoverable from a .bak.<ts> sibling
        backups = list(tmp_path.glob(".gitignore.bak.*"))
        assert len(backups) == 1
        assert backups[0].read_text() == original_content

    def test_no_backup_when_no_op(self, tmp_path, monkeypatch):
        """If all required patterns already present, no backup."""
        monkeypatch.chdir(tmp_path)
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text(
            "*.session\n*.session-journal\n.env\ngcp-sa.json\n"
        )

        from monogram.cli_migrate import _apply_gitignore_migration
        _apply_gitignore_migration()

        backups = list(tmp_path.glob(".gitignore.bak.*"))
        assert len(backups) == 0


# ---------------------------------------------------------------------------
# #4 — publish workflow has version-tag guard (smoke test)
# ---------------------------------------------------------------------------

class TestPublishWorkflowGuards:
    def test_workflow_has_version_match_step(self):
        from pathlib import Path
        content = Path(".github/workflows/publish.yml").read_text()
        # The step name is what the reviewer will see in CI
        assert "Verify tag matches pyproject.toml version" in content
        assert "TAG_VERSION" in content
        assert "FILE_VERSION" in content

    def test_publish_to_pypi_skips_pre_releases(self):
        from pathlib import Path
        content = Path(".github/workflows/publish.yml").read_text()
        # Must have `!contains(github.ref, '-')` gate on the PyPI job
        assert "!contains(github.ref, '-')" in content
