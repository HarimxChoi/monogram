"""Office extractor via MarkItDown — high accuracy for docx/pptx/xlsx, not used for PDFs (poor PDF backend)."""
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

    filename = url.rstrip("/").rsplit("/", 1)[-1].split("?", 1)[0]
    return await extract_from_bytes(data, filename=filename, url=url)


async def extract_from_bytes(
    data: bytes, filename: str, url: str = ""
) -> ExtractionResult:
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
                return resp.read(max_bytes + 1)[:max_bytes]
        except Exception as e:
            log.warning("office: urllib download failed: %s", e)
            return None

    return await asyncio.to_thread(_sync)


async def _markitdown_extract(data: bytes, ext: str) -> str | None:
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
