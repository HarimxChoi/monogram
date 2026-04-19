"""arXiv extractor — uses the `arxiv` Python library (handles the
1-request-per-3-seconds rate limit by default) + optional Semantic
Scholar enrichment for citations.

Module named `arxiv_source` (not `arxiv`) to avoid shadowing the
third-party library when imported from within the package.

Critical:
  - arXiv ToU is 1 request per 3 seconds (NOT 3/sec — commonly
    misstated). The `arxiv` library's Client defaults `delay_seconds=3.0`
    which respects this. We use defaults.
  - As of Feb 2026 arXiv tightened 429 enforcement (see API Discussion
    thread). Defaults still work; aggressive batch enrichment should
    move to morning_job, not per-drop.
  - Semantic Scholar is free, no auth needed, but has its own 100 req / 5 min
    limit. Graceful degradation on 429.
"""
from __future__ import annotations

import asyncio
import logging
import re

from .base import ExtractionResult

log = logging.getLogger("monogram.ingestion.arxiv")

# Module-level client cache. The arxiv library's Client tracks its own
# "last request" timestamp, which is what actually enforces the 3-second
# global gap. Per-call Client() defeats that — concurrent asyncio.gather
# callers each get a fresh-timestamp instance and fire simultaneously.
_arxiv_client: object | None = None


def _get_arxiv_client(arxiv_module):
    """Return the process-wide arxiv.Client singleton."""
    global _arxiv_client
    if _arxiv_client is None:
        _arxiv_client = arxiv_module.Client()
    return _arxiv_client


# arXiv ID patterns:
#   new format:  2301.12345 / 2301.12345v2
#   old format:  cs/0701001 / hep-ph/9901001
_ARXIV_ID_RE = re.compile(
    r"arxiv\.org/(?:abs|pdf)/([a-z\-]+/\d{7}|\d{4}\.\d{4,5})(v\d+)?",
    re.IGNORECASE,
)


def parse_arxiv_id(url: str) -> str | None:
    match = _ARXIV_ID_RE.search(url)
    if not match:
        return None
    # Base ID without version suffix
    return match.group(1)


async def extract(url: str) -> ExtractionResult:
    paper_id = parse_arxiv_id(url)
    if not paper_id:
        return ExtractionResult(
            source_type="arxiv",
            url=url,
            text=f"[Could not parse arXiv ID from {url}]",
            success=False,
            extraction_method="parse_failed",
            warning="invalid_arxiv_id",
        )

    paper_data = await _fetch_arxiv(paper_id)
    if not paper_data:
        return ExtractionResult(
            source_type="arxiv",
            url=url,
            text=f"[arXiv fetch failed for {paper_id}]",
            success=False,
            extraction_method="fetch_failed",
            warning="arxiv_api_error",
        )

    # Optional enrichment — opt-in via vault_config.arxiv_enrichment
    if await _is_enrichment_enabled():
        s2 = await _fetch_semantic_scholar(paper_id)
        if s2:
            paper_data["citations"] = s2.get("citationCount")
            paper_data["influential_citations"] = s2.get(
                "influentialCitationCount"
            )

    text_body = f"{paper_data['title']}\n\n{paper_data['summary']}"

    return ExtractionResult(
        source_type="arxiv",
        url=url,
        text=text_body,
        metadata=paper_data,
        extraction_method="arxiv_api",
    )


async def _fetch_arxiv(paper_id: str) -> dict | None:
    """Use the `arxiv` library with its default 3-second rate limit."""
    def _sync() -> dict | None:
        try:
            import arxiv  # type: ignore
        except ImportError:
            log.debug("arxiv library not installed")
            return None

        try:
            search = arxiv.Search(id_list=[paper_id])
            # Shared module-level client — every ingestion of an arXiv
            # URL goes through the same rate-limiter state. Two concurrent
            # drops with arXiv links won't each fire instantly.
            client = _get_arxiv_client(arxiv)
            paper = next(client.results(search))
        except StopIteration:
            return None
        except Exception as e:
            log.warning("arxiv fetch failed for %s: %s", paper_id, e)
            return None

        return {
            "id": paper_id,
            "title": paper.title,
            "summary": paper.summary,
            "authors": [str(a) for a in paper.authors],
            "published": paper.published.isoformat() if paper.published else None,
            "categories": paper.categories,
            "pdf_url": paper.pdf_url,
        }

    return await asyncio.to_thread(_sync)


async def _is_enrichment_enabled() -> bool:
    """Semantic Scholar enrichment is opt-in via vault config."""
    try:
        from ..vault_config import load_vault_config
        cfg = load_vault_config()
        return bool(getattr(cfg, "arxiv_enrichment", True))
    except Exception:
        return True


async def _fetch_semantic_scholar(paper_id: str) -> dict | None:
    """Fetch citation count from Semantic Scholar — free, no auth.

    Rate limit: 100 req / 5 min per IP. Graceful fail on 429.
    """
    def _sync() -> dict | None:
        try:
            import httpx  # type: ignore
        except ImportError:
            return None

        url = f"https://api.semanticscholar.org/graph/v1/paper/arXiv:{paper_id}"
        params = {"fields": "citationCount,influentialCitationCount"}
        try:
            resp = httpx.get(url, params=params, timeout=5.0)
            if resp.status_code != 200:
                log.debug("semantic scholar %d for %s", resp.status_code, paper_id)
                return None
            return resp.json()
        except Exception as e:
            log.debug("semantic scholar error for %s: %s", paper_id, e)
            return None

    return await asyncio.to_thread(_sync)
