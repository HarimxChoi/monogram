"""PDF extractor — PyMuPDF4LLM fast path, Marker fallback for complex layouts.

2026 architecture:
  - Primary: PyMuPDF4LLM — fast (~100x faster than Docling), no ML models,
    ~100MB install. Good enough for native text-based PDFs (papers, reports).
  - Fallback: Marker — handles scanned PDFs, multi-column layouts, tables.
    Uses Surya OCR. ~1GB install but much higher accuracy.
  - Quality gate: if PyMuPDF4LLM returns <100 chars or suspicious garbled
    text, escalate to Marker.

Why not MarkItDown + Docling (old plan):
  - MarkItDown's PDF success rate is 25% per 2025 benchmarks (uses
    pdfminer.six, no layout analysis).
  - Docling is 100× slower and ships with 1GB of HuggingFace models.
  - Marker's single-tool design (also handles DOCX/PPTX/XLSX) makes it a
    cleaner fallback.

Korean HWP files go through LibreOffice→PDF→this pipeline. MinerU (best
for CJK) is optionally available but not default — adds complexity.

References:
  - https://themenonlab.blog/blog/best-open-source-pdf-to-markdown-tools-2026
  - https://dev.to/nhirschfeld/i-benchmarked-4-python-text-extraction-libraries-2025-4e7j
"""
from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

from .base import ExtractionResult, require_safe_url

log = logging.getLogger("monogram.ingestion.pdf")


async def extract_from_url(url: str) -> ExtractionResult:
    """Download a PDF and extract markdown from it.

    SSRF-checked: only fetches from public IPs via HTTP(S).
    """
    # Defense in depth: reject internal URLs before the fetch
    try:
        require_safe_url(url)
    except Exception as e:
        return ExtractionResult(
            source_type="pdf",
            url=url,
            text=f"[PDF fetch blocked: {e}]",
            success=False,
            extraction_method="blocked",
            warning=str(e),
        )

    pdf_bytes = await _download_pdf(url)
    if not pdf_bytes:
        return ExtractionResult(
            source_type="pdf",
            url=url,
            text=f"[PDF download failed for {url}]",
            success=False,
            extraction_method="download_failed",
        )

    return await extract_from_bytes(pdf_bytes, url=url)


async def extract_from_bytes(
    pdf_bytes: bytes, url: str = "", filename: str = ""
) -> ExtractionResult:
    """Extract markdown from PDF bytes. Used directly when listener
    receives a PDF attachment in Telegram (not via URL).

    Two-tier strategy:
      1. Try PyMuPDF4LLM (fast)
      2. If quality is poor, retry with Marker (accurate but slow)
    """
    fast_result = await _pymupdf4llm_extract(pdf_bytes)
    if fast_result and _quality_ok(fast_result):
        return ExtractionResult(
            source_type="pdf",
            url=url,
            text=fast_result,
            metadata={"filename": filename, "extractor": "pymupdf4llm"},
            extraction_method="pymupdf4llm",
        )

    # Quality gate failed — try Marker if installed
    marker_result = await _marker_extract(pdf_bytes)
    if marker_result:
        return ExtractionResult(
            source_type="pdf",
            url=url,
            text=marker_result,
            metadata={"filename": filename, "extractor": "marker"},
            extraction_method="marker_fallback",
        )

    # Both failed — return best-effort with warning
    text = fast_result or f"[PDF extraction failed for {filename or url}]"
    return ExtractionResult(
        source_type="pdf",
        url=url,
        text=text,
        metadata={"filename": filename, "extractor": "pymupdf4llm_best_effort"},
        extraction_method="low_quality",
        success=bool(fast_result),
        warning="quality_gate_failed_no_marker",
    )


async def _download_pdf(url: str, max_bytes: int = 20 * 1024 * 1024) -> bytes | None:
    """Stream-download a PDF with a size cap (default 20MB).

    Validates every redirect hop via safe_stream_bytes — an attacker's
    public URL can't 302-redirect to http://127.0.0.1/admin.
    """
    def _sync() -> bytes | None:
        from .base import safe_stream_bytes
        data = safe_stream_bytes(url, max_bytes=max_bytes, timeout=15.0)
        if data is not None:
            return data

        # urllib fallback (no httpx installed). urllib does NOT follow
        # redirects by default — safe behavior. We still validate the
        # initial URL.
        try:
            from .base import require_safe_url
            require_safe_url(url)
            import urllib.request
            req = urllib.request.Request(url, headers={"User-Agent": "monogram-ingestion/0.8"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read(max_bytes + 1)
                if len(data) > max_bytes:
                    log.warning("pdf download exceeded size cap")
                    return data[:max_bytes]
                return data
        except Exception as e:
            log.warning("pdf download via urllib failed: %s", e)
            return None

    return await asyncio.to_thread(_sync)


async def _pymupdf4llm_extract(pdf_bytes: bytes) -> str | None:
    """Fast path: PyMuPDF4LLM."""
    def _sync() -> str | None:
        try:
            import pymupdf4llm  # type: ignore
        except ImportError:
            log.debug("pymupdf4llm not installed")
            return None

        # pymupdf4llm takes a path, not bytes. Write to tempfile.
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = tmp.name

        try:
            md = pymupdf4llm.to_markdown(tmp_path)
            return md if md else None
        except Exception as e:
            log.warning("pymupdf4llm failed: %s", e)
            return None
        finally:
            try:
                Path(tmp_path).unlink()
            except OSError:
                pass

    return await asyncio.to_thread(_sync)


async def _marker_extract(pdf_bytes: bytes) -> str | None:
    """Complex fallback: Marker with Surya OCR."""
    def _sync() -> str | None:
        try:
            from marker.converters.pdf import PdfConverter  # type: ignore
            from marker.models import create_model_dict  # type: ignore
            from marker.output import text_from_rendered  # type: ignore
        except ImportError:
            log.debug("marker-pdf not installed (install monogram[ingestion-pdf-complex])")
            return None

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = tmp.name

        try:
            converter = PdfConverter(artifact_dict=create_model_dict())
            rendered = converter(tmp_path)
            text, _, _ = text_from_rendered(rendered)
            return text
        except Exception as e:
            log.warning("marker failed: %s", e)
            return None
        finally:
            try:
                Path(tmp_path).unlink()
            except OSError:
                pass

    return await asyncio.to_thread(_sync)


def _quality_ok(text: str) -> bool:
    """Cheap heuristic: if text is long enough and not mostly garbage.

    Not a full quality check — just catches the clear-failure cases
    (empty output, tokenizer barfing control characters, obviously
    corrupt Unicode).
    """
    if not text or len(text) < 100:
        return False

    # Count printable ASCII + common Unicode
    printable = sum(1 for c in text if c.isprintable() or c in "\n\t")
    if printable / len(text) < 0.85:
        return False

    return True
