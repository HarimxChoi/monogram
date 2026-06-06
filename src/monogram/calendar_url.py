"""Google Calendar add-URL builder — avoids LLM hallucinating URL timestamp formats."""
from __future__ import annotations

from datetime import datetime
from urllib.parse import quote_plus


def _normalize_dt(value: str | datetime) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y%m%dT%H%M%SZ")
    s = str(value).strip()
    if "T" not in s and len(s) == 10:
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

    # Description is the only field that can blow up the URL length.
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
