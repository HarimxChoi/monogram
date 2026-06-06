"""PDF extractor — native text layer via PyMuPDF4LLM; no OCR (scanned PDFs won't extract)."""
from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

from .base import ExtractionResult, require_safe_url

log = logging.getLogger("monogram.ingestion.pdf")


async def extract_from_url(url: str) -> ExtractionResult:
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
    text = await _pymupdf4llm_extract(pdf_bytes)
    ok = bool(text) and _quality_ok(text)
    return ExtractionResult(
        source_type="pdf",
        url=url,
        text=text or f"[PDF extraction failed for {filename or url}]",
        metadata={"filename": filename, "extractor": "pymupdf4llm"},
        extraction_method="pymupdf4llm" if ok else "pymupdf4llm_low_quality",
        success=ok,
        warning=None if ok else "no_text_layer (scanned PDF? native extraction has no OCR)",
    )


async def _download_pdf(url: str, max_bytes: int = 20 * 1024 * 1024) -> bytes | None:
    """Validates every redirect hop (SSRF) — attacker's URL can't 302 to loopback."""
    def _sync() -> bytes | None:
        from .base import safe_stream_bytes
        data = safe_stream_bytes(url, max_bytes=max_bytes, timeout=15.0)
        if data is not None:
            return data

        try:
            from .base import require_safe_url
            require_safe_url(url)
            import urllib.request

            class _ValidatingRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    require_safe_url(newurl)
                    return super().redirect_request(req, fp, code, msg, headers, newurl)

            opener = urllib.request.build_opener(_ValidatingRedirect())
            req = urllib.request.Request(url, headers={"User-Agent": "monogram-ingestion/0.8"})
            with opener.open(req, timeout=15) as resp:
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
    def _sync() -> str | None:
        import importlib.util
        if importlib.util.find_spec("pymupdf4llm") is None:
            log.debug("pymupdf4llm not installed (install monogram[ingestion-pdf])")
            return None

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = tmp.name

        try:
            from ._sandbox import run_parser
            return run_parser("pdf", tmp_path, timeout=60)
        finally:
            try:
                Path(tmp_path).unlink()
            except OSError:
                pass

    return await asyncio.to_thread(_sync)


def _quality_ok(text: str) -> bool:
    if not text or len(text) < 100:
        return False
    printable = sum(1 for c in text if c.isprintable() or c in "\n\t")
    if printable / len(text) < 0.85:
        return False
    return True
