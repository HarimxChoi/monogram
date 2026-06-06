"""Scheduler due-logic tests (no live jobs / no network)."""
from datetime import datetime, timedelta, timezone

from monogram.scheduler import _morning_due, _weekly_due


def test_morning_due_after_hour_once_per_day():
    now = datetime(2026, 6, 1, 8, 30, tzinfo=timezone.utc)  # 08:30 UTC
    assert _morning_due(now, last_run=None, hour=8) is True
    # already ran today → not due again
    assert _morning_due(now, last_run=now.date(), hour=8) is False
    # before the configured hour → not due
    early = datetime(2026, 6, 1, 7, 59, tzinfo=timezone.utc)
    assert _morning_due(early, last_run=None, hour=8) is False


def test_weekly_due_only_on_dow_after_hour():
    # find the next Sunday (weekday() == 6) at 21:05 UTC
    sunday = datetime(2026, 6, 1, 21, 5, tzinfo=timezone.utc)
    while sunday.weekday() != 6:
        sunday += timedelta(days=1)

    assert _weekly_due(sunday, last_run=None, dow=6, hour=21) is True
    iso = sunday.isocalendar()
    assert _weekly_due(sunday, last_run=(iso[0], iso[1]), dow=6, hour=21) is False

    # the day after Sunday is not a weekly-due day
    not_sunday = sunday + timedelta(days=1)
    assert _weekly_due(not_sunday, last_run=None, dow=6, hour=21) is False
