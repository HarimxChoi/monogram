"""Web page extractor — trafilatura primary, jina.ai reader fallback.

Why trafilatura: highest-accuracy body-text extractor for modern HTML
(outperforms readability-lxml, newspaper3k, Goose3). Zero ML models,
works fully offline once the page is fetched.

Why jina as fallback: for JS-heavy pages where trafilatura returns
empty, jina.ai/reader renders the page server-side (free, no-auth)
and returns clean markdown.

SSRF gate: we fetch via `safe_stream_bytes` — which validates every
redirect hop with `require_safe_url` — rather than `trafilatura.fetch_url`
(which auto-follows redirects without per-hop validation). Parsing is
then done in-process via `trafilatura.extract(html)`.

Jina fallback strips the query string from the outbound URL so any
tokens embedded in the drop don't end up in jina.ai's logs.
"""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlsplit, urlunsplit

from .base import ExtractionResult, require_safe_url, safe_stream_bytes

log = logging.getLogger("monogram.ingestion.web")

_WEB_HTML_CAP = 5 * 1024 * 1024  # 5 MB — plenty for any real article


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
    """Fetch via safe_stream_bytes (per-hop SSRF validation), then parse
    with trafilatura.extract. We intentionally do NOT use
    trafilatura.fetch_url — it auto-follows redirects without validating
    each hop against require_safe_url.
    """
    def _sync() -> str | None:
        try:
            import trafilatura  # type: ignore
        except ImportError:
            log.debug("trafilatura not installed")
            return None

        html_bytes = safe_stream_bytes(url, max_bytes=_WEB_HTML_CAP, timeout=15.0)
        if not html_bytes:
            return None

        # Explicit decode — some trafilatura versions handle bytes, some
        # expect str. utf-8 with errors="replace" is a safe default: the
        # vast majority of modern web is UTF-8, and malformed bytes just
        # become replacement chars rather than raising.
        try:
            html = html_bytes.decode("utf-8", errors="replace")
        except Exception as e:  # pragma: no cover — decode shouldn't raise here
            log.warning("html decode failed for %s: %s", url, e)
            return None

        try:
            return trafilatura.extract(
                html,
                output_format="markdown",
                include_comments=False,
                include_tables=True,
                favor_recall=False,
            )
        except Exception as e:
            log.warning("trafilatura.extract failed for %s: %s", url, e)
            return None

    return await asyncio.to_thread(_sync)


async def _jina_reader_extract(url: str) -> str | None:
    """Fallback: jina.ai/reader serves clean markdown for any URL.

    Strips query/fragment so tokens embedded in the drop's URL don't
    land in jina.ai's access logs. No auth. Free tier: 200 req/min.
    """
    parts = urlsplit(url)
    cleaned = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

    def _sync() -> str | None:
        try:
            import httpx
        except ImportError:
            return None

        reader_url = f"https://r.jina.ai/{cleaned}"
        try:
            resp = httpx.get(
                reader_url,
                timeout=15.0,
                headers={"Accept": "text/plain"},
            )
            if resp.status_code == 200 and resp.text:
                return resp.text
            log.debug("jina reader %d for %s", resp.status_code, cleaned)
            return None
        except Exception as e:
            log.warning("jina reader error for %s: %s", cleaned, e)
            return None

    return await asyncio.to_thread(_sync)
