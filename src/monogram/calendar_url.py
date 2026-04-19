"""Deterministic Google Calendar add-URL builder.

LLMs hallucinate URL formats — a timestamp typo in a cron-generated
brief rots silently. This module gives morning/weekly jobs a pure-Python
helper so they can extract structured events from LLM output and build
URLs in code. No network, no OAuth, no 2-way sync (non-goal).
"""
from __future__ import annotations

from datetime import datetime
from urllib.parse import quote_plus


def _normalize_dt(value: str | datetime) -> str:
    """Return Google Calendar's expected `YYYYMMDDTHHMMSSZ` format."""
    if isinstance(value, datetime):
        return value.strftime("%Y%m%dT%H%M%SZ")
    # Accept ISO 8601 (YYYY-MM-DDTHH:MM:SSZ or YYYY-MM-DD)
    s = str(value).strip()
    if "T" not in s and len(s) == 10:
        # date-only → 00:00 UTC
        return s.replace("-", "") + "T000000Z"
    return s.replace("-", "").replace(":", "").replace("Z", "") + "Z"


def build_calendar_url(
    title: str,
    start: str | datetime,
    end: str | datetime | None = None,
    description: str = "",
    location: str = "",
    *,
    max_url_len: int = 2000,
) -> str:
    """Build a Google Calendar add-event URL.

    Title/description/location are URL-encoded. Description is truncated
    if the final URL would exceed `max_url_len` (Google caps URLs at
    ~2048; leave headroom).
    """
    end_value = end or start
    dates = f"{_normalize_dt(start)}/{_normalize_dt(end_value)}"

    params = [
        ("action", "TEMPLATE"),
        ("text", title),
        ("dates", dates),
        ("details", description),
        ("location", location),
    ]
    base = "https://calendar.google.com/calendar/render"
    qs = "&".join(f"{k}={quote_plus(v)}" for k, v in params if v)
    url = f"{base}?{qs}"

    # Trim description if URL is too long (description is the only
    # user-controlled field that can blow up; title/location stay intact).
    if len(url) > max_url_len and description:
        allowance = max_url_len - (len(url) - len(quote_plus(description))) - 10
        if allowance > 0:
            params_dict = dict(params)
            params_dict["details"] = description[:allowance] + "…"
            qs = "&".join(
                f"{k}={quote_plus(v)}"
                for k, v in params_dict.items() if v
            )
            url = f"{base}?{qs}"

    return url
