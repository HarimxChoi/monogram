"""Scheduled-job observability — commits status/duration/errors to log/runs/."""
from __future__ import annotations

import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from . import github_store


@contextmanager
def log_run(job_name: str) -> Iterator[dict]:
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
        print(f"runlog write failed: {log_err}")
