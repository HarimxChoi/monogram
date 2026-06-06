"""Telethon Saved Messages watcher: routes drops through the pipeline."""
from __future__ import annotations

import logging

from telethon import TelegramClient, events

from . import github_store
from .config import load_config
from .llm import complete_vision
from .pipeline import run_pipeline

config = load_config()
log = logging.getLogger("monogram.listener")

VISION_OCR_PROMPT = (
    "Transcribe and describe this image. If it contains text "
    "(handwriting, screenshot, document, slide), transcribe verbatim. "
    "If it's a diagram or photo, describe what's shown concisely. "
    "Output plain text only — no markdown, no preamble."
)


async def handle_drop(text: str) -> str:
    enriched_text = await _enrich_with_ingestion(text)
    result = await run_pipeline(enriched_text)

    if result.file_change is None:
        return f"blocked: {result.blocked_reason or 'unknown'}"

    fc = result.file_change
    ok = github_store.write_atomic(fc.writes, fc.commit_message)
    if not ok:
        return (
            "write failed: atomic commit did not land "
            "(concurrent-writer race or API error). Re-drop to retry."
        )

    # Never echo slug or content for credential paths.
    if fc.primary_path.startswith("life/credentials/"):
        return f"credential captured (confidence: {fc.confidence})"

    esc = " +escalated" if result.escalated else ""
    paths = len(fc.writes)
    return f"`{fc.primary_path}` committed ({fc.confidence}{esc}, {paths} paths)"


_DOC_EXTS = {".pdf", ".hwp", ".hwpx", ".docx", ".pptx", ".xlsx", ".doc", ".ppt", ".xls"}


async def _extract_document_attachment(event) -> str | None:
    """Best-effort: returns None on any failure so a drop is never blocked."""
    try:
        f = event.file
        ext = (f.ext or "").lower() if f else ""
        name = (f.name if f and f.name else "") or f"document{ext}"
    except Exception:
        return None

    if ext not in _DOC_EXTS:
        return None

    try:
        data = await event.download_media(file=bytes)
    except Exception as e:
        log.warning("document download failed (%s): %s", name, e)
        return None
    if not data:
        return None

    try:
        if ext == ".pdf":
            from .ingestion import pdf
            result = await pdf.extract_from_bytes(data, filename=name)
        elif ext in (".hwp", ".hwpx"):
            from .ingestion import hwp
            result = await hwp.extract_from_bytes(data, filename=name)
        else:
            from .ingestion import office
            result = await office.extract_from_bytes(data, filename=name)
    except Exception as e:
        log.warning("document extraction failed (%s): %s", name, e)
        return None

    if result and result.text and result.success:
        return result.text
    log.info("document extraction unsuccessful for %s", name)
    return None


async def run_listener(send_reply_fn):
    """send_reply_fn: async callable(user_id, text)."""
    client = TelegramClient(
        "monogram_session",
        config.telegram_api_id,
        config.telegram_api_hash,
    )
    await client.start()
    me = await client.get_me()
    log.info("Listener started for %s", me.username or me.id)

    @client.on(events.NewMessage(outgoing=True))
    async def saved_handler(event):
        if event.peer_id.user_id != me.id:
            return

        # Dedup claim prevents a co-running instance from double-processing the same message.
        msg_id = getattr(event, "id", None)
        dedup_key = f"saved-{msg_id}" if msg_id is not None else None
        if dedup_key is not None:
            from .dedup import claim
            if not claim(dedup_key):
                return

        caption = event.raw_text or ""
        text = caption

        if event.photo or (event.document and event.document.mime_type and
                           event.document.mime_type.startswith("image/")):
            from .models import get_vision_model

            vision_model = get_vision_model()
            try:
                image_bytes = await event.download_media(file=bytes)
            except Exception as e:
                await send_reply_fn(config.telegram_user_id, f"image download failed: {e}")
                return

            if not vision_model:
                # Fall through to caption-only rather than crashing the pipeline.
                placeholder = "[image — vision not configured; set llm_models.vision in mono/config.md]"
                text = f"{caption}\n\n{placeholder}".strip() if caption else placeholder
            else:
                try:
                    description = await complete_vision(
                        image_bytes, VISION_OCR_PROMPT, model=vision_model
                    )
                    text = f"{caption}\n\n[image]\n{description}".strip()
                except Exception as e:
                    await send_reply_fn(config.telegram_user_id, f"vision error: {e}")
                    return

        elif event.document:
            doc_text = await _extract_document_attachment(event)
            if doc_text:
                text = f"{caption}\n\n{doc_text}".strip() if caption else doc_text

        if not text:
            return

        reply = await handle_drop(text)
        if dedup_key is not None and "write failed" in reply:
            from .dedup import release
            release(dedup_key)  # commit didn't land — allow a re-drop
        await send_reply_fn(config.telegram_user_id, reply)

    await client.run_until_disconnected()


async def _enrich_with_ingestion(text: str) -> str:
    """Best-effort URL extraction; returns original text unchanged on any failure."""
    import logging

    log = logging.getLogger("monogram.listener.ingestion")

    try:
        from .vault_config import load_vault_config
        cfg = load_vault_config()
    except Exception:
        return text

    if not getattr(cfg, "ingestion_enabled", True):
        return text

    try:
        from .ingestion import enrich_drop
        from .ingestion.raw_tier import write_raw
    except ImportError as e:
        log.debug("ingestion module unavailable: %s", e)
        return text

    try:
        enriched_text, results = await enrich_drop(text, config=cfg)
    except Exception as e:
        log.warning("ingestion.enrich_drop failed: %s", e)
        return text

    # raw/ is audit-only; raw/ write failure must not revert enrichment.
    for result in results:
        if result.success and result.text:
            try:
                write_raw(result)
            except Exception as e:
                log.warning("raw_tier.write_raw failed for %s: %s", result.url, e)

    return enriched_text
