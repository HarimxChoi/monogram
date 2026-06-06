"""Single-instance guard — latest `monogram run` wins; older instances step down on read-check."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import uuid
from datetime import datetime, timezone

log = logging.getLogger("monogram.instance")

_LOCK_PATH = ".monogram/instance.json"
_CHECK_INTERVAL_SECONDS = 180


def _disabled() -> bool:
    return os.environ.get("MONOGRAM_NO_INSTANCE_LOCK", "").strip() == "1"


def _is_owner(lock: dict | None, instance_id: str) -> bool:
    return bool(lock and lock.get("id") == instance_id)


def _read() -> dict | None:
    try:
        from . import github_store
        content = github_store.read(_LOCK_PATH)
        return json.loads(content) if content else None
    except Exception as e:
        log.debug("instance: lock read failed: %s", e)
        return None


def claim() -> str:
    # Tolerant: if write fails, returns id anyway and guard degrades to no-lock mode.
    instance_id = uuid.uuid4().hex
    if _disabled():
        log.info("instance: lock disabled (MONOGRAM_NO_INSTANCE_LOCK=1)")
        return instance_id

    payload = {
        "id": instance_id,
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "started_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    prior = _read()
    if prior and prior.get("id") and prior.get("id") != instance_id:
        log.info(
            "instance: taking over from %s@%s (started %s)",
            str(prior.get("id"))[:8], prior.get("host", "?"), prior.get("started_at", "?"),
        )
    try:
        from . import github_store
        github_store.write(
            _LOCK_PATH,
            json.dumps(payload, indent=2),
            f"monogram: instance claim {instance_id[:8]} @ {payload['host']}",
        )
        log.info("instance: claimed %s @ %s", instance_id[:8], payload["host"])
    except Exception as e:
        log.warning("instance: claim write failed (running without lock): %s", e)
    return instance_id


async def guard(instance_id: str) -> None:
    # When lock is disabled, block forever so it never triggers FIRST_COMPLETED shutdown.
    if _disabled():
        while True:
            await asyncio.sleep(3600)

    loop = asyncio.get_event_loop()
    while True:
        await asyncio.sleep(_CHECK_INTERVAL_SECONDS)
        lock = await loop.run_in_executor(None, _read)
        if lock is None:
            continue  # transient read failure; keep running
        if not _is_owner(lock, instance_id):
            log.warning(
                "instance: superseded by %s@%s — stepping down",
                str(lock.get("id"))[:8], lock.get("host", "?"),
            )
            return
