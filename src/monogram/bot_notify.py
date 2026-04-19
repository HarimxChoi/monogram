"""Push-to-Telegram helper from non-bot code (MCP tool handlers, etc.).

Uses aiogram one-shot Bot inside `async with` so aiohttp session closes
cleanly. Caller must be in an asyncio context.
"""
from __future__ import annotations

import logging

from .config import load_config

log = logging.getLogger("monogram.bot_notify")


async def push_to_telegram(text: str) -> bool:
    """Push a message to the configured user. Returns True on success."""
    try:
        from aiogram.client.bot import Bot
    except Exception as e:
        log.warning("bot_notify: aiogram import failed: %s", e)
        return False

    cfg = load_config()
    if not cfg.telegram_bot_token or not cfg.telegram_user_id:
        log.info("bot_notify: no Telegram bot configured; skipping push")
        return False

    try:
        async with Bot(token=cfg.telegram_bot_token) as b:
            await b.send_message(cfg.telegram_user_id, text)
        return True
    except Exception as e:
        log.warning("bot_notify: push failed: %s", e)
        return False
