"""MCP write-approval queue; persisted to GitHub vault so mcp-serve and monogram run (separate processes/hosts) share state."""
from __future__ import annotations

import json
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from . import github_store

log = logging.getLogger("monogram.mcp_pending")

_TTL_SECONDS = 300  # 5 minutes
_PENDING_DIR = ".monogram/pending"


def _path(token: str) -> str:
    return f"{_PENDING_DIR}/{token}.json"


@dataclass
class PendingEntry:
    token: str
    kind: str
    payload: Any
    preview: str
    expires_at: float  # unix seconds

    def expired(self, now: float | None = None) -> bool:
        import time
        return (now or time.time()) >= self.expires_at

    @classmethod
    def from_dict(cls, data: dict) -> "PendingEntry":
        return cls(
            token=data["token"],
            kind=data["kind"],
            payload=data["payload"],
            preview=data.get("preview", ""),
            expires_at=data["expires_at"],
        )

    def to_dict(self) -> dict:
        return {
            "token": self.token,
            "kind": self.kind,
            "payload": self.payload,
            "preview": self.preview,
            "expires_at": self.expires_at,
        }


def new_pending(kind: str, payload: Any, preview: str = "") -> PendingEntry:
    import time
    token = secrets.token_urlsafe(16)  # 128-bit entropy; not 32-bit
    entry = PendingEntry(
        token=token,
        kind=kind,
        payload=payload,
        preview=preview,
        expires_at=time.time() + _TTL_SECONDS,
    )
    try:
        github_store.write(
            _path(token),
            json.dumps(entry.to_dict(), indent=2, default=str),
            f"monogram: pending {kind} [{token[:8]}]",
        )
    except Exception as e:
        log.warning("new_pending: github_store.write failed: %s", e)
        raise
    return entry


def pop_pending(token: str) -> PendingEntry | None:
    if not token or not _looks_like_token(token):
        return None
    content = github_store.read(_path(token))
    if not content:
        return None
    try:
        data = json.loads(content)
    except Exception:
        _delete_quiet(token)
        return None
    entry = PendingEntry.from_dict(data)
    # Consumed tokens are always deleted, even if expired.
    _delete_quiet(token)
    if entry.expired():
        return None
    return entry


def peek_pending(token: str) -> PendingEntry | None:
    if not token or not _looks_like_token(token):
        return None
    content = github_store.read(_path(token))
    if not content:
        return None
    try:
        data = json.loads(content)
    except Exception:
        _delete_quiet(token)
        return None
    entry = PendingEntry.from_dict(data)
    if entry.expired():
        _delete_quiet(token)
        return None
    return entry


def _delete_quiet(token: str) -> None:
    try:
        repo = github_store._repo()
        gh_entry = repo.get_contents(_path(token))
        repo.delete_file(
            _path(token),
            f"monogram: consumed pending [{token[:8]}]",
            gh_entry.sha,
        )
    except Exception as e:
        log.debug("pending file delete failed for %s: %s", token[:8], e)


def _looks_like_token(token: str) -> bool:
    if len(token) < 8 or len(token) > 64:
        return False
    return all(c.isalnum() or c in "-_" for c in token)


def _reset_for_tests() -> None:
    """Test-only no-op; state isolation is done via per-test github_store patching."""
    pass
