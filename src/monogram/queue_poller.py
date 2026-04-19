"""v0.5 — poll daily/<today>/queue-*.md files, run each through the pipeline.

Obsidian plugin writes captures to `daily/<today>/queue-<ts>-<rand>.md`.
This poller runs alongside listener + bot in `monogram run`, picks up
new queue files every 2 minutes, processes them, and deletes on success.

Failure policy: leave the queue file + log error; retry next cycle.
"""
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
    """Return full paths of queue-*.md files in daily/<date>/.

    v0.5.1: skip files that have a companion `.processing` sidecar — those
    are either in-flight or recently processed (waiting for delete to
    succeed). Prevents duplicate writes on transient delete failures.
    """
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
            # Already being handled or recently processed — skip this cycle.
            log.debug("queue_poller: skipping %s (sidecar present)", f.path)
            continue
        out.append(f.path)
    return sorted(out)


def _write_sidecar(queue_path: str, note: str) -> None:
    """Best-effort sidecar creation. Non-blocking — failure tolerated."""
    try:
        github_store.write(
            queue_path + ".processing",
            f"{note}\n",
            f"monogram: queue claim [{queue_path.rsplit('/', 1)[-1]}]",
        )
    except Exception as e:
        log.warning("queue_poller: sidecar write failed for %s: %s", queue_path, e)


def _delete_sidecar(queue_path: str) -> None:
    """Best-effort sidecar removal."""
    sidecar = queue_path + ".processing"
    try:
        repo = github_store._repo()
        entry = repo.get_contents(sidecar)
        repo.delete_file(sidecar, "monogram: clear sidecar", entry.sha)
    except Exception as e:
        log.debug("queue_poller: sidecar delete failed for %s: %s", sidecar, e)


def _extract_body(content: str) -> str:
    """Strip the frontmatter block; return the body (the actual drop text)."""
    try:
        meta, body = github_store.parse_metadata(content)
        if meta or body:
            return body or ""
    except Exception:
        pass
    return content


async def process_one(path: str) -> bool:
    """Read + pipeline + commit a single queue file. Returns True on success.

    v0.5.1 flow (sidecar-protected):
      1. Read queue file
      2. If empty/blocked → return False (no sidecar created)
      3. Write `.processing` sidecar (claims the file across cycles)
      4. Run pipeline via handle_drop
      5. On success: delete queue file + delete sidecar (both best-effort)
      6. On failure: leave sidecar — next cycle will skip until sidecar
         expires or is manually cleared

    If step 5 partially fails (queue file deleted but sidecar lingers),
    the file is already gone so no duplicate write is possible. If the
    queue-file delete fails but sidecar succeeds, next cycle skips thanks
    to the sidecar. Worst case: stale sidecar + stale queue file; admin
    cleans manually or via a future `/config_clear_queue_sidecars` command.
    """
    content = safe_read(path)
    if not content:
        log.warning("queue_poller: %s missing or blocked", path)
        return False

    body = _extract_body(content).strip()
    if not body:
        log.warning("queue_poller: %s has empty body", path)
        return False

    # Claim the file before running the pipeline.
    _write_sidecar(path, f"started {_today()}")

    try:
        reply = await handle_drop(body)
    except Exception as e:
        log.error("queue_poller: pipeline raised on %s: %s", path, e)
        _delete_sidecar(path)  # release claim so retry is possible next cycle
        return False

    if reply.startswith("blocked") or "write failed" in reply:
        log.warning("queue_poller: %s did not commit — keeping: %s", path, reply)
        _delete_sidecar(path)  # release claim so retry is possible
        return False

    # Delete the queue file; on failure leave sidecar so next cycle skips.
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
    """Forever-loop poller. Intended to run inside asyncio.gather(...)."""
    log.info("queue_poller: started (interval=%ss)", interval_sec)
    while True:
        try:
            queue_paths = _list_queue_files()
            for path in queue_paths:
                await process_one(path)
        except Exception as e:
            log.warning("queue_poller: loop error: %s", e)
        await asyncio.sleep(interval_sec)
