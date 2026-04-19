"""Scheduled-job observability — commits a run log to log/runs/YYYY-MM-DD-<job>.md.

Without this, a silent cron failure (quota exhausted, PAT revoked,
LLM hiccup) is invisible until the next morning when no brief arrives.
Each scheduled job records its status, duration, and error list.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from . import github_store


@contextmanager
def log_run(job_name: str) -> Iterator[dict]:
    """Context manager that records success/failure + duration to log/runs/.

    Usage:
        with log_run("morning") as status:
            status["brief_len"] = len(brief)
            ...
    """
    start = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    status: dict = {"job": job_name, "started_at": started_at, "ok": True}

    try:
        yield status
    except Exception as e:
        status["ok"] = False
        status["error"] = f"{type(e).__name__}: {e}"
        _write_run_log(status, time.monotonic() - start)
        raise
    else:
        _write_run_log(status, time.monotonic() - start)


def _write_run_log(status: dict, duration_s: float) -> None:
    job = status["job"]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = f"log/runs/{today}-{job}.md"

    # Render known fields in a stable order, then anything extra.
    known_order = ("job", "started_at", "ok", "error")
    parts = [f"# {job} — {today}", ""]
    parts.append(f"started_at: {status['started_at']}")
    parts.append(f"duration_s: {duration_s:.2f}")
    parts.append(f"ok: {status['ok']}")
    if not status["ok"]:
        parts.append(f"error: {status.get('error', '')}")
    for k, v in status.items():
        if k in known_order:
            continue
        parts.append(f"{k}: {v}")

    body = "\n".join(parts) + "\n"
    try:
        github_store.write(path, body, f"monogram runlog: {job} {today}")
    except Exception as log_err:
        # Logging the logger's failure — last-ditch stderr output.
        print(f"runlog write failed: {log_err}")
