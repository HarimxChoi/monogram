"""C4 — aiogram bot handlers.

/start, /status, and free-form messages all route through the pipeline.
v0.4: /config_llm_* commands registered via bot_config_cmds router.
      /approve_<token> and /deny_<token> for MCP-gated writes.
"""
from __future__ import annotations

import logging
import re
from functools import cache

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message

from . import github_store
from .config import load_config
from .listener import handle_drop

log = logging.getLogger("monogram.bot")


@cache
def _cfg():
    """Lazy app-config accessor — defers .env loading until first use."""
    return load_config()


@cache
def bot() -> Bot:
    """Lazy Bot instance — constructed on first call so importing
    monogram.bot does not require a valid telegram_bot_token at import time."""
    return Bot(token=_cfg().telegram_bot_token)


dp = Dispatcher()

# v0.4: register /config_llm_* command router
from .bot_config_cmds import router as _config_router  # noqa: E402
dp.include_router(_config_router)

# v0.6: register /webui and /config_webui_* command router
from .bot_webui_cmds import router as _webui_router  # noqa: E402
dp.include_router(_webui_router)

# v0.7: register /eval_* command router (kill-switch + few-shot)
from .bot_eval_cmds import router as _eval_router  # noqa: E402
dp.include_router(_eval_router)

# v0.8: register /stats command router (pipeline health from phone)
from .bot_stats_cmd import router as _stats_router  # noqa: E402
dp.include_router(_stats_router)


@dp.message(Command("start"))
async def cmd_start(msg: Message):
    if msg.from_user.id != _cfg().telegram_user_id:
        return
    await msg.answer(
        "Monogram online.\n\n"
        "Drop anything in *Saved Messages* — links, thoughts, voice notes.\n"
        "Talk to me here for queries.",
        parse_mode="Markdown",
    )


@dp.message(Command("status"))
async def cmd_status(msg: Message):
    if msg.from_user.id != _cfg().telegram_user_id:
        return
    content = github_store.read("README.md") or "(scheduler README empty)"
    await msg.answer(content[:4000], parse_mode="Markdown")


@dp.message(Command("done"))
async def cmd_done(msg: Message):
    """Mark a project done: `/done paper-a` → moves to scheduler/archive/."""
    if msg.from_user.id != _cfg().telegram_user_id:
        return
    slug = _extract_slug(msg.text)
    if not slug:
        await msg.answer("Usage: `/done <project-slug>`", parse_mode="Markdown")
        return
    moved = _move_project(slug, to_archive=True)
    await msg.answer(moved, parse_mode="Markdown")


@dp.message(Command("revive"))
async def cmd_revive(msg: Message):
    """Reverse /done: move project back from archive to projects/."""
    if msg.from_user.id != _cfg().telegram_user_id:
        return
    slug = _extract_slug(msg.text)
    if not slug:
        await msg.answer("Usage: `/revive <project-slug>`", parse_mode="Markdown")
        return
    moved = _move_project(slug, to_archive=False)
    await msg.answer(moved, parse_mode="Markdown")


# v0.5.1: tokens are now secrets.token_urlsafe(16) → URL-safe base64
# alphabet [A-Za-z0-9_-], length ~22. Old v0.4 tests used 8-char hex —
# the regex must accept both for backward compat with any stale tokens.
_APPROVE_RE = re.compile(r"^/approve_([A-Za-z0-9_-]{8,64})(?:\s|$)")
_DENY_RE = re.compile(r"^/deny_([A-Za-z0-9_-]{8,64})(?:\s|$)")


async def _execute_pending(entry, msg: Message) -> None:
    """Dispatch pending entry by kind — writes to the vault."""
    if entry.kind == "set_llm_config":
        from .bot_config_cmds import (
            _read_meta_and_body,
            _write_meta_and_body,
        )
        meta, body = _read_meta_and_body()
        for field, value in entry.payload.items():
            meta[field] = value
        ok = _write_meta_and_body(
            meta, body, "monogram: config.md — LLM config via MCP"
        )
        await msg.answer(
            "✓ LLM config updated." if ok else "✗ config.md write failed."
        )
    elif entry.kind == "add_wiki_entry":
        from .agents.writer import FileChange
        from .mcp_writes import commit_wiki_entry
        ok, summary = await commit_wiki_entry(entry.payload)
        await msg.answer(
            f"✓ {summary}" if ok else f"✗ {summary}"
        )
    else:
        await msg.answer(f"✗ Unknown kind: {entry.kind}")


