"""Pending write-gate queue for MCP tools (v0.5.1 fix).

External MCP clients should never silently rewrite user data. When a
write-type tool is called, it enqueues a pending entry with a token and
pushes an approval prompt to Telegram. The bot's /approve_<token> handler
pops and executes.

v0.5.1 fix: queue lives in the GitHub vault (`.monogram/pending/<token>.json`),
not process-local memory. This is necessary because:
- `monogram mcp-serve` is a stdio subprocess spawned by Claude Desktop / Cursor
  (usually on the user's laptop)
- `monogram run` is a separate long-lived process, often on a different host
  (GCP VM, etc.)
- Module-scope Python state doesn't cross processes; the earlier in-memory
  queue silently lost every approval.

Trade-off: each pending write creates a transient commit. The entry file
is deleted on approve/deny or when expired (TTL 5 min). Users wanting the
`.monogram/` directory hidden from Obsidian search add it to their vault's
`.gitignore`.

Token entropy: `secrets.token_urlsafe(16)` = 128 bits (not 32).
"""
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
    """Create a pending entry backed by a GitHub commit. Returns the entry."""
    import time
    token = secrets.token_urlsafe(16)  # 128-bit entropy, ~22 chars URL-safe
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
    """Atomic consume: read + delete. Returns None if missing/expired."""
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
    # Delete regardless of expiry — consumed tokens are consumed tokens.
    _delete_quiet(token)
    if entry.expired():
        return None
    return entry


def peek_pending(token: str) -> PendingEntry | None:
    """View without consuming. Auto-removes expired entries."""
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
    """Best-effort removal of the pending file. Swallow errors."""
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
    """Cheap sanity check — URL-safe base64 only."""
    if len(token) < 8 or len(token) > 64:
        return False
    return all(c.isalnum() or c in "-_" for c in token)


def _reset_for_tests() -> None:
    """Test-only helper. No-op at module level; per-test patching of
    github_store handles state isolation now."""
    pass
