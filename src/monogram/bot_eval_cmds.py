"""Telegram bot commands for eval kill-switch and few-shot control.

New in v0.7. Parallels bot_config_cmds.py (LLM config) and bot_webui_cmds.py
(web UI controls). Same authorization pattern: commands only respond to the
configured TELEGRAM_USER_ID.

Commands:
    /eval_status              → Show effective state across 3 layers
    /eval_enable              → Write eval_enabled: true to mono/config.md
    /eval_disable             → Write eval_enabled: false
    /eval_disable_few_shot    → Turn off Track B classifier few-shot
    /eval_enable_few_shot     → Turn on Track B classifier few-shot
                                (subject to P7 2-week rule — do not flip
                                casually before reading docs/eval-plan.md §10)
"""
from __future__ import annotations

import logging
import os

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from .config import load_config
from .vault_config import load_vault_config, reload_vault_config, set_config_field

log = logging.getLogger("monogram.bot_eval_cmds")

router = Router(name="eval_cmds")


def _is_owner(message: Message) -> bool:
    """Gate commands to the configured user only."""
    try:
        cfg = load_config()
    except Exception:
        return False
    return str(message.from_user.id) == str(cfg.telegram_user_id)


def _env_disabled() -> bool:
    return os.environ.get("MONOGRAM_EVAL_DISABLED") == "1"


@router.message(Command(commands=["eval_status"]))
async def eval_status(message: Message) -> None:
    if not _is_owner(message):
        return
    try:
        cfg = reload_vault_config()
    except Exception as e:
        await message.answer(f"eval_status: read failed: {e}")
        return

    env_off = _env_disabled()
    cfg_on = cfg.eval_enabled
    few_shot_on = cfg.classifier_few_shot_enabled

    # effective state: env wins over config
    effective = "DISABLED" if env_off or not cfg_on else "ENABLED"

    lines = [
        "📊 Eval status",
        "",
        f"  Layer 2 (env MONOGRAM_EVAL_DISABLED): {'1 (off)' if env_off else 'unset (pass)'}",
        f"  Layer 3 (config eval_enabled):        {cfg_on}",
        f"  Layer 4 (classifier_few_shot):        {few_shot_on}",
        "",
        f"  Effective: eval is {effective}",
        "",
        "Commands:",
        "  /eval_enable           — turn eval system on (layer 3)",
        "  /eval_disable          — turn eval system off (layer 3)",
        "  /eval_enable_few_shot  — turn on classifier few-shot (P7 rule applies)",
        "  /eval_disable_few_shot — turn off classifier few-shot",
    ]
    await message.answer("\n".join(lines))


@router.message(Command(commands=["eval_enable"]))
async def eval_enable(message: Message) -> None:
    if not _is_owner(message):
        return
    ok = set_config_field("eval_enabled", True)
    if ok:
        await message.answer(
            "✓ eval_enabled: true in mono/config.md\n"
            "(env MONOGRAM_EVAL_DISABLED still overrides if set)"
        )
    else:
        await message.answer("✗ Write to config.md failed. Check logs.")


@router.message(Command(commands=["eval_disable"]))
async def eval_disable(message: Message) -> None:
    if not _is_owner(message):
        return
    ok = set_config_field("eval_enabled", False)
    if ok:
        await message.answer(
            "✓ eval_enabled: false in mono/config.md\n"
            "Scheduled harvest + bot /eval_* commands are now disabled.\n"
            "`monogram eval run` CLI still works if you need a manual run."
        )
    else:
        await message.answer("✗ Write to config.md failed. Check logs.")


@router.message(Command(commands=["eval_disable_few_shot"]))
async def eval_disable_few_shot(message: Message) -> None:
    if not _is_owner(message):
        return
    ok = set_config_field("classifier_few_shot_enabled", False)
    if ok:
        await message.answer(
            "✓ classifier_few_shot_enabled: false\n"
            "Classifier returns to zero-shot. Eval harness still runs if "
            "eval_enabled: true."
        )
    else:
        await message.answer("✗ Write to config.md failed.")


@router.message(Command(commands=["eval_enable_few_shot"]))
async def eval_enable_few_shot(message: Message) -> None:
    if not _is_owner(message):
        return
    ok = set_config_field("classifier_few_shot_enabled", True)
    if ok:
        await message.answer(
            "✓ classifier_few_shot_enabled: true\n\n"
            "⚠️  Per eval plan §10: measure for 2 weeks against the "
            "pre-committed failure rule. If any of:\n"
            "  • accuracy drops >1pp vs baseline\n"
            "  • any credential fixture fails\n"
            "  • any injection fixture fails\n"
            "  • escalation rate shifts >5pp either direction\n"
            "→ run /eval_disable_few_shot immediately and write post-mortem."
        )
    else:
        await message.answer("✗ Write to config.md failed.")