@dp.message()
async def handle_any(msg: Message):
    if msg.from_user.id != _cfg().telegram_user_id:
        return

    text = (msg.text or "").strip()

    # /approve_<token> — MCP pending first, then v0.7 harvest pending
    m = _APPROVE_RE.match(text)
    if m:
        token = m.group(1)
        from .mcp_pending import pop_pending
        entry = pop_pending(token)
        if entry is not None:
            await _execute_pending(entry, msg)
            return
        # Fall back to Track-A harvest pending (24h TTL).
        # Only registered when .[eval] extras are installed.
        try:
            from evals.harvest import accept_pending as _accept_harvest
            ok, reply = _accept_harvest(token)
            if ok or "No pending" not in reply:
                await msg.answer(("✓ " if ok else "✗ ") + reply)
                return
        except ImportError:
            pass
        await msg.answer("Token expired or not found.")
        return

    # /deny_<token> — same two-store lookup
    m = _DENY_RE.match(text)
    if m:
        token = m.group(1)
        from .mcp_pending import pop_pending
        entry = pop_pending(token)
        if entry is not None:
            await msg.answer(f"✗ Denied ({entry.kind}).")
            return
        try:
            from evals.harvest import deny_pending as _deny_harvest
            ok, reply = _deny_harvest(token)
            if ok or "Could not delete" not in reply:
                await msg.answer(("✗ " if ok else "! ") + reply)
                return
        except ImportError:
            pass
        await msg.answer("Token expired or not found.")
        return

    # Otherwise, treat as a drop. Wrap in try/except so a pipeline /
    # commit error reaches the user instead of aiogram swallowing it.
    try:
        reply = await handle_drop(text)
        await msg.answer(reply, parse_mode="Markdown")
    except Exception as e:
        log.exception("handle_any drop error")
        # parse_mode=None: exception strings often contain unescaped
        # markdown chars (paths, brackets) that crash aiogram parser.
        await msg.answer(f"drop error: {e}", parse_mode=None)


def _extract_slug(text: str | None) -> str | None:
    """Parse '/done paper-a' → 'paper-a'. Whole argument is slugified so
    '/done Paper A' → 'paper-a' also works; user sees 'not found' if the
    slugified result doesn't match any project file."""
    if not text:
        return None
    parts = text.strip().split(maxsplit=1)
    if len(parts) < 2:
        return None
    from .taxonomy import slugify
    return slugify(parts[1])


def _move_project(slug: str, *, to_archive: bool) -> str:
    """Atomic rename + status flip. Returns a user-visible reply string."""
    from_dir, to_dir, new_status = (
        ("projects", "projects/archive", "done")
        if to_archive
        else ("projects/archive", "projects", "active")
    )
    src = f"{from_dir}/{slug}.md"
    dst = f"{to_dir}/{slug}.md"

    content = github_store.read(src)
    if not content:
        return f"`{src}` not found — nothing to move"

    updated = _flip_status_frontmatter(content, new_status)
    if not github_store.write(
        dst, updated, f"monogram: {slug} → {new_status} (user {'done' if to_archive else 'revive'})"
    ):
        return f"failed to write `{dst}`"

    # Delete source
    try:
        repo = github_store._repo()
        src_file = repo.get_contents(src)
        repo.delete_file(src, f"monogram: {slug} moved to {to_dir}/", src_file.sha)
    except Exception as e:
        return f"wrote `{dst}` but failed to remove `{src}`: {e}"

    return f"`{slug}` → `{dst}` (status: {new_status})"


def _flip_status_frontmatter(content: str, new_status: str) -> str:
    """Rewrite `status:` line in the YAML frontmatter, preserving everything else."""
    if not content.startswith("---"):
        return f"---\nstatus: {new_status}\n---\n\n{content}"
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("status:"):
            lines[i] = f"status: {new_status}"
            return "\n".join(lines)
    # No status line yet — insert before the closing `---`
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            lines.insert(i, f"status: {new_status}")
            return "\n".join(lines)
    return content


async def send_reply(user_id: int, text: str):
    """Called by listener to push drop confirmations into bot chat.

    Uses parse_mode=None for safety: drop replies may begin with
    "drop error: ..." carrying raw exception text with unescaped
    markdown chars; let it through as plain text instead of crashing.
    """
    await bot().send_message(user_id, text, parse_mode=None)


async def push_text(text: str, chunk_size: int = 3800) -> None:
    """One-shot push of arbitrary text to the configured user.

    Used by scheduled jobs (morning/weekly/digest) which run outside the
    long-polling loop. Creates a dedicated Bot instance so the session is
    closed cleanly when the coroutine exits — a naked `bot.send_message()`
    from cron leaves aiogram's aiohttp session open and the loop hangs.
    """
    from aiogram.client.bot import Bot as AiogramBot

    cfg = _cfg()
    text = text or "(empty message)"
    async with AiogramBot(token=cfg.telegram_bot_token) as one_shot:
        for i in range(0, len(text), chunk_size):
            await one_shot.send_message(
                cfg.telegram_user_id,
                text[i : i + chunk_size],
                parse_mode=None,  # plain text — briefs may contain un-escaped markdown
            )


async def run_bot():
    await dp.start_polling(bot())
