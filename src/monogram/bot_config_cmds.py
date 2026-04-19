"""Bot commands for editing vault config at runtime.

Namespace: /config_*
Current surface: /config_llm_* (v0.4)
Future: /config_language, /config_category_add, etc.

All editing commands are immediate for the whitelisted user — the
Telegram bot is already authenticated by `config.telegram_user_id`.
"""
from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from . import github_store
from .config import load_config
from .endpoint_docs import ENDPOINTS, format_endpoint_help
from .models import get_model
from .vault_config import load_vault_config, reload_vault_config

log = logging.getLogger("monogram.bot_config")

router = Router()
_cfg = load_config()


def _user_allowed(msg: Message) -> bool:
    return msg.from_user.id == _cfg.telegram_user_id


def _read_meta_and_body() -> tuple[dict, str]:
    content = github_store.read("config.md") or ""
    if not content:
        return {}, ""
    meta, body = github_store.parse_metadata(content)
    return (meta or {}), body


def _write_meta_and_body(meta: dict, body: str, commit_msg: str) -> bool:
    new_content = github_store.serialize_with_metadata(meta, body)
    ok = github_store.write("config.md", new_content, commit_msg)
    if ok:
        reload_vault_config()
    return ok


def _parse_arg(text: str | None) -> str | None:
    if not text:
        return None
    parts = text.strip().split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else None


async def _update_field(
    msg: Message, field_path: list[str], value: str
) -> None:
    """Update dotted path in config.md frontmatter and reload cache."""
    if not _user_allowed(msg):
        return
    meta, body = _read_meta_and_body()
    node = meta
    for key in field_path[:-1]:
        if key not in node or not isinstance(node[key], dict):
            node[key] = {}
        node = node[key]
    node[field_path[-1]] = value
    dotted = ".".join(field_path)
    ok = _write_meta_and_body(
        meta, body, f"monogram: config.md — {dotted} → {value[:40]}"
    )
    if ok:
        await msg.answer(
            f"✓ `{dotted}` = `{value}`", parse_mode="Markdown"
        )
    else:
        await msg.answer("✗ Write to config.md failed")


# ── show / inspect ──


@router.message(Command("config_llm"))
async def show_llm(msg: Message):
    if not _user_allowed(msg):
        return
    vc = load_vault_config()
    lines = [
        "*LLM configuration*",
        f"Provider: `{vc.llm_provider or '(unset — legacy mode)'}`",
        f"Mode:     `{vc.llm_mode}`",
        "Models:",
    ]
    if vc.llm_models:
        for k, v in vc.llm_models.items():
            lines.append(f"  {k}: `{v}`")
    else:
        lines.append("  (none)")
    lines.append(f"Base URL: `{vc.llm_base_url or '(empty)'}`")
    await msg.answer("\n".join(lines), parse_mode="Markdown")


@router.message(Command("config_llm_help"))
async def llm_help(msg: Message):
    if not _user_allowed(msg):
        return
    value = _parse_arg(msg.text)
    if not value:
        await msg.answer(
            f"Usage: /config_llm_help <endpoint>\n"
            f"Endpoints: {', '.join(ENDPOINTS.keys())}"
        )
        return
    await msg.answer(format_endpoint_help(value))


# ── setters ──


@router.message(Command("config_llm_provider"))
async def set_provider(msg: Message):
    if not _user_allowed(msg):
        return
    value = _parse_arg(msg.text)
    if not value:
        await msg.answer(
            f"Usage: /config_llm_provider <name>\n"
            f"Supported: {', '.join(ENDPOINTS.keys())}"
        )
        return
    if value not in ENDPOINTS:
        await msg.answer(
            f"Unknown provider `{value}`.\n"
            f"Supported: {', '.join(ENDPOINTS.keys())}",
            parse_mode="Markdown",
        )
        return
    await _update_field(msg, ["llm_provider"], value)


@router.message(Command("config_llm_mode"))
async def set_mode(msg: Message):
    if not _user_allowed(msg):
        return
    value = _parse_arg(msg.text)
    if value not in ("tiered", "single"):
        await msg.answer("Usage: /config_llm_mode tiered|single")
        return
    await _update_field(msg, ["llm_mode"], value)


@router.message(Command("config_llm_model_low"))
async def set_low(msg: Message):
    if not _user_allowed(msg):
        return
    value = _parse_arg(msg.text)
    if not value:
        await msg.answer("Usage: /config_llm_model_low <model-string>")
        return
    await _update_field(msg, ["llm_models", "low"], value)


@router.message(Command("config_llm_model_mid"))
async def set_mid(msg: Message):
    if not _user_allowed(msg):
        return
    value = _parse_arg(msg.text)
    if not value:
        await msg.answer("Usage: /config_llm_model_mid <model-string>")
        return
    await _update_field(msg, ["llm_models", "mid"], value)


@router.message(Command("config_llm_model_high"))
async def set_high(msg: Message):
    if not _user_allowed(msg):
        return
    value = _parse_arg(msg.text)
    if not value:
        await msg.answer("Usage: /config_llm_model_high <model-string>")
        return
    await _update_field(msg, ["llm_models", "high"], value)


@router.message(Command("config_llm_model_single"))
async def set_single(msg: Message):
    if not _user_allowed(msg):
        return
    value = _parse_arg(msg.text)
    if not value:
        await msg.answer("Usage: /config_llm_model_single <model-string>")
        return
    await _update_field(msg, ["llm_models", "single"], value)


@router.message(Command("config_llm_base_url"))
async def set_base_url(msg: Message):
    if not _user_allowed(msg):
        return
    # Explicitly allow empty string — clears the field
    parts = (msg.text or "").strip().split(maxsplit=1)
    value = parts[1].strip() if len(parts) > 1 else ""
    await _update_field(msg, ["llm_base_url"], value)


# ── test + reload ──


@router.message(Command("config_llm_test"))
async def test_llm(msg: Message):
    if not _user_allowed(msg):
        return
    from .llm import complete

    vc = load_vault_config()
    tiers = ("single",) if vc.llm_mode == "single" else ("low", "mid", "high")
    lines = ["*LLM test results*", ""]
    for tier in tiers:
        try:
            model = get_model(tier)
            out = await complete("Say OK", model=model, max_output_tokens=10)
            lines.append(f"✓ {tier} (`{model}`): {out.strip()[:60]}")
        except Exception as e:
            lines.append(f"✗ {tier}: `{type(e).__name__}`: {str(e)[:80]}")
    await msg.answer("\n".join(lines), parse_mode="Markdown")


@router.message(Command("config_reload"))
async def reload_config(msg: Message):
    if not _user_allowed(msg):
        return
    reload_vault_config()
    await msg.answer("✓ Vault config reloaded.")
