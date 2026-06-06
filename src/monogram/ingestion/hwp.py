"""HWP/HWPX extractor via rhwp-python — replaces LibreOffice path (no LibreOffice CVE surface)."""
from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

from .base import ExtractionResult, require_safe_url

log = logging.getLogger("monogram.ingestion.hwp")

_MAX_INPUT_BYTES = 20 * 1024 * 1024  # 20 MB


async def extract_from_bytes(
    data: bytes, filename: str = "document.hwp", url: str = ""
) -> ExtractionResult:
    if len(data) > _MAX_INPUT_BYTES:
        return ExtractionResult(
            source_type="hwp",
            url=url,
            text=f"[HWP file too large: {len(data)} bytes > {_MAX_INPUT_BYTES}]",
            success=False,
            extraction_method="size_cap_exceeded",
            warning=f"size_cap_{_MAX_INPUT_BYTES}",
        )

    text = await _rhwp_extract(data, filename)
    if text is None:
        return ExtractionResult(
            source_type="hwp",
            url=url,
            text=f"[HWP extraction failed for {filename}]",
            metadata={"filename": filename},
            success=False,
            extraction_method="rhwp_failed",
            warning="rhwp_error_or_not_installed",
        )

    return ExtractionResult(
        source_type="hwp",
        url=url,
        text=text,
        metadata={"filename": filename, "extractor": "rhwp"},
        extraction_method="rhwp",
        success=bool(text.strip()),
        warning=None if text.strip() else "empty_document",
    )


async def extract_from_url(url: str) -> ExtractionResult:
    try:
        require_safe_url(url)
    except Exception as e:
        return ExtractionResult(
            source_type="hwp",
            url=url,
            text=f"[HWP fetch blocked: {e}]",
            success=False,
            extraction_method="blocked",
            warning=str(e),
        )

    data = await _download(url)
    if not data:
        return ExtractionResult(
            source_type="hwp",
            url=url,
            text=f"[HWP download failed for {url}]",
            success=False,
            extraction_method="download_failed",
        )

    filename = url.rstrip("/").rsplit("/", 1)[-1].split("?", 1)[0] or "document.hwp"
    return await extract_from_bytes(data, filename=filename, url=url)


async def _rhwp_extract(data: bytes, filename: str) -> str | None:
    def _sync() -> str | None:
        import importlib.util
        if importlib.util.find_spec("rhwp") is None:
            log.debug("rhwp-python not installed (install monogram[ingestion-hwp])")
            return None

        suffix = ".hwpx" if filename.lower().endswith(".hwpx") else ".hwp"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name

        try:
            from ._sandbox import run_parser
            return run_parser("hwp", tmp_path, timeout=60)
        finally:
            try:
                Path(tmp_path).unlink()
            except OSError:
                pass

    return await asyncio.to_thread(_sync)


async def _download(url: str, max_bytes: int = _MAX_INPUT_BYTES) -> bytes | None:
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
            log.warning("hwp: urllib download failed: %s", e)
            return None

    return await asyncio.to_thread(_sync)
