"""v0.6 — Bot commands for web UI delivery.

Namespace: /webui and /config_webui_*

Extends the /config_* Router pattern established in v0.4 bot_config_cmds.py.
Only the whitelisted telegram_user_id can use these commands.
"""
from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from . import github_store
from .config import load_config
from .vault_config import load_vault_config, reload_vault_config

log = logging.getLogger("monogram.bot_webui")

router = Router()
_cfg = load_config()


def _user_allowed(msg: Message) -> bool:
    return msg.from_user.id == _cfg.telegram_user_id


def _parse_arg(text: str | None) -> str | None:
    if not text:
        return None
    parts = text.strip().split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else None


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


async def _update_field(msg: Message, field_path: list[str], value) -> None:
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
        meta, body, f"monogram: config.md — {dotted} → {str(value)[:40]}"
    )
    if ok:
        await msg.answer(
            f"✓ `{dotted}` = `{value}`", parse_mode="Markdown"
        )
    else:
        await msg.answer("✗ Write to config.md failed")


# ── /webui — regenerate + reply with URL ──


@router.message(Command("webui"))
async def cmd_webui(msg: Message):
    if not _user_allowed(msg):
        return
    from .encryption_layer import wrap
    from .webgen import render_bundle
    from .webui import WebUIDisabledError, get_active_backend

    vc = load_vault_config()
    if vc.webui_mode == "mcp-only":
        await msg.answer(
            "Web UI disabled (mcp-only mode). "
            "Enable with `/config_webui_mode gcs` or `/config_webui_mode self-host`.",
            parse_mode="Markdown",
        )
        return

    password = _cfg.monogram_webui_password
    if not password:
        await msg.answer(
            "MONOGRAM_WEBUI_PASSWORD not set in .env. "
            "Run `monogram webui rotate-password` to set it."
        )
        return

    await msg.answer("🔄 Regenerating dashboard…")
    try:
        plaintext = await render_bundle()
        encrypted = wrap(plaintext, password)
        backend = get_active_backend()
        url = await backend.publish(encrypted)
        await msg.answer(
            f"✓ Dashboard ready: {url}",
            disable_web_page_preview=True,
        )
    except WebUIDisabledError as e:
        await msg.answer(str(e))
    except Exception as e:
        await msg.answer(f"✗ Web UI publish failed: `{type(e).__name__}`: {e}",
                         parse_mode="Markdown")


@router.message(Command("config_webui"))
async def show_webui(msg: Message):
    if not _user_allowed(msg):
        return
    vc = load_vault_config()
    lines = [
        "*Web UI configuration*",
        f"Mode:     `{vc.webui_mode}`",
    ]
    if vc.webui_mode == "gcs":
        bucket = (vc.webui_gcs or {}).get("bucket", "(unset)")
        slug = (vc.webui_gcs or {}).get("path_slug", "main")
        lines.append(f"Bucket:   `{bucket}`")
        lines.append(f"Path slug: `{slug}`")
    elif vc.webui_mode == "self-host":
        port = (vc.webui_self_host or {}).get("port", 8765)
        lines.append(f"Port:     `{port}`")
    pw_set = "set" if _cfg.monogram_webui_password else "**NOT SET**"
    lines.append(f"Password: {pw_set}")
    await msg.answer("\n".join(lines), parse_mode="Markdown")


@router.message(Command("config_webui_mode"))
async def set_mode(msg: Message):
    if not _user_allowed(msg):
        return
    value = _parse_arg(msg.text)
    if value not in ("gcs", "self-host", "mcp-only"):
        await msg.answer(
            "Usage: /config_webui_mode gcs|self-host|mcp-only"
        )
        return
    await _update_field(msg, ["webui_mode"], value)


@router.message(Command("config_webui_gcs_bucket"))
async def set_gcs_bucket(msg: Message):
    if not _user_allowed(msg):
        return
    value = _parse_arg(msg.text)
    if not value:
        await msg.answer("Usage: /config_webui_gcs_bucket <name>")
        return
    await _update_field(msg, ["webui_gcs", "bucket"], value)


@router.message(Command("config_webui_self_host_port"))
async def set_port(msg: Message):
    if not _user_allowed(msg):
        return
    value = _parse_arg(msg.text)
    try:
        port = int(value or "")
    except ValueError:
        await msg.answer("Usage: /config_webui_self_host_port <1024-65535>")
        return
    if port < 1024 or port > 65535:
        await msg.answer("Port must be in range 1024-65535.")
        return
    await _update_field(msg, ["webui_self_host", "port"], port)


@router.message(Command("config_webui_open"))
async def open_url(msg: Message):
    """Return current URL without regenerating."""
    if not _user_allowed(msg):
        return
    from .webui import get_active_backend

    try:
        backend = get_active_backend()
        url = await backend.current_url()
        if url:
            await msg.answer(f"→ {url}", disable_web_page_preview=True)
        else:
            await msg.answer(
                "No current URL. Run `/webui` to regenerate.",
                parse_mode="Markdown",
            )
    except Exception as e:
        await msg.answer(f"✗ {type(e).__name__}: {e}")
