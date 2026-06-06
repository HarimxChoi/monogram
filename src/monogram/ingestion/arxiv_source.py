"""arXiv extractor — named arxiv_source (not arxiv) to avoid shadowing the third-party library."""
from __future__ import annotations

import asyncio
import logging
import re

from .base import ExtractionResult

log = logging.getLogger("monogram.ingestion.arxiv")

# Module-level singleton so concurrent callers share the arxiv.Client rate-limiter state.
_arxiv_client: object | None = None


def _get_arxiv_client(arxiv_module):
    global _arxiv_client
    if _arxiv_client is None:
        _arxiv_client = arxiv_module.Client()
    return _arxiv_client


_ARXIV_ID_RE = re.compile(
    r"arxiv\.org/(?:abs|pdf)/([a-z\-]+/\d{7}|\d{4}\.\d{4,5})(v\d+)?",
    re.IGNORECASE,
)


def parse_arxiv_id(url: str) -> str | None:
    match = _ARXIV_ID_RE.search(url)
    if not match:
        return None
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
    def _sync() -> dict | None:
        try:
            import arxiv  # type: ignore
        except ImportError:
            log.debug("arxiv library not installed")
            return None

        try:
            search = arxiv.Search(id_list=[paper_id])
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
    try:
        from ..vault_config import load_vault_config
        cfg = load_vault_config()
        return bool(getattr(cfg, "arxiv_enrichment", True))
    except Exception:
        return True


async def _fetch_semantic_scholar(paper_id: str) -> dict | None:
    """100 req/5min rate limit; graceful fail on 429."""
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
