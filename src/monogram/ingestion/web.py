"""Web page extractor — trafilatura primary, jina.ai reader fallback.

Why trafilatura: it's the highest-accuracy body-text extractor for
modern HTML (outperforms readability-lxml, newspaper3k, Goose3). Zero
ML models, works fully offline once the page is fetched.

Why jina as fallback: for JS-heavy pages where trafilatura's HTML parse
returns empty, jina.ai/reader renders the page server-side (free,
no-auth) and returns clean markdown.

SSRF gate: `require_safe_url` BEFORE fetch — blocks private IPs and
non-HTTP schemes. The optional jina fallback also requires the URL to
have passed the safe check locally, since jina.ai will happily fetch
internal URLs if we pass them in (no, they won't — jina resolves the
URL from its own infra — but we don't want to tell jina about our
internal topology either).
"""
from __future__ import annotations

import asyncio
import logging

from .base import ExtractionResult, require_safe_url

log = logging.getLogger("monogram.ingestion.web")


async def extract(url: str) -> ExtractionResult:
    """Extract main content from a web page URL."""
    try:
        require_safe_url(url)
    except Exception as e:
        return ExtractionResult(
            source_type="web",
            url=url,
            text=f"[Web fetch blocked: {e}]",
            success=False,
            extraction_method="blocked",
            warning=str(e),
        )

    # Tier 1: trafilatura (fast, accurate for static HTML)
    text = await _trafilatura_extract(url)
    if text and len(text) >= 200:
        return ExtractionResult(
            source_type="web",
            url=url,
            text=text,
            metadata={"extractor": "trafilatura"},
            extraction_method="trafilatura",
        )

    # Tier 2: jina.ai reader (free, renders JS-heavy pages)
    jina_text = await _jina_reader_extract(url)
    if jina_text and len(jina_text) >= 100:
        return ExtractionResult(
            source_type="web",
            url=url,
            text=jina_text,
            metadata={"extractor": "jina_reader"},
            extraction_method="jina_reader",
            warning="used_jina_fallback" if text else None,
        )

    # Both failed — return whatever trafilatura got, with warning
    return ExtractionResult(
        source_type="web",
        url=url,
        text=text or f"[Web extraction returned no content for {url}]",
        metadata={"extractor": "none"},
        extraction_method="no_content",
        success=bool(text),
        warning="both_extractors_returned_empty",
    )


async def _trafilatura_extract(url: str) -> str | None:
    """Use trafilatura to fetch + extract main body text."""
    def _sync() -> str | None:
        try:
            import trafilatura  # type: ignore
        except ImportError:
            log.debug("trafilatura not installed")
            return None

        try:
            # trafilatura.fetch_url returns the raw HTML (or None)
            downloaded = trafilatura.fetch_url(url)
            if not downloaded:
                return None
            # extract returns the main body text as plain text or markdown
            return trafilatura.extract(
                downloaded,
                output_format="markdown",
                include_comments=False,
                include_tables=True,
                favor_recall=False,
            )
        except Exception as e:
            log.warning("trafilatura failed for %s: %s", url, e)
            return None

    return await asyncio.to_thread(_sync)


async def _jina_reader_extract(url: str) -> str | None:
    """Fallback: jina.ai/reader serves clean markdown for any URL.

    No auth. Free tier: 200 req/min.
    """
    def _sync() -> str | None:
        try:
            import httpx
        except ImportError:
            return None

        reader_url = f"https://r.jina.ai/{url}"
        try:
            resp = httpx.get(
                reader_url,
                timeout=15.0,
                headers={"Accept": "text/plain"},
            )
            if resp.status_code == 200 and resp.text:
                return resp.text
            log.debug("jina reader %d for %s", resp.status_code, url)
            return None
        except Exception as e:
            log.warning("jina reader error for %s: %s", url, e)
            return None

    return await asyncio.to_thread(_sync)
