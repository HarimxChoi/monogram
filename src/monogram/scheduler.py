"""In-process scheduler (opt-in); fires morning + weekly jobs at most once per period (UTC)."""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, timezone

log = logging.getLogger("monogram.scheduler")

_CHECK_INTERVAL_SECONDS = 300


def _morning_due(now: datetime, last_run: date | None, hour: int) -> bool:
    return now.hour >= hour and last_run != now.date()


def _weekly_due(
    now: datetime, last_run: tuple[int, int] | None, dow: int, hour: int
) -> bool:
    # Tracks ISO (year, week) so a restart mid-week doesn't re-fire.
    if now.weekday() != dow or now.hour < hour:
        return False
    iso = now.isocalendar()
    return last_run != (iso[0], iso[1])


async def run_scheduler(
    morning_hour: int = 8,
    weekly_dow: int = 6,
    weekly_hour: int = 21,
) -> None:
    if os.environ.get("MONOGRAM_INPROCESS_SCHEDULER", "").strip() != "1":
        log.info(
            "scheduler: in-process scheduler off (default) — morning/weekly run "
            "via GitHub Actions. Set MONOGRAM_INPROCESS_SCHEDULER=1 to enable here."
        )
        return

    now = datetime.now(timezone.utc)
    # Mark period as already-run on startup if past the trigger hour, to avoid re-firing on restart.
    last_morning: date | None = now.date() if now.hour >= morning_hour else None
    iso = now.isocalendar()
    last_weekly: tuple[int, int] | None = (
        (iso[0], iso[1])
        if now.weekday() == weekly_dow and now.hour >= weekly_hour
        else None
    )
    log.info(
        "scheduler: in-process scheduler ON (morning %02d:00 UTC, weekly dow=%d %02d:00 UTC)",
        morning_hour, weekly_dow, weekly_hour,
    )

    while True:
        now = datetime.now(timezone.utc)
        if _morning_due(now, last_morning, morning_hour):
            last_morning = now.date()
            await _run_job("morning")
        if _weekly_due(now, last_weekly, weekly_dow, weekly_hour):
            iso = now.isocalendar()
            last_weekly = (iso[0], iso[1])
            await _run_job("weekly")
        await asyncio.sleep(_CHECK_INTERVAL_SECONDS)


async def _run_job(which: str) -> None:
    # Swallow errors so a failing job never kills the scheduler loop.
    try:
        if which == "morning":
            from .morning_job import run_morning_job
            result = await run_morning_job(push_to_telegram=True)
        else:
            from .weekly_job import run_weekly_job
            result = await run_weekly_job(push_to_telegram=True)
        log.info("scheduler: %s job done: %s", which, result)
    except Exception as e:
        log.exception("scheduler: %s job failed: %s", which, e)
