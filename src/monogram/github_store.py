"""GitHub-backed markdown store with YAML frontmatter metadata.

Design rules (see docs/architecture.md for sourcing):
- Git history is the audit trail. Every write carries a commit message.
- Metadata is per-page YAML frontmatter (confidence enum, sources, timestamps, tags).
- YAML supersession fields deferred to v2.0.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

import yaml
from github import Auth, Github
from github.GithubException import GithubException, UnknownObjectException

from .config import load_config

log = logging.getLogger("monogram.github_store")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@lru_cache(maxsize=1)
def _repo():
    cfg = load_config()
    return Github(auth=Auth.Token(cfg.github_pat)).get_repo(cfg.github_repo)


def read(path: str) -> str:
    """Return file content as a string, empty string if not found."""
    try:
        return _repo().get_contents(path).decoded_content.decode()
    except UnknownObjectException:
        return ""
    except GithubException as e:
        if getattr(e, "status", None) == 404:
            return ""
        raise


def write(path: str, content: str, message: str) -> bool:
    """Create or update a file. Returns True on success."""
    repo = _repo()
    try:
        existing = repo.get_contents(path)
        repo.update_file(path, message, content, existing.sha)
        return True
    except (UnknownObjectException, GithubException) as e:
        if isinstance(e, GithubException) and getattr(e, "status", None) != 404:
            log.error("write %s: %s", path, e)
            return False
        try:
            repo.create_file(path, message, content)
            return True
        except GithubException as inner:
            log.error("write create %s: %s", path, inner)
            return False


def write_multi(writes: dict[str, str], message: str) -> bool:
    """Write multiple files in sequential commits under a shared message prefix.

    NOT truly atomic (each file is a separate API call), but provides
    try/except per-path with a summary. For true atomicity, use the
    Git Tree API (v1.0 upgrade path).

    Returns True if ALL writes succeeded, False if any failed (partial
    state possible — logged for manual recovery).
    """
    failed: list[str] = []
    for path, content in writes.items():
        ok = write(path, content, f"{message} [{path.split('/')[-1]}]")
        if not ok:
            failed.append(path)
    if failed:
        log.error("write_multi: %d failed: %s", len(failed), failed)
        return False
    return True


def append(path: str, line: str, commit_msg: str) -> bool:
    """Append a line to an existing file, or create it if missing."""
    current = read(path)
    updated = f"{current}\n{line}" if current else line
    return write(path, updated, commit_msg)


# ── Atomic multi-file write via Git Tree API ──────────────────────────────
#
# v0.8: write_atomic stages N files into a single commit, eliminating the
# partial-state risk of write_multi. Uses the Git Data API:
#
#   1. create_git_blob per file (N API calls)
#   2. create_git_tree with base_tree = current branch tip's tree
#   3. create_git_commit with parent = current branch tip
#   4. ref.edit (the atomic moment — either takes or 422s)
#
# Total: N+3 API calls. At GitHub's 5000/hour fine-grained PAT limit,
# this is well within budget even with morning_job + user drops racing.
#
# Failure modes:
#   - API transient (network, rate-limit): raised to caller, retry manually
#   - ref.edit 422 "not a fast-forward": concurrent writer got there first;
#     we retry the ENTIRE sequence with a freshly-read parent. Orphan blobs
#     from the failed attempt are collected by GitHub's git GC.
#
# v0.8 default write path. Listener drops, weekly rollup, morning brief
# scaffolding, backup mirror, and `monogram init` all go through this.
# `write_multi` is retained as dead code only — any future caller with
# multi-file writes should use write_atomic instead so partial-state
# failure modes stay impossible by construction.


def write_atomic(
    writes: dict[str, str],
    message: str,
    max_retries: int = 3,
) -> bool:
    """Atomically write multiple files in one commit via Git Tree API.

    Returns True iff ALL files landed in a single commit. Returns False
    after max_retries if concurrent writes keep winning the ref.edit race.

    Empty `writes` is a no-op (returns True without making a commit).
    """
    if not writes:
        return True  # explicit no-op; don't create empty commits

    from github import InputGitTreeElement

    repo = _repo()
    default_branch = repo.default_branch
    ref_name = f"heads/{default_branch}"

    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            # 1. Read current tip (MUST be inside retry loop for freshness)
            ref = repo.get_git_ref(ref_name)
            parent_commit = repo.get_git_commit(ref.object.sha)
            base_tree = parent_commit.tree

            # 2. Create a blob per file (each is one API call)
            tree_elements: list[InputGitTreeElement] = []
            for path, content in writes.items():
                blob = repo.create_git_blob(content, "utf-8")
                tree_elements.append(
                    InputGitTreeElement(
                        path=path,
                        mode="100644",
                        type="blob",
                        sha=blob.sha,
                    )
                )

            # 3. Create tree + commit
            new_tree = repo.create_git_tree(tree_elements, base_tree=base_tree)
            new_commit = repo.create_git_commit(
                message, new_tree, [parent_commit]
            )

            # 4. The atomic moment — either this takes or we retry
            try:
                ref.edit(new_commit.sha)
                log.info(
                    "write_atomic: committed %d files as %s",
                    len(writes), new_commit.sha[:7],
                )
                return True
            except GithubException as e:
                # 422: "Update is not a fast-forward" = someone else
                # pushed while we were staging. Retry with fresh parent.
                # Any other GithubException is non-retryable.
                if _is_fast_forward_conflict(e):
                    last_error = e
                    if attempt < max_retries:
                        log.warning(
                            "write_atomic: ref.edit 422 on attempt %d; retrying",
                            attempt,
                        )
                        continue
                    log.error(
                        "write_atomic: exhausted %d retries on ref.edit 422 "
                        "for %d files",
                        max_retries, len(writes),
                    )
                    return False
                raise

        except GithubException as e:
            last_error = e
            log.error("write_atomic attempt %d error: %s", attempt, e)
            if attempt == max_retries:
                return False
            # Non-422 errors get a single retry; 5xx/transient may succeed

    log.error("write_atomic exhausted retries: %s", last_error)
    return False


def _is_fast_forward_conflict(exc: GithubException) -> bool:
    """True if the ref.edit failure looks like a concurrent-writer race.

    GitHub returns 422 for ref.edit with wording like "Update is not a
    fast-forward" or "not a fast forward". Rather than grep for exact
    strings (brittle across API wording changes), treat all 422s on
    ref.edit as retryable — the retry refetches the parent SHA, which
    is the correct response regardless of the specific 422 reason.

    Non-422 exceptions fall through to the caller's outer handler.
    """
    return getattr(exc, "status", None) == 422


# ── Metadata helpers ──────────────────────────────────────────────────────────


def parse_metadata(content: str) -> tuple[dict, str]:
    """Split YAML frontmatter from body. Returns (metadata, body)."""
    if not content.startswith("---\n"):
        return {}, content
    try:
        _, frontmatter, body = content.split("---\n", 2)
        return yaml.safe_load(frontmatter) or {}, body
    except ValueError:
        return {}, content


def build_metadata(
    confidence: str = "medium",
    sources: int = 1,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    now = _now_iso()
    return {
        "confidence": confidence,
        "sources": sources,
        "created": now,
        "last_accessed": now,
        "last_confirmed": now,
        "tags": tags or [],
    }


def serialize_with_metadata(metadata: dict, body: str) -> str:
    return f"---\n{yaml.dump(metadata, default_flow_style=False, sort_keys=False)}---\n\n{body}"
