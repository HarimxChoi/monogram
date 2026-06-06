"""Credential never-read gate — context loaders MUST use this instead of github_store.read()."""
from __future__ import annotations

import logging
import posixpath

from . import github_store
from .vault_config import load_vault_config

log = logging.getLogger("monogram.safe_read")


def is_blocked(path: str) -> bool:
    if not path:
        return False
    cfg = load_vault_config()
    # Normalize to block path-traversal bypasses like life/./credentials/x.
    norm = posixpath.normpath(path)
    for p in cfg.effective_never_read:
        pref = posixpath.normpath(p)
        if norm == pref or norm.startswith(pref + "/"):
            return True
    return False


def safe_read(path: str) -> str:
    """Returns empty string for blocked paths so callers preserve their control flow."""
    if is_blocked(path):
        log.info("safe_read: blocked %s (credential or user-configured)", path)
        return ""
    return github_store.read(path)
