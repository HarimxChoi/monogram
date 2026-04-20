"""C3 — Telethon Saved Messages watcher.

Watches outgoing messages to self (Saved Messages) and runs each through
the pipeline. Commits ALL staged writes via github_store.write_multi().
Photos are OCR'd via Gemini vision and combined with any caption text.
"""
from __future__ import annotations

from telethon import TelegramClient, events

from . import github_store
from .config import load_config
from .llm import complete_vision
from .pipeline import run_pipeline

config = load_config()

VISION_OCR_PROMPT = (
    "Transcribe and describe this image. If it contains text "
    "(handwriting, screenshot, document, slide), transcribe verbatim. "
    "If it's a diagram or photo, describe what's shown concisely. "
    "Output plain text only — no markdown, no preamble."
)


async def handle_drop(text: str) -> str:
    """Run a text drop through the pipeline and commit all writes.

    v0.8: if ingestion is enabled and the drop contains URLs, extract
    content from them BEFORE the pipeline runs. Enriched text goes to
    the pipeline; raw extraction copies land in raw/ tier for audit.
    """
    enriched_text = await _enrich_with_ingestion(text)
    result = await run_pipeline(enriched_text)

    if result.file_change is None:
        return f"blocked: {result.blocked_reason or 'unknown'}"

    fc = result.file_change
    ok = github_store.write_atomic(fc.writes, fc.commit_message)
    if not ok:
        return (
            f"write failed: atomic commit of {len(fc.writes)} paths did not "
            "land (concurrent-writer race or API error). Re-drop to retry."
        )

    # Credentials: minimal reply, never echo slug or content
    if fc.primary_path.startswith("life/credentials/"):
        return f"credential captured (confidence: {fc.confidence})"

    esc = " +escalated" if result.escalated else ""
    paths = len(fc.writes)
    return f"`{fc.primary_path}` committed ({fc.confidence}{esc}, {paths} paths)"


async def run_listener(send_reply_fn):
    """Start the Telethon client, watch Saved Messages.

    send_reply_fn: async callable(user_id: int, text: str) — e.g. bot.send_reply.
    """
    client = TelegramClient(
        "monogram_session",
        config.telegram_api_id,
        config.telegram_api_hash,
    )
    await client.start()
    me = await client.get_me()
    print(f"Listener started for {me.username or me.id}")

    @client.on(events.NewMessage(outgoing=True))
    async def saved_handler(event):
        if event.peer_id.user_id != me.id:
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
                # No vision-capable model configured. Don't crash the pipeline —
                # fall through to caption-only with an explicit placeholder.
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

        if not text:
            return

        reply = await handle_drop(text)
        await send_reply_fn(config.telegram_user_id, reply)

    await client.run_until_disconnected()


async def _enrich_with_ingestion(text: str) -> str:
    """If ingestion is enabled, extract content from URLs in the drop.

    Returns the enriched text (original + extracted snippets). On any
    failure, returns the original text unchanged — ingestion is best-
    effort and must never block pipeline processing.
    """
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

    # Write each successful extraction to raw/ tier (audit-only; not
    # read back by the pipeline). Best-effort: raw/ write failure
    # doesn't revert the enrichment.
    for result in results:
        if result.success and result.text:
            try:
                write_raw(result)
            except Exception as e:
                log.warning("raw_tier.write_raw failed for %s: %s", result.url, e)

    return enriched_text
