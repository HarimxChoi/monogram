"""FRED (Federal Reserve Economic Data) adapter.

Pulls a configurable list of economic series from the St. Louis Fed's
free API and renders them as a markdown table for `daily/<date>/signals.md`.

Default series cover the macro dashboard a knowledge-worker cares about:
inflation (CPI), policy rates (Fed funds), curve (10Y, 2Y, spread),
unemployment, crude, and USD/KRW for the Korean context.

API key:
  - Free, issued instantly at <https://fred.stlouisfed.org/docs/api/api_key.html>
  - Set `FRED_API_KEY` in `.env`. If unset, this module is a no-op.

Rate limit:
  - FRED allows 120 req/min. Each dashboard fetch makes N small requests
    (default N=9). Well under the limit even with retries.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("monogram.news.fred")

_FRED_BASE = "https://api.stlouisfed.org/fred"
_FETCH_TIMEOUT = 10.0
_TOTAL_TIMEOUT = 45.0   # upper bound across the full dashboard fetch

# Default dashboard — can be overridden via vault_config.news_fred_series
# or the --series option on `monogram news fetch`.
DEFAULT_SERIES: list[str] = [
    "CPIAUCSL",     # Consumer Price Index, All Urban Consumers (inflation)
    "FEDFUNDS",     # Effective Federal Funds Rate
    "DGS10",        # 10-Year Treasury constant maturity
    "DGS2",         # 2-Year Treasury constant maturity
    "T10Y2Y",       # 10Y-2Y spread (negative = inversion = recession watch)
    "UNRATE",       # Unemployment rate (seasonally adjusted)
    "DCOILWTICO",   # WTI crude oil spot
    "DEXKOUS",      # South Korean Won to US Dollar (0.00074 = 1 USD → ~1350 KRW)
    "PAYEMS",       # Nonfarm Payrolls (total employment)
]

# Human labels. Not all series need one; fallback is the raw id.
_SERIES_LABEL = {
    "CPIAUCSL":   "CPI (all items)",
    "FEDFUNDS":   "Fed funds rate",
    "DGS10":      "10Y Treasury",
    "DGS2":       "2Y Treasury",
    "T10Y2Y":     "10Y-2Y spread",
    "UNRATE":     "Unemployment",
    "DCOILWTICO": "WTI crude",
    "DEXKOUS":    "KRW/USD",
    "PAYEMS":     "Nonfarm payrolls",
    "GDP":        "GDP (nominal)",
    "M2SL":       "M2 money supply",
}


@dataclass
class FredObservation:
    series_id: str
    label: str
    latest_value: float | None
    latest_date: str
    prev_value: float | None
    prev_date: str
    unit_hint: str  # "pct" | "index" | "usd" | "ratio" — informs formatting

    @property
    def delta(self) -> float | None:
        if self.latest_value is None or self.prev_value is None:
            return None
        return self.latest_value - self.prev_value

    @property
    def delta_pct(self) -> float | None:
        if (
            self.latest_value is None or self.prev_value is None
            or self.prev_value == 0
        ):
            return None
        return (self.latest_value - self.prev_value) / self.prev_value * 100


def _infer_unit(series_id: str) -> str:
    """Pick a rendering hint based on the series id. FRED doesn't return
    unit info in /series/observations alone (would require a second /series
    call); this gets the common ones right and falls back to generic."""
    if series_id in ("FEDFUNDS", "DGS10", "DGS2", "UNRATE", "T10Y2Y"):
        return "pct"
    if series_id == "DCOILWTICO":
        return "usd"
    if series_id == "DEXKOUS":
        # FRED publishes KRW/USD as KRW-per-USD (e.g. 1350 = 1 USD buys 1350 KRW).
        # Render as KRW/USD integer.
        return "fx"
    if series_id == "PAYEMS":
        return "kilos"  # series is in thousands of persons
    return "index"


def _fmt_value(v: float | None, unit: str) -> str:
    if v is None:
        return "—"
    if unit == "pct":
        return f"{v:.2f}%"
    if unit == "usd":
        return f"${v:,.2f}"
    if unit == "fx":
        return f"{v:,.2f}"
    if unit == "kilos":
        return f"{v:,.0f}k"
    return f"{v:,.2f}"


def _fmt_delta(obs: FredObservation) -> str:
    d = obs.delta
    if d is None:
        return "—"
    if obs.unit_hint == "pct":
        # For percentage series, delta is in percentage points → "+4 bp" etc.
        bp = d * 100
        return f"{'+' if bp >= 0 else ''}{bp:,.0f} bp"
    pct = obs.delta_pct
    if pct is None:
        return "—"
    return f"{'+' if pct >= 0 else ''}{pct:.2f}%"


async def fetch_series(
    series_id: str,
    api_key: str,
    timeout: float = _FETCH_TIMEOUT,
) -> FredObservation | None:
    """Fetch latest + prior observation for one FRED series.

    Returns None on any failure (unreachable, 4xx, empty series, etc.) —
    the whole dashboard tolerates per-series failures so one bad ID
    doesn't kill the morning run.
    """
    try:
        import httpx
    except ImportError:
        log.warning("httpx not installed — FRED adapter disabled")
        return None

    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "limit": "2",
        "sort_order": "desc",
    }
    url = f"{_FRED_BASE}/series/observations"
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            resp = await c.get(url, params=params)
    except Exception as e:
        log.warning("FRED %s: network error %s", series_id, e)
        return None

    if resp.status_code == 400:
        log.warning("FRED %s: bad request (invalid series id?)", series_id)
        return None
    if resp.status_code != 200:
        log.warning("FRED %s: HTTP %s", series_id, resp.status_code)
        return None

    try:
        obs = resp.json().get("observations") or []
    except Exception:
        log.warning("FRED %s: non-JSON response", series_id)
        return None
    if not obs:
        return None

    def _parse(row: dict[str, Any]) -> tuple[float | None, str]:
        raw = row.get("value", ".")
        try:
            v = None if raw in (".", "", None) else float(raw)
        except (TypeError, ValueError):
            v = None
        return v, str(row.get("date", ""))

    latest_v, latest_d = _parse(obs[0])
    prev_v, prev_d = _parse(obs[1]) if len(obs) > 1 else (None, "")
    return FredObservation(
        series_id=series_id,
        label=_SERIES_LABEL.get(series_id, series_id),
        latest_value=latest_v,
        latest_date=latest_d,
        prev_value=prev_v,
        prev_date=prev_d,
        unit_hint=_infer_unit(series_id),
    )


async def fetch_dashboard(
    series_ids: list[str] | None = None,
    api_key: str | None = None,
) -> list[FredObservation]:
    """Fetch all series in parallel, tolerating per-series failures.

    `api_key` defaults to `FRED_API_KEY` env var. Returns empty list if
    key is missing (the caller decides whether that's a silent no-op
    or an error — typically silent when `news_enabled=False`).
    """
    key = api_key or os.environ.get("FRED_API_KEY", "").strip()
    if not key:
        log.info("FRED_API_KEY not set — skipping FRED dashboard")
        return []

    ids = series_ids or DEFAULT_SERIES
    try:
        results = await asyncio.wait_for(
            asyncio.gather(
                *(fetch_series(sid, key) for sid in ids),
                return_exceptions=True,
            ),
            timeout=_TOTAL_TIMEOUT,
        )
    except asyncio.TimeoutError:
        log.warning("FRED dashboard total timeout (%ss)", _TOTAL_TIMEOUT)
        return []

    out: list[FredObservation] = []
    for r in results:
        if isinstance(r, FredObservation):
            out.append(r)
        elif isinstance(r, Exception):
            log.warning("FRED adapter raised: %s", r)
    return out


def render_signals_md(observations: list[FredObservation]) -> str:
    """Markdown section for `daily/<date>/signals.md`. Table + notes.

    Intentionally fact-only. No interpretation — that's the morning
    brief's job. Notes block below the table surfaces anything
    mechanically noteworthy (inversions, multi-SD moves) so a human
    or LLM reading the file can spot signals without eyeballing rows.
    """
    if not observations:
        return ""

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [
        f"# Signals — {today}",
        "",
        "## Macro indicators (FRED)",
        "",
        "| Series | Latest | Prev | Δ | As-of |",
        "|---|---|---|---|---|",
    ]
    for o in observations:
        lines.append(
            f"| {o.label} "
            f"| {_fmt_value(o.latest_value, o.unit_hint)} "
            f"| {_fmt_value(o.prev_value, o.unit_hint)} "
            f"| {_fmt_delta(o)} "
            f"| {o.latest_date} |"
        )

    # Mechanical flags
    notes: list[str] = []
    for o in observations:
        if o.series_id == "T10Y2Y" and o.latest_value is not None and o.latest_value < 0:
            notes.append(
                f"- ⚠ **Yield curve inverted** — 10Y-2Y = {_fmt_value(o.latest_value, 'pct')}"
            )
        if o.delta_pct is not None and abs(o.delta_pct) >= 5.0 and o.unit_hint != "pct":
            notes.append(
                f"- {o.label} moved {_fmt_delta(o)} day-over-day"
            )

    if notes:
        lines.extend(["", "## Notes", "", *notes])
    lines.append("")
    return "\n".join(lines)


async def fetch_and_render(
    series_ids: list[str] | None = None,
    api_key: str | None = None,
) -> str:
    """Convenience: fetch dashboard + render markdown in one call.

    Returns empty string if no observations (key missing, all series
    failed, etc.) so the caller can skip writing an empty signals.md.
    """
    observations = await fetch_dashboard(series_ids, api_key)
    return render_signals_md(observations)
