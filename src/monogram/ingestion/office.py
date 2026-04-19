"""Office-document extractor — docx, pptx, xlsx via MarkItDown.

Why MarkItDown here and not for PDFs:
  - MarkItDown's 47% overall / 25% PDF success rate comes from PDFs
    (pdfminer.six backend with no layout analysis).
  - For Microsoft Office formats it wraps python-docx, python-pptx,
    openpyxl — all of which are high-quality. Output for .docx/.pptx/
    .xlsx is 80-95% accurate on common documents.
  - Zero ML models, ~10MB install footprint.

File-attachment path (not URL path):
  Office docs are usually attached to Telegram messages, not shared as
  URLs. The listener's attachment handler calls extract_from_bytes().
  URL-based office docs are rarer but we support them via
  extract_from_url() for symmetry with the PDF path.
"""
from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

from .base import ExtractionResult, require_safe_url

log = logging.getLogger("monogram.ingestion.office")


_SUPPORTED_EXTS = (".docx", ".pptx", ".xlsx", ".doc", ".ppt", ".xls")


def is_office_url(url: str) -> bool:
    return url.lower().endswith(_SUPPORTED_EXTS)


async def extract_from_url(url: str) -> ExtractionResult:
    """Download an office doc and extract markdown."""
    try:
        require_safe_url(url)
    except Exception as e:
        return ExtractionResult(
            source_type="office",
            url=url,
            text=f"[Office fetch blocked: {e}]",
            success=False,
            extraction_method="blocked",
            warning=str(e),
        )

    data = await _download(url)
    if not data:
        return ExtractionResult(
            source_type="office",
            url=url,
            text=f"[Office download failed for {url}]",
            success=False,
            extraction_method="download_failed",
        )

    # Determine filename from URL (preserves extension for MarkItDown)
    filename = url.rstrip("/").rsplit("/", 1)[-1].split("?", 1)[0]
    return await extract_from_bytes(data, filename=filename, url=url)


async def extract_from_bytes(
    data: bytes, filename: str, url: str = ""
) -> ExtractionResult:
    """Extract markdown from office-document bytes via MarkItDown."""
    ext = Path(filename).suffix.lower()
    if ext not in _SUPPORTED_EXTS:
        return ExtractionResult(
            source_type="office",
            url=url,
            text=f"[Unsupported office format: {ext}]",
            metadata={"filename": filename},
            success=False,
            extraction_method="unsupported",
            warning=f"ext_{ext}_not_supported",
        )

    text = await _markitdown_extract(data, ext)
    if text is None:
        return ExtractionResult(
            source_type="office",
            url=url,
            text=f"[MarkItDown extraction failed for {filename}]",
            metadata={"filename": filename, "ext": ext},
            success=False,
            extraction_method="markitdown_failed",
            warning="markitdown_error_or_not_installed",
        )

    return ExtractionResult(
        source_type="office",
        url=url,
        text=text,
        metadata={"filename": filename, "ext": ext, "extractor": "markitdown"},
        extraction_method="markitdown",
    )


async def _download(url: str, max_bytes: int = 20 * 1024 * 1024) -> bytes | None:
    """Stream-download with size cap (20MB) and SSRF-safe redirect handling."""
    def _sync() -> bytes | None:
        from .base import safe_stream_bytes
        data = safe_stream_bytes(url, max_bytes=max_bytes, timeout=15.0)
        if data is not None:
            return data

        try:
            from .base import require_safe_url
            require_safe_url(url)
            import urllib.request
            req = urllib.request.Request(url, headers={"User-Agent": "monogram-ingestion/0.8"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read(max_bytes + 1)[:max_bytes]
        except Exception as e:
            log.warning("office: urllib download failed: %s", e)
            return None

    return await asyncio.to_thread(_sync)


async def _markitdown_extract(data: bytes, ext: str) -> str | None:
    """Run MarkItDown on bytes written to a tempfile."""
    def _sync() -> str | None:
        try:
            from markitdown import MarkItDown  # type: ignore
        except ImportError:
            log.debug("markitdown not installed (install monogram[ingestion-office])")
            return None

        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name

        try:
            md = MarkItDown()
            result = md.convert(tmp_path)
            return result.text_content if result else None
        except Exception as e:
            log.warning("markitdown failed: %s", e)
            return None
        finally:
            try:
                Path(tmp_path).unlink()
            except OSError:
                pass

    return await asyncio.to_thread(_sync)
