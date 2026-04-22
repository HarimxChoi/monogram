"""Telegram bot: on-demand reports + query commands.

Exposes five commands that mirror CLI / MCP functionality, pullable
from a phone without SSH:

    /report  [YYYY-MM-DD]   morning brief for a date (default: yesterday)
    /weekly  [YYYY-Www]     weekly report (default: last Mon–Sun block)
    /digest  [Nh|Nd]        commit digest since N hours/days (default: 24h)
    /search  <query>        grep the vault (credentials path always blocked)
    /last    [N]            last N drops across dates (default 10, cap 50)

Design constraints:
  - Every handler auth-gates on `cfg.telegram_user_id` before anything.
  - Per-(user, command) in-memory cooldowns prevent accidental LLM-spam;
    the dict is module-level, reset on bot restart — intentional: we'd
    rather lose short-term rate-limit state on restart than risk a
    pathological write to the vault.
  - Replies longer than Telegram's 4096-char limit route through
    `bot.push_text()` which chunks at 3800 and sends each chunk with
    `parse_mode=None`. Sticking to plain text avoids Markdown parse
    crashes on content like "2 * 2 = 4" that looks bold-opener.
  - `/search` hardcodes a `life/credentials/` skip even though the same
    path is on `vault_config.never_read_paths`. Defence in depth —
    if someone accidentally drops credentials-path off the config list,
    the bot surface still never leaks them to Telegram.
  - No new vault-config fields. Behaviour tweaks (cooldown durations,
    max N for /last) are constants in this module so the config stays
    uncluttered.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime, timedelta, timezone
from functools import cache
from pathlib import Path

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from .config import load_config
from .safe_read import safe_read

log = logging.getLogger("monogram.bot.reports")

router = Router(name="report_cmds")


# ── Config / auth ─────────────────────────────────────────────────────

@cache
def _cfg():
    return load_config()


def _user_allowed(msg: Message) -> bool:
    return str(msg.from_user.id) == str(_cfg().telegram_user_id)


# ── Cooldowns ────────────────────────────────────────────────────────
#
# Keyed on (user_id, command); value is last-call monotonic timestamp.
# Durations chosen by expected cost:
#   - /report, /weekly      → LLM call possible on cache miss
#   - /digest               → GitHub API calls; cheap but rate-limited
#   - /search, /last        → local reads; near-free

_COOLDOWN_S = {
    "report": 600.0,   # 10 min  — may trigger a Pro-tier LLM call
    "weekly": 1800.0,  # 30 min  — Pro + longer context
    "digest": 60.0,    # 1 min   — GitHub API call, several repos
    "search":   5.0,   # 5 sec   — just grep
    "last":     5.0,   # 5 sec   — local reads only
}

_last_call: dict[tuple[int, str], float] = {}


def _cooldown_ok(uid: int, cmd: str) -> tuple[bool, float]:
    now = time.monotonic()
    last = _last_call.get((uid, cmd), 0.0)
    remaining = _COOLDOWN_S[cmd] - (now - last)
    if remaining > 0:
        return False, remaining
    _last_call[(uid, cmd)] = now
    return True, 0.0


# ── Date / duration helpers ───────────────────────────────────────────

def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _yesterday() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")


def _parse_date_arg(raw: str | None) -> str | None:
    """YYYY-MM-DD string → normalized date string, or None if unparseable.

    Accepts tokens beyond date too (e.g. user typed `/report 2026-04-21
    with context`); only the first token is considered.
    """
    if not raw:
        return None
    token = raw.strip().split()[0]
    try:
        return datetime.strptime(token, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _parse_duration_arg(raw: str | None, default_hours: int = 24) -> int:
    """Parse `24h` / `7d` / `1w` into hours. Unparseable or non-positive
    → default. Silently clamping negatives to 1 would surprise users."""
    if not raw:
        return default_hours
    token = raw.strip().split()[0].lower()
    try:
        if token.endswith("h"):
            n = int(token[:-1])
        elif token.endswith("d"):
            n = int(token[:-1]) * 24
        elif token.endswith("w"):
            n = int(token[:-1]) * 24 * 7
        else:
            # Bare integer → hours
            n = int(token)
    except (ValueError, TypeError):
        return default_hours
    return n if n >= 1 else default_hours


def _last_week_label() -> str:
    """ISO week label (YYYY-Www) for the most recently completed Mon–Sun."""
    today = datetime.now(timezone.utc).date()
    # Back up to the most recent Sunday (weekday Mon=0..Sun=6)
    days_since_sunday = (today.weekday() + 1) % 7
    if days_since_sunday == 0:
        # Today IS a Sunday — use LAST week's Sunday
        last_sunday = today - timedelta(days=7)
    else:
        last_sunday = today - timedelta(days=days_since_sunday)
    iso_year, iso_week, _ = last_sunday.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


# ── Telegram delivery ────────────────────────────────────────────────

_TELEGRAM_CHUNK = 3800  # under the 4096 cap with headroom for markers


async def _send_long(msg: Message, text: str) -> None:
    """Send possibly-long text, splitting on paragraph boundaries first.

    Stays with `parse_mode=None` because vault content is GitHub-flavored
    Markdown (`**bold**`, fenced code, …) which Telegram's Markdown parser
    doesn't understand. Plain text is uglier but never crashes.
    """
    if not text:
        await msg.answer("(empty)", parse_mode=None)
        return
    if len(text) <= _TELEGRAM_CHUNK:
        await msg.answer(text, parse_mode=None)
        return

    # Prefer paragraph-boundary splits; fall back to hard slice if a single
    # block is bigger than the chunk cap.
    pieces: list[str] = []
    buf = ""
    for para in text.split("\n\n"):
        candidate = f"{buf}\n\n{para}" if buf else para
        if len(candidate) <= _TELEGRAM_CHUNK:
            buf = candidate
            continue
        if buf:
            pieces.append(buf)
        if len(para) <= _TELEGRAM_CHUNK:
            buf = para
        else:
            # Hard slice oversized paragraph
            for i in range(0, len(para), _TELEGRAM_CHUNK):
                pieces.append(para[i : i + _TELEGRAM_CHUNK])
            buf = ""
    if buf:
        pieces.append(buf)

    for p in pieces:
        await msg.answer(p, parse_mode=None)


async def _busy_or_ok(msg: Message, cmd: str) -> bool:
    """Return True if cooldown passes. On fail, tells the user and
    returns False — caller returns early."""
    ok, wait = _cooldown_ok(msg.from_user.id, cmd)
    if ok:
        return True
    await msg.answer(
        f"/{cmd}: cooldown — try again in {wait:.0f}s.",
        parse_mode=None,
    )
    return False


# ── /report ──────────────────────────────────────────────────────────

@router.message(Command("report"))
async def cmd_report(msg: Message, command: CommandObject):
    """Return daily/<date>/report.md. Defaults to yesterday. If missing
    AND target is yesterday (today's scheduled cron hasn't run yet),
    generate on demand via `generate_morning_brief`. Past dates are
    read-only — re-generating history is a cron-scheduled operation."""
    if not _user_allowed(msg):
        return
    if not await _busy_or_ok(msg, "report"):
        return

    target = _parse_date_arg(command.args) or _yesterday()
    path = f"daily/{target}/report.md"

    cached = safe_read(path)
    if cached:
        await _send_long(msg, f"📝 Morning brief — {target}\n\n{cached}")
        return

    if target != _yesterday():
        await msg.answer(
            f"No cached brief at `{path}`. On-demand generation is limited "
            "to yesterday's date (today's cron hasn't run yet); older "
            "briefs require cron or manual `monogram morning`.",
            parse_mode=None,
        )
        return

    # Generate on demand — may take 30–120s depending on Pro-tier latency.
    await msg.answer(
        f"Generating brief for {target}… (30–120s, single Pro-tier call)",
        parse_mode=None,
    )
    try:
        from .morning_job import generate_morning_brief
        brief = await asyncio.wait_for(
            generate_morning_brief(target), timeout=180
        )
    except asyncio.TimeoutError:
        await msg.answer(
            "LLM timeout after 180s. Pro tier may be rate-limited or slow.",
            parse_mode=None,
        )
        return
    except Exception as e:
        log.exception("cmd_report: generate_morning_brief failed")
        await msg.answer(f"brief error: {e}", parse_mode=None)
        return

    if not brief:
        await msg.answer(
            f"No activity on {target} — nothing to summarize.",
            parse_mode=None,
        )
        return
    await _send_long(msg, f"📝 Morning brief — {target}\n\n{brief}")


# ── /weekly ──────────────────────────────────────────────────────────

@router.message(Command("weekly"))
async def cmd_weekly(msg: Message, command: CommandObject):
    """Return reports/weekly/<YYYY-Www>.md. Defaults to last completed
    Mon–Sun block. On cache miss (only for the most recent week),
    generates on demand."""
    if not _user_allowed(msg):
        return
    if not await _busy_or_ok(msg, "weekly"):
        return

    raw_label = (command.args or "").strip().split()[0] if command.args else ""
    label = raw_label or _last_week_label()
    path = f"reports/weekly/{label}.md"

    cached = safe_read(path)
    if cached:
        await _send_long(msg, f"📊 Weekly report — {label}\n\n{cached}")
        return

    if label != _last_week_label():
        await msg.answer(
            f"No cached weekly at `{path}`. On-demand generation covers "
            "only the most recent completed week.",
            parse_mode=None,
        )
        return

    await msg.answer(
        f"Generating weekly report for {label}… (60–180s)",
        parse_mode=None,
    )
    try:
        from .weekly_job import generate_weekly_report
        report = await asyncio.wait_for(generate_weekly_report(), timeout=300)
    except asyncio.TimeoutError:
        await msg.answer("LLM timeout after 300s.", parse_mode=None)
        return
    except Exception as e:
        log.exception("cmd_weekly: generate_weekly_report failed")
        await msg.answer(f"weekly error: {e}", parse_mode=None)
        return

    if not report:
        await msg.answer(
            f"No activity in {label} — nothing to summarize.",
            parse_mode=None,
        )
        return
    await _send_long(msg, f"📊 Weekly report — {label}\n\n{report}")


# ── /digest ──────────────────────────────────────────────────────────

@router.message(Command("digest"))
async def cmd_digest(msg: Message, command: CommandObject):
    """Fresh commit digest over the last N hours (default 24h).
    Returns counts + the current commits.md for today."""
    if not _user_allowed(msg):
        return
    if not await _busy_or_ok(msg, "digest"):
        return

    hours = _parse_duration_arg(command.args, default_hours=24)
    await msg.answer(
        f"Fetching commits from the last {hours}h…",
        parse_mode=None,
    )

    try:
        from .digest import run_digest
        result = await asyncio.wait_for(
            run_digest(since_hours=hours), timeout=60
        )
    except asyncio.TimeoutError:
        await msg.answer(
            "GitHub API timeout — try again or narrow the window.",
            parse_mode=None,
        )
        return
    except Exception as e:
        log.exception("cmd_digest: run_digest failed")
        await msg.answer(f"digest error: {e}", parse_mode=None)
        return

    header = (
        f"🔁 Digest — last {hours}h\n"
        f"repos: {result.get('repos_fetched', 0)}  |  "
        f"commits: {result.get('commits', 0)}"
    )
    errors = result.get("errors") or []
    if errors:
        header += f"  |  errors: {len(errors)}"
    # Attach the rendered commits.md (written by run_digest) for detail.
    commits_md = safe_read(f"daily/{_today()}/commits.md") or ""
    await _send_long(msg, f"{header}\n\n{commits_md}".rstrip())


# ── /search ──────────────────────────────────────────────────────────

_CREDENTIAL_PATHS = ("life/credentials/",)
_SEARCH_HIT_CAP = 20


def _is_credential_line(line: str) -> bool:
    """Defence-in-depth: any hit whose path starts with a credential
    path is dropped, even though the LLM read-path is already gated."""
    # ripgrep / python fallback both yield "<relpath>:<lineno>:<content>"
    path = line.split(":", 1)[0]
    return any(path.startswith(p) for p in _CREDENTIAL_PATHS)


@router.message(Command("search"))
async def cmd_search(msg: Message, command: CommandObject):
    """`/search <query>` — fixed-string grep over the vault cache.

    Credentials path is unconditionally filtered from results so the
    bot never relays secrets to Telegram.
    """
    if not _user_allowed(msg):
        return
    if not await _busy_or_ok(msg, "search"):
        return

    query = (command.args or "").strip()
    if not query:
        await msg.answer(
            "Usage: `/search <query>` — fixed-string match across the vault.",
            parse_mode=None,
        )
        return
    if len(query) < 2:
        await msg.answer("Query too short (min 2 chars).", parse_mode=None)
        return

    try:
        import shutil as _sh
        from .cli_search import (
            _refresh_vault_cache,
            _search_via_python_re,
            _search_via_ripgrep,
        )
        vault_dir = _refresh_vault_cache()
        impl = (
            _search_via_ripgrep if _sh.which("rg") else _search_via_python_re
        )
        raw_hits: list[str] = []
        for line in impl(vault_dir, query, None, None, False, False):
            if _is_credential_line(line):
                continue
            raw_hits.append(line)
            if len(raw_hits) >= _SEARCH_HIT_CAP:
                break
    except Exception as e:
        log.exception("cmd_search failed")
        await msg.answer(f"search error: {e}", parse_mode=None)
        return

    if not raw_hits:
        await msg.answer(f"No hits for: {query}", parse_mode=None)
        return

    header = f"🔍 {len(raw_hits)} hit(s) for: {query}"
    body = "\n".join(raw_hits)
    await _send_long(msg, f"{header}\n\n{body}")


# ── /last ────────────────────────────────────────────────────────────

_LAST_DEFAULT = 10
_LAST_MAX = 50
_LAST_SCAN_DAYS = 14  # how far back to look when assembling recent drops


@router.message(Command("last"))
async def cmd_last(msg: Message, command: CommandObject):
    """Return the N most recent drop headers across daily/*/drops.md.
    Headers are the timestamped "## HH:MM — kind — slug" entries the
    listener emits per drop."""
    if not _user_allowed(msg):
        return
    if not await _busy_or_ok(msg, "last"):
        return

    try:
        n = int((command.args or "").strip().split()[0]) if command.args else _LAST_DEFAULT
    except (ValueError, IndexError):
        n = _LAST_DEFAULT
    n = max(1, min(n, _LAST_MAX))

    today_dt = datetime.now(timezone.utc).date()
    entries: list[tuple[str, str]] = []  # (datetime_str, header_line)

    for i in range(_LAST_SCAN_DAYS):
        day = (today_dt - timedelta(days=i)).strftime("%Y-%m-%d")
        content = safe_read(f"daily/{day}/drops.md")
        if not content:
            continue
        for line in content.splitlines():
            line = line.rstrip()
            # drops.md emits entries starting with `## ` per drop (see writer.py)
            if line.startswith("## "):
                entries.append((day, line[3:].strip()))
        if len(entries) >= n * 3:
            # Collected enough candidate lines; we'll slice after sorting
            break

    if not entries:
        await msg.answer(
            f"No drops found in the last {_LAST_SCAN_DAYS} days.",
            parse_mode=None,
        )
        return

    # Entries are appended in file order (oldest → newest within a day,
    # newest day first since we iterate i=0..). Reverse within-day so the
    # most recent first, then stable-concat.
    by_day: dict[str, list[str]] = {}
    for day, line in entries:
        by_day.setdefault(day, []).append(line)

    flat: list[tuple[str, str]] = []
    for day in sorted(by_day.keys(), reverse=True):
        for line in reversed(by_day[day]):
            flat.append((day, line))

    flat = flat[:n]
    shown = "\n".join(f"- {day}  {header}" for day, header in flat)
    await _send_long(msg, f"🕐 Last {len(flat)} drop(s)\n\n{shown}")
