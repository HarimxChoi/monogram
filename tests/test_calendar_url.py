"""Calendar URL builder — pure function, no network."""
from __future__ import annotations

from datetime import datetime, timezone

from monogram.calendar_url import build_calendar_url


def test_basic_url_has_required_params():
    url = build_calendar_url(
        title="Paper-a deadline",
        start="2026-05-01",
    )
    assert url.startswith("https://calendar.google.com/calendar/render")
    assert "action=TEMPLATE" in url
    assert "text=Paper-a+deadline" in url
    # `/` in dates is URL-encoded to %2F — Google accepts both forms
    assert "dates=20260501T000000Z%2F20260501T000000Z" in url


def test_datetime_object_input():
    url = build_calendar_url(
        title="Meeting",
        start=datetime(2026, 5, 1, 14, 30, tzinfo=timezone.utc),
        end=datetime(2026, 5, 1, 15, 30, tzinfo=timezone.utc),
    )
    assert "dates=20260501T143000Z%2F20260501T153000Z" in url


def test_url_encodes_special_characters_in_title():
    url = build_calendar_url(
        title="review: paper-a §3 & §4",
        start="2026-05-01",
    )
    assert "%26" in url  # & encoded
    assert "%3A" in url  # : encoded


def test_description_truncated_for_long_urls():
    very_long = "x" * 5000
    url = build_calendar_url(
        title="t",
        start="2026-05-01",
        description=very_long,
        max_url_len=2000,
    )
    assert len(url) <= 2000
    # Ellipsis encoded
    assert "%E2%80%A6" in url or "..." in url


def test_empty_optional_fields_are_omitted():
    url = build_calendar_url(title="t", start="2026-05-01")
    assert "details=" not in url
    assert "location=" not in url
