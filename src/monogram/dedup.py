"""Cross-instance dedup: atomic create_file claim — 422 means already claimed by another instance."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

log = logging.getLogger("monogram.dedup")


def _claim_path(key: str) -> str:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f".monogram/seen/{day}/{key}"


def claim(key: str) -> bool:
    """True if this instance won the claim (skip the drop if False)."""
    # Transient errors return True — a claim hiccup must never silently drop a message.
    from github.GithubException import GithubException

    from . import github_store

    try:
        github_store._repo().create_file(_claim_path(key), "monogram: dedup claim", "")
        return True
    except GithubException as e:
        if getattr(e, "status", None) in (409, 422):
            log.info("dedup: %s already claimed; skipping", key)
            return False
        log.warning("dedup: claim failed (%s); processing anyway", e)
        return True
    except Exception as e:
        log.warning("dedup: claim error (%s); processing anyway", e)
        return True


def release(key: str) -> None:
    """Delete a claim so a drop that failed to commit can be re-dropped."""
    from . import github_store

    path = _claim_path(key)
    try:
        repo = github_store._repo()
        repo.delete_file(path, "monogram: dedup release", repo.get_contents(path).sha)
    except Exception as e:
        log.debug("dedup: release failed for %s: %s", key, e)
