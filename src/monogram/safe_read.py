"""Read gate — wraps github_store.read() with never-read path enforcement.

Used by context-loading code (morning_job, weekly_job, digest, pipeline)
to prevent credential-containing files from ever entering the LLM context.

Classifier and Extractor operate on user-supplied drop text only (they do
NOT read from repo), so they don't need this wrapper. But anything that
calls github_store.read(path) to assemble context MUST go through this.
"""
from __future__ import annotations

import logging

from . import github_store
from .vault_config import load_vault_config

log = logging.getLogger("monogram.safe_read")


def is_blocked(path: str) -> bool:
    """Return True if `path` is on the effective never-read list."""
    if not path:
        return False
    cfg = load_vault_config()
    return any(path.startswith(p) for p in cfg.effective_never_read)


def safe_read(path: str) -> str:
    """Drop-in replacement for github_store.read() that respects
    never_read_paths.

    Returns empty string for blocked paths so callers see "no content"
    and preserve their existing control flow.
    """
    if is_blocked(path):
        log.info("safe_read: blocked %s (credential or user-configured)", path)
        return ""
    return github_store.read(path)
