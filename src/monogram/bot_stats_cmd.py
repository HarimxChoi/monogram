"""/stats — show pipeline latency + error-rate distribution in Telegram.

Lets the user check dogfood metrics from their phone without SSH'ing
to the server or opening the GitHub repo.

Usage in Telegram:
    /stats           → last 7 days
    /stats 1         → last 24 hours
    /stats 30        → last 30 days

Keeps replies under 4096 chars (Telegram message limit). For full
reports, users can run `monogram stats --window 30 --markdown` locally.
"""
from __future__ import annotations

import logging
import re
from functools import cache

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from .config import load_config

log = logging.getLogger("monogram.bot_stats_cmd")

router = Router()


@cache
def _cfg():
    """Lazy app-config accessor — defers .env loading until first use."""
    return load_config()


@router.message(Command("stats"))
async def stats_cmd(message: Message):
    """Reply with pipeline health metrics from log/pipeline.jsonl."""
    if str(message.from_user.id) != str(_cfg().telegram_user_id):
        return  # silent ignore non-owner

    # Parse optional window arg: /stats 30 → 30 days
    text = (message.text or "").strip()
    window_days = 7
    match = re.search(r"/stats\s+(\d+)", text)
    if match:
        try:
            window_days = max(1, min(90, int(match.group(1))))
        except ValueError:
            pass

    from .pipeline_stats import fetch_stats

    try:
        stats = fetch_stats(window_days=window_days)
    except Exception as e:
        log.warning("/stats error: %s", e)
        await message.answer(f"stats error: {type(e).__name__}")
        return

    if stats is None or stats.total_runs == 0:
        await message.answer(
            f"No pipeline data in last {window_days}d. "
            f"Drop something in Saved Messages to populate the log."
        )
        return

    # Compact markdown for Telegram (no tables, which render poorly on mobile)
    lines = [
        f"*Pipeline stats — last {window_days}d*",
        f"",
        f"Runs: {stats.total_runs}",
        f"Errors: {stats.error_rate:.1%} · Escalations: {stats.escalation_rate:.1%}",
    ]
    if stats.latency.samples < 10:
        lines.append(f"⚠ warmup (n={stats.latency.samples}): p95/p99 not yet reliable")
    lines += [
        f"",
        f"Latency (ms):",
        f"  p50 {stats.latency.p50_ms} · p95 {stats.latency.p95_ms} · p99 {stats.latency.p99_ms}",
        f"  mean {stats.latency.mean_ms} · max {stats.latency.max_ms}",
    ]

    if stats.per_stage:
        lines.extend(["", "Per-stage p50/p95 (ms):"])
        for s in stats.per_stage:
            lines.append(f"  {s.stage}: {s.p50_ms}/{s.p95_ms}")

    if stats.by_target_kind:
        # Top 5 kinds
        top = sorted(stats.by_target_kind.items(), key=lambda x: -x[1])[:5]
        lines.extend(["", "Top target kinds:"])
        for kind, count in top:
            lines.append(f"  {kind}: {count}")

    # Use HTML format to avoid accidental markdown-escape issues
    reply = "\n".join(lines)
    if len(reply) > 4000:
        reply = reply[:4000] + "\n…(truncated)"

    await message.answer(reply, parse_mode=None)
