"""Tests for the ingestion package.

Focus areas:
- URL extraction (extract_urls, is_youtube, is_arxiv, is_pdf_url)
- SSRF guard (is_safe_url, require_safe_url)
- Dispatcher timeout + exception handling (never raises, always
  returns a result)
- YouTube video ID parser (parse_video_id)
- arXiv ID parser (parse_arxiv_id)
- ExtractionResult slug generation + raw-tier path

Does NOT test live network calls. Extractor integration tests with
mocked HTTP happen in tests/ingestion/test_extractors_integration.py
(added in a follow-up).
"""
from __future__ import annotations

import asyncio
import socket
from unittest.mock import patch

import pytest

from monogram.ingestion.base import (
    ExtractionResult,
    UnsafeURLError,
    extract_urls,
    is_arxiv,
    is_pdf_url,
    is_safe_url,
    is_youtube,
    require_safe_url,
)


# ---------------------------------------------------------------------------
# URL pattern detection
# ---------------------------------------------------------------------------

class TestURLPatterns:
    def test_youtube_watch(self):
        assert is_youtube("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    def test_youtube_short_form(self):
        assert is_youtube("https://youtu.be/dQw4w9WgXcQ")

    def test_youtube_shorts(self):
        assert is_youtube("https://www.youtube.com/shorts/abc123xyz00")

    def test_not_youtube(self):
        assert not is_youtube("https://example.com/video")

    def test_arxiv_abs(self):
        assert is_arxiv("https://arxiv.org/abs/2301.12345")

    def test_arxiv_pdf(self):
        assert is_arxiv("https://arxiv.org/pdf/2301.12345.pdf")

    def test_pdf_url(self):
        assert is_pdf_url("https://example.com/paper.PDF")
        assert is_pdf_url("https://example.com/paper.pdf")
        assert not is_pdf_url("https://example.com/paper.html")


# ---------------------------------------------------------------------------
# extract_urls
# ---------------------------------------------------------------------------

class TestExtractURLs:
    def test_single_url(self):
        text = "Check this out https://example.com/article"
        assert extract_urls(text) == ["https://example.com/article"]

    def test_multiple_urls(self):
        text = "read https://a.com and also https://b.com today"
        assert extract_urls(text) == ["https://a.com", "https://b.com"]

    def test_dedup(self):
        text = "https://a.com and https://a.com again"
        assert extract_urls(text) == ["https://a.com"]

    def test_respects_max(self):
        text = " ".join(f"https://site{i}.com" for i in range(10))
        assert len(extract_urls(text, max_urls=3)) == 3

    def test_trailing_punctuation_stripped(self):
        text = "look at https://example.com/article, nice"
        urls = extract_urls(text)
        assert urls == ["https://example.com/article"]

    def test_empty(self):
        assert extract_urls("") == []
        assert extract_urls("no urls here") == []


# ---------------------------------------------------------------------------
# SSRF guard
# ---------------------------------------------------------------------------

class TestSSRFGuard:
    def test_rejects_file_scheme(self):
        ok, reason = is_safe_url("file:///etc/passwd")
        assert not ok
        assert "scheme" in reason

    def test_rejects_gopher(self):
        ok, reason = is_safe_url("gopher://evil.com/")
        assert not ok

    def test_rejects_ftp(self):
        ok, reason = is_safe_url("ftp://example.com/file")
        assert not ok

    def test_rejects_metadata_dns(self):
        ok, reason = is_safe_url("http://metadata.google.internal/")
        assert not ok
        assert "metadata" in reason

    def test_rejects_localhost_by_dns(self):
        # localhost resolves to 127.0.0.1 which is_loopback
        ok, reason = is_safe_url("http://localhost/")
        assert not ok
        assert "private" in reason or "loopback" in reason

    def test_rejects_127_by_literal(self):
        ok, reason = is_safe_url("http://127.0.0.1:8080/admin")
        assert not ok

    def test_rejects_aws_metadata(self):
        # 169.254.169.254 is link-local (is_link_local=True)
        ok, reason = is_safe_url("http://169.254.169.254/")
        assert not ok

    def test_rejects_encoded_loopback_integer(self):
        # 2130706433 == 0x7f000001 == 127.0.0.1
        with patch.object(socket, "getaddrinfo") as mock_ga:
            mock_ga.return_value = [(None, None, None, None, ("127.0.0.1", 0))]
            ok, reason = is_safe_url("http://2130706433/")
            assert not ok

    def test_rejects_rfc1918(self):
        with patch.object(socket, "getaddrinfo") as mock_ga:
            mock_ga.return_value = [(None, None, None, None, ("10.0.0.5", 0))]
            ok, reason = is_safe_url("http://internal-service.corp/")
            assert not ok

    def test_accepts_real_public(self):
        # Use a real public IP that will resolve on any CI machine
        with patch.object(socket, "getaddrinfo") as mock_ga:
            mock_ga.return_value = [(None, None, None, None, ("93.184.216.34", 0))]
            ok, _ = is_safe_url("https://example.com/")
            assert ok

    def test_require_safe_url_raises(self):
        with pytest.raises(UnsafeURLError):
            require_safe_url("http://127.0.0.1/")

    def test_require_safe_url_passes(self):
        with patch.object(socket, "getaddrinfo") as mock_ga:
            mock_ga.return_value = [(None, None, None, None, ("93.184.216.34", 0))]
            require_safe_url("https://example.com/")  # no raise


# ---------------------------------------------------------------------------
# ExtractionResult
# ---------------------------------------------------------------------------

class TestExtractionResult:
    def test_raw_path_format(self):
        result = ExtractionResult(
            source_type="youtube",
            url="https://youtu.be/abc123xyz00",
            text="transcript here",
        )
        path = result.raw_path()
        assert path.startswith("raw/")
        assert "-youtube-" in path
        assert path.endswith(".md")

    def test_pipeline_snippet_truncates(self):
        result = ExtractionResult(
            source_type="web",
            url="https://example.com",
            text="x" * 3000,
        )
        snippet = result.to_pipeline_snippet(max_chars=500)
        assert len(snippet) < 1000
        assert "truncated" in snippet

    def test_raw_markdown_includes_metadata(self):
        result = ExtractionResult(
            source_type="arxiv",
            url="https://arxiv.org/abs/2301.12345",
            text="Paper summary.",
            metadata={
                "title": "Example Paper",
                "authors": ["Alice", "Bob"],
                "published": "2023-01-15T00:00:00",
            },
            extraction_method="arxiv_api",
        )
        md = result.to_raw_markdown()
        assert "Example Paper" in md
        assert "Alice, Bob" in md
        assert "2023-01-15" in md
        assert "Paper summary." in md

    def test_failed_result_preserves_warning(self):
        result = ExtractionResult(
            source_type="web",
            url="http://example.com/404",
            text="[fetch failed]",
            success=False,
            warning="http_404",
        )
        assert not result.success
        assert result.warning == "http_404"


# ---------------------------------------------------------------------------
# Dispatcher behavior (no live network — verify error paths)
# ---------------------------------------------------------------------------

class TestDispatcherErrorHandling:
    def test_timeout_returns_result_not_raise(self):
        """Dispatcher must never raise — timeout should surface as a
        result with success=False."""
        from monogram.ingestion import extract

        async def slow_extractor(url):
            await asyncio.sleep(5)
            return None

        with patch("monogram.ingestion.web.extract", new=slow_extractor):
            with patch.object(socket, "getaddrinfo") as mock_ga:
                mock_ga.return_value = [
                    (None, None, None, None, ("93.184.216.34", 0))
                ]
                result = asyncio.run(extract("https://example.com/x", timeout=0.2))
        assert result.success is False
        assert result.extraction_method in ("timeout", "error", "blocked", "no_content")

    def test_exception_returns_result_not_raise(self):
        from monogram.ingestion import extract

        async def broken_extractor(url):
            raise RuntimeError("synthetic failure")

        with patch("monogram.ingestion.web.extract", new=broken_extractor):
            with patch.object(socket, "getaddrinfo") as mock_ga:
                mock_ga.return_value = [
                    (None, None, None, None, ("93.184.216.34", 0))
                ]
                result = asyncio.run(extract("https://example.com/x", timeout=5.0))
        assert result.success is False
        assert result.warning is not None


# ---------------------------------------------------------------------------
# YouTube / arXiv ID parsers
# ---------------------------------------------------------------------------

class TestYouTubeParser:
    def test_parse_watch_url(self):
        from monogram.ingestion.youtube import parse_video_id
        assert parse_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_parse_short_url(self):
        from monogram.ingestion.youtube import parse_video_id
        assert parse_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_parse_shorts_url(self):
        from monogram.ingestion.youtube import parse_video_id
        assert parse_video_id("https://youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_unparseable_returns_none(self):
        from monogram.ingestion.youtube import parse_video_id
        assert parse_video_id("https://example.com") is None


class TestArXivParser:
    def test_new_format(self):
        from monogram.ingestion.arxiv_source import parse_arxiv_id
        assert parse_arxiv_id("https://arxiv.org/abs/2301.12345") == "2301.12345"

    def test_with_version(self):
        from monogram.ingestion.arxiv_source import parse_arxiv_id
        # v1 suffix should be discarded
        assert parse_arxiv_id("https://arxiv.org/abs/2301.12345v2") == "2301.12345"

    def test_old_format(self):
        from monogram.ingestion.arxiv_source import parse_arxiv_id
        assert parse_arxiv_id("https://arxiv.org/abs/cs/0701001") == "cs/0701001"

    def test_pdf_url_format(self):
        from monogram.ingestion.arxiv_source import parse_arxiv_id
        assert parse_arxiv_id("https://arxiv.org/pdf/2301.12345.pdf") == "2301.12345"


# ---------------------------------------------------------------------------
# vault_config ingestion fields default
# ---------------------------------------------------------------------------

class TestVaultConfigIngestionDefaults:
    def test_defaults(self):
        from monogram.vault_config import VaultConfig
        cfg = VaultConfig()
        assert cfg.ingestion_enabled is True
        assert cfg.ingestion_timeout_seconds == 10.0
        assert cfg.ingestion_max_urls_per_drop == 3
        assert cfg.youtube_whisper_fallback is False
        assert cfg.arxiv_enrichment is True
