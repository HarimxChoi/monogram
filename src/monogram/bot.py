"""Aiogram bot handlers: /start, /status, free-form drops, and MCP-gated /approve|deny."""
from __future__ import annotations

import re

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message

from . import github_store
from .config import load_config
from .listener import handle_drop

config = load_config()
bot = Bot(token=config.telegram_bot_token)
dp = Dispatcher()

from .bot_config_cmds import router as _config_router  # noqa: E402
dp.include_router(_config_router)

from .bot_webui_cmds import router as _webui_router  # noqa: E402
dp.include_router(_webui_router)

from .bot_eval_cmds import router as _eval_router  # noqa: E402
dp.include_router(_eval_router)

from .bot_stats_cmd import router as _stats_router  # noqa: E402
dp.include_router(_stats_router)


@dp.message(Command("start"))
async def cmd_start(msg: Message):
    await msg.answer(
        "Monogram online.\n\n"
        "Drop anything in *Saved Messages* — links, thoughts, voice notes.\n"
        "Talk to me here for queries.",
        parse_mode="Markdown",
    )


@dp.message(Command("status"))
async def cmd_status(msg: Message):
    content = github_store.read("README.md") or "(scheduler README empty)"
    await msg.answer(content[:4000], parse_mode="Markdown")


@dp.message(Command("done"))
async def cmd_done(msg: Message):
    """Mark a project done: `/done paper-a` → moves to scheduler/archive/."""
    if msg.from_user.id != config.telegram_user_id:
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
    if msg.from_user.id != config.telegram_user_id:
        return
    slug = _extract_slug(msg.text)
    if not slug:
        await msg.answer("Usage: `/revive <project-slug>`", parse_mode="Markdown")
        return
    moved = _move_project(slug, to_archive=False)
    await msg.answer(moved, parse_mode="Markdown")


@dp.message(Command("search"))
async def cmd_search(msg: Message):
    """Hybrid semantic + lexical search over the vault: `/search how do I deploy`."""
    if msg.from_user.id != config.telegram_user_id:
        return
    query = re.sub(r"^/search(@\w+)?\s*", "", msg.text or "", count=1).strip()
    if not query:
        await msg.answer("Usage: /search <query>")
        return
    from .semantic_index import semantic_search
    try:
        hits = await semantic_search(query, k=8)
    except Exception as e:
        await msg.answer(f"search failed: {e}")
        return
    if not hits:
        await msg.answer("No matches. If the index is empty, run `monogram reindex`.")
        return
    # No parse_mode: excerpts contain arbitrary markdown chars.
    lines = [f"🔎 {query}"]
    for i, h in enumerate(hits, 1):
        head = f" — {h['heading']}" if h.get("heading") else ""
        lines.append(f"\n{i}. {h['path']}{head}\n   {h.get('excerpt', '')[:160]}")
    await msg.answer("".join(lines)[:4000])


# Regex accepts 8-64 chars to handle both 22-char urlsafe tokens and legacy 8-char hex stale tokens.
_APPROVE_RE = re.compile(r"^/approve_([A-Za-z0-9_-]{8,64})(?:\s|$)")
_DENY_RE = re.compile(r"^/deny_([A-Za-z0-9_-]{8,64})(?:\s|$)")


async def _execute_pending(entry, msg: Message) -> None:
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
    if msg.from_user.id != config.telegram_user_id:
        return

    text = (msg.text or "").strip()

    m = _APPROVE_RE.match(text)
    if m:
        token = m.group(1)
        from .mcp_pending import pop_pending
        entry = pop_pending(token)
        if entry is not None:
            await _execute_pending(entry, msg)
            return
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

    reply = await handle_drop(text)
    await msg.answer(reply, parse_mode="Markdown")


def _extract_slug(text: str | None) -> str | None:
    """Parse '/done paper-a' → 'paper-a'; slugifies so '/done Paper A' also works."""
    if not text:
        return None
    parts = text.strip().split(maxsplit=1)
    if len(parts) < 2:
        return None
    from .taxonomy import slugify
    return slugify(parts[1])


def _move_project(slug: str, *, to_archive: bool) -> str:
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

    try:
        repo = github_store._repo()
        src_file = repo.get_contents(src)
        repo.delete_file(src, f"monogram: {slug} moved to {to_dir}/", src_file.sha)
    except Exception as e:
        return f"wrote `{dst}` but failed to remove `{src}`: {e}"

    return f"`{slug}` → `{dst}` (status: {new_status})"


def _flip_status_frontmatter(content: str, new_status: str) -> str:
    if not content.startswith("---"):
        return f"---\nstatus: {new_status}\n---\n\n{content}"
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("status:"):
            lines[i] = f"status: {new_status}"
            return "\n".join(lines)
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            lines.insert(i, f"status: {new_status}")
            return "\n".join(lines)
    return content


async def send_reply(user_id: int, text: str):
    await bot.send_message(user_id, text, parse_mode="Markdown")


async def push_text(text: str, chunk_size: int = 3800) -> None:
    """Used by cron jobs; creates a fresh Bot so aiohttp session closes cleanly (naked bot.send_message hangs the loop)."""
    from aiogram.client.bot import Bot as AiogramBot

    text = text or "(empty message)"
    async with AiogramBot(token=config.telegram_bot_token) as one_shot:
        for i in range(0, len(text), chunk_size):
            await one_shot.send_message(
                config.telegram_user_id,
                text[i : i + chunk_size],
                parse_mode=None,  # briefs may contain un-escaped markdown
            )


async def run_bot():
    await dp.start_polling(bot)
