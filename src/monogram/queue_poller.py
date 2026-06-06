"""Poll daily/<today>/queue-*.md files and run each through the pipeline."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone

from . import github_store
from .listener import handle_drop
from .safe_read import safe_read

log = logging.getLogger("monogram.queue_poller")

_POLL_INTERVAL_SEC = 120  # 2 minutes
_QUEUE_RE = re.compile(r"^queue-(\d+)-([a-z0-9]+)\.md$")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _list_queue_files(date: str | None = None) -> list[str]:
    """Skip files with a .processing sidecar to prevent duplicate writes on transient delete failures."""
    date = date or _today()
    try:
        repo = github_store._repo()
        contents = repo.get_contents(f"daily/{date}")
    except Exception:
        return []
    files_in_dir = {f.name for f in contents if f.type == "file"}
    out = []
    for f in contents:
        if f.type != "file":
            continue
        if not _QUEUE_RE.match(f.name):
            continue
        if f"{f.name}.processing" in files_in_dir:
            log.debug("queue_poller: skipping %s (sidecar present)", f.path)
            continue
        out.append(f.path)
    return sorted(out)


def _write_sidecar(queue_path: str, note: str) -> None:
    try:
        github_store.write(
            queue_path + ".processing",
            f"{note}\n",
            f"monogram: queue claim [{queue_path.rsplit('/', 1)[-1]}]",
        )
    except Exception as e:
        log.warning("queue_poller: sidecar write failed for %s: %s", queue_path, e)


def _delete_sidecar(queue_path: str) -> None:
    sidecar = queue_path + ".processing"
    try:
        repo = github_store._repo()
        entry = repo.get_contents(sidecar)
        repo.delete_file(sidecar, "monogram: clear sidecar", entry.sha)
    except Exception as e:
        log.debug("queue_poller: sidecar delete failed for %s: %s", sidecar, e)


def _extract_body(content: str) -> str:
    try:
        meta, body = github_store.parse_metadata(content)
        if meta or body:
            return body or ""
    except Exception:
        pass
    return content


async def process_one(path: str) -> bool:
    """Sidecar-protected: write .processing before pipeline, delete on success; on partial failure next cycle skips via sidecar."""
    content = safe_read(path)
    if not content:
        log.warning("queue_poller: %s missing or blocked", path)
        return False

    body = _extract_body(content).strip()
    if not body:
        log.warning("queue_poller: %s has empty body", path)
        return False

    _write_sidecar(path, f"started {_today()}")

    try:
        reply = await handle_drop(body)
    except Exception as e:
        log.error("queue_poller: pipeline raised on %s: %s", path, e)
        _delete_sidecar(path)
        return False

    if reply.startswith("blocked") or "write failed" in reply:
        log.warning("queue_poller: %s did not commit — keeping: %s", path, reply)
        _delete_sidecar(path)
        return False

    deleted = False
    try:
        repo = github_store._repo()
        entry = repo.get_contents(path)
        repo.delete_file(
            path, f"monogram: queue processed — {path.rsplit('/', 1)[-1]}", entry.sha
        )
        log.info("queue_poller: processed + deleted %s", path)
        deleted = True
    except Exception as e:
        log.warning(
            "queue_poller: queue delete failed for %s (sidecar retained): %s",
            path, e,
        )

    if deleted:
        _delete_sidecar(path)
    return True


async def run_queue_poller(interval_sec: int = _POLL_INTERVAL_SEC) -> None:
    log.info("queue_poller: started (interval=%ss)", interval_sec)
    while True:
        try:
            queue_paths = _list_queue_files()
            for path in queue_paths:
                await process_one(path)
        except Exception as e:
            log.warning("queue_poller: loop error: %s", e)
        await asyncio.sleep(interval_sec)
