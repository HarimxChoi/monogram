"""File-attachment dispatcher used by the Telethon listener and the
aiogram bot.

Both entry points receive the same kinds of file attachments (PDF, HWP,
Office, text, etc.); this module owns the MIME / filename → extractor
routing so the two call sites stay in lock-step.

Contract:
  - Pure async function of (bytes, mime, filename) → ExtractionResult.
  - Never raises. Any failure (size cap, missing extractor, decoder
    crash) becomes ExtractionResult(success=False, warning=...).
  - Does NOT handle images — vision requires a configured vision model
    probe and belongs in the entry points themselves.
"""
from __future__ import annotations

import logging
from pathlib import Path

from .base import ExtractionResult

log = logging.getLogger("monogram.ingestion.attachment")


_PDF_MIMES = frozenset({"application/pdf", "application/x-pdf"})
_HWP_MIMES = frozenset({
    "application/vnd.hancom.hwp",
    "application/x-hwp",
    "application/haansofthwp",
    "application/vnd.hancom.hwpx",
})
_OFFICE_MIMES = frozenset({
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
})
_HWP_EXTS = (".hwp", ".hwpx")
_OFFICE_EXTS = (".docx", ".xlsx", ".pptx", ".doc", ".xls", ".ppt")
_TEXT_EXTS = (
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json",
    ".yaml", ".yml", ".log", ".rst", ".ini", ".toml", ".xml",
)

# Sanity cap on plain-text decode; PDFs/HWPs have their own caps
# inside the dedicated extractors.
_MAX_TEXT_BYTES = 2 * 1024 * 1024  # 2 MB


def classify(mime_type: str | None, filename: str | None) -> str:
    """One of: 'pdf' | 'hwp' | 'office' | 'text' | 'image' | 'unsupported'.

    'image' is returned for completeness; entry points are expected to
    have already routed images to vision before calling this module.
    """
    mime = (mime_type or "").lower().strip()
    ext = Path((filename or "").lower().strip()).suffix

    if mime.startswith("image/"):
        return "image"
    if mime in _PDF_MIMES or ext == ".pdf":
        return "pdf"
    if mime in _HWP_MIMES or ext in _HWP_EXTS:
        return "hwp"
    if mime in _OFFICE_MIMES or ext in _OFFICE_EXTS:
        return "office"
    if mime.startswith("text/") or ext in _TEXT_EXTS:
        return "text"
    return "unsupported"


async def extract_attachment(
    data: bytes,
    mime_type: str | None = None,
    filename: str | None = None,
) -> ExtractionResult:
    """Route bytes to the right extractor. Never raises.

    The returned ExtractionResult can be fed into the same
    `to_pipeline_snippet()` path used by URL enrichment, so the caller
    stays agnostic to how the text arrived.
    """
    fname = filename or "attachment"
    kind = classify(mime_type, fname)

    try:
        if kind == "pdf":
            from . import pdf
            return await pdf.extract_from_bytes(data, url="", filename=fname)

        if kind == "hwp":
            from . import hwp
            return await hwp.extract_from_bytes(data, filename=fname)

        if kind == "office":
            from . import office
            return await office.extract_from_bytes(data, filename=fname, url="")

        if kind == "text":
            if len(data) > _MAX_TEXT_BYTES:
                return ExtractionResult(
                    source_type="text",
                    url="",
                    text=f"[text file too large: {len(data)} bytes > {_MAX_TEXT_BYTES}]",
                    metadata={"filename": fname},
                    success=False,
                    extraction_method="size_cap_exceeded",
                    warning=f"size_cap_{_MAX_TEXT_BYTES}",
                )
            try:
                text = data.decode("utf-8")
                method = "utf8_decode"
            except UnicodeDecodeError:
                text = data.decode("utf-8", errors="replace")
                method = "utf8_decode_replace"
            return ExtractionResult(
                source_type="text",
                url="",
                text=text,
                metadata={"filename": fname, "mime": mime_type or ""},
                extraction_method=method,
            )

        if kind == "image":
            # Callers must branch to vision before reaching this module.
            # If they didn't, be explicit instead of silently succeeding.
            return ExtractionResult(
                source_type="image",
                url="",
                text=f"[image attachment {fname} — vision branch not taken]",
                metadata={"filename": fname, "mime": mime_type or ""},
                success=False,
                extraction_method="image_not_routed",
                warning="image_must_use_vision_branch",
            )

        # unsupported — audio, video, sticker, voice, archive, …
        return ExtractionResult(
            source_type="unknown",
            url="",
            text=f"[unsupported attachment: {fname} (mime={mime_type!r})]",
            metadata={"filename": fname, "mime": mime_type or ""},
            success=False,
            extraction_method="unsupported_mime",
            warning=f"unsupported_{mime_type or 'unknown'}",
        )
    except Exception as e:
        log.exception("extract_attachment dispatch failed for %s", fname)
        return ExtractionResult(
            source_type="unknown",
            url="",
            text=f"[extractor crashed for {fname}: {type(e).__name__}]",
            metadata={"filename": fname, "mime": mime_type or ""},
            success=False,
            extraction_method="dispatch_error",
            warning=f"dispatch_error: {type(e).__name__}",
        )


def build_drop_text(
    caption: str,
    result: ExtractionResult,
    max_chars: int = 2000,
) -> str:
    """Compose pipeline-ready drop text from (caption + extracted file).

    Kept here (rather than in listener/bot) so the two entry points
    format identically. Truncates the extracted snippet to `max_chars`
    to keep pipeline token usage bounded — the full text is already
    preserved in the extraction result for raw/ tier capture.
    """
    fname = result.metadata.get("filename", "attachment")
    body = (result.text or "").strip()
    if len(body) > max_chars:
        body = body[:max_chars].rstrip() + "…\n[truncated — full text in raw/]"

    header = f"[{result.source_type}:{fname}]"
    attachment_block = f"{header}\n{body}" if body else header

    if caption.strip():
        return f"{caption.strip()}\n\n{attachment_block}"
    return attachment_block
