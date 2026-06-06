"""Push-to-Telegram from non-bot code; one-shot Bot ensures aiohttp session closes cleanly."""
from __future__ import annotations

import logging

from .config import load_config

log = logging.getLogger("monogram.bot_notify")


async def push_to_telegram(text: str) -> bool:
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
