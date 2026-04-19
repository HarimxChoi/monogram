"""Ingestion dispatcher — routes a URL to the right extractor.

Usage from listener:
    from monogram.ingestion import extract_if_url, enrich_drop

    enriched_text = await enrich_drop(original_drop_text)

Design:
  - Each extractor is LAZILY imported so missing optional-deps don't
    fail at startup. If `monogram[ingestion-video]` isn't installed,
    YouTube URLs gracefully degrade to the web extractor.
  - Extractors return ExtractionResult. Failures return a result with
    success=False and a warning instead of raising — we never let
    ingestion break drop processing.
  - Timeout cap per extraction via `ingestion_timeout_seconds` config
    (default 10s). Slow extractor cannot block the pipeline.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from .base import (
    ExtractionResult,
    extract_urls,
    is_arxiv,
    is_pdf_url,
    is_youtube,
)

log = logging.getLogger("monogram.ingestion")


async def extract(url: str, timeout: float = 10.0) -> ExtractionResult:
    """Route URL to the appropriate extractor with a timeout guard."""
    try:
        return await asyncio.wait_for(_dispatch(url), timeout=timeout)
    except asyncio.TimeoutError:
        log.warning("ingestion: timeout after %.1fs for %s", timeout, url)
        return ExtractionResult(
            source_type="timeout",
            url=url,
            text=f"[Extraction timed out for {url}]",
            success=False,
            extraction_method="timeout",
            warning=f"timeout after {timeout}s",
        )
    except Exception as e:
        log.warning("ingestion: %s failed for %s: %s", type(e).__name__, url, e)
        return ExtractionResult(
            source_type="error",
            url=url,
            text=f"[Extraction failed for {url}: {type(e).__name__}]",
            success=False,
            extraction_method="error",
            warning=str(e)[:200],
        )


async def _dispatch(url: str) -> ExtractionResult:
    """Internal: select extractor and call it. Lazy imports guard extras."""
    if is_youtube(url):
        from . import youtube
        return await youtube.extract(url)

    if is_arxiv(url):
        from . import arxiv_source
        return await arxiv_source.extract(url)

    if is_pdf_url(url):
        from . import pdf
        return await pdf.extract_from_url(url)

    # v0.8 Tier 3: social + office URL routing
    from . import social, office
    if social.is_instagram(url) or social.is_tiktok(url):
        return await social.extract(url)

    if office.is_office_url(url):
        return await office.extract_from_url(url)

    # Default: web page extraction
    from . import web
    return await web.extract(url)


async def enrich_drop(text: str, config: Any | None = None) -> tuple[str, list[ExtractionResult]]:
    """Extract content from any URLs in the drop text.

    Returns (enriched_text, list_of_results). Caller writes each result
    to raw/ tier and optionally uses the enriched text for the pipeline.

    If config is None, uses sensible defaults. Otherwise expects a
    vault_config with `ingestion_timeout_seconds` and
    `ingestion_max_urls_per_drop`.
    """
    timeout = getattr(config, "ingestion_timeout_seconds", 10.0) if config else 10.0
    max_urls = getattr(config, "ingestion_max_urls_per_drop", 3) if config else 3

    urls = extract_urls(text, max_urls=max_urls)
    if not urls:
        return text, []

    log.info("ingestion: extracting %d URL(s) from drop", len(urls))
    results = await asyncio.gather(*(extract(u, timeout=timeout) for u in urls))

    # Build enriched text: original + each snippet
    snippets = [r.to_pipeline_snippet() for r in results if r.text]
    enriched = text + "".join(snippets)
    return enriched, results


# Public API re-exports ----------------------------------------------------

__all__ = ["extract", "enrich_drop", "ExtractionResult", "extract_urls"]


# Alias for code search (some places may look for `extract_if_url`)
async def extract_if_url(url: str, timeout: float = 10.0) -> ExtractionResult | None:
    """Convenience: return None if `url` doesn't look like one."""
    if not url or not url.startswith(("http://", "https://")):
        return None
    return await extract(url, timeout=timeout)
