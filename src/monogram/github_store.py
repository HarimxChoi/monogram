"""GitHub-backed markdown store with YAML frontmatter metadata."""
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
    try:
        return _repo().get_contents(path).decoded_content.decode()
    except UnknownObjectException:
        return ""
    except GithubException as e:
        if getattr(e, "status", None) == 404:
            return ""
        raise


def write(path: str, content: str, message: str) -> bool:
    repo = _repo()
    try:
        existing = repo.get_contents(path)
        repo.update_file(path, message, content, existing.sha)
        return True
    except (UnknownObjectException, GithubException) as e:
        if isinstance(e, GithubException) and getattr(e, "status", None) != 404:
            print(f"github_store.write error: {e}")
            return False
        try:
            repo.create_file(path, message, content)
            return True
        except GithubException as inner:
            print(f"github_store.write create error: {inner}")
            return False


def write_multi(writes: dict[str, str], message: str) -> bool:
    """Sequential per-path writes — not atomic; partial failure is possible."""
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
    current = read(path)
    updated = f"{current}\n{line}" if current else line
    return write(path, updated, commit_msg)


def write_atomic(
    writes: dict[str, str],
    message: str,
    max_retries: int = 3,
) -> bool:
    """All files in one commit via Git Tree API; retries on 422 ref.edit race."""
    if not writes:
        return True

    from github import InputGitTreeElement

    repo = _repo()
    default_branch = repo.default_branch
    ref_name = f"heads/{default_branch}"

    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            # Refetch tip each retry to get fresh parent SHA.
            ref = repo.get_git_ref(ref_name)
            parent_commit = repo.get_git_commit(ref.object.sha)
            base_tree = parent_commit.tree

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

            new_tree = repo.create_git_tree(tree_elements, base_tree=base_tree)
            new_commit = repo.create_git_commit(
                message, new_tree, [parent_commit]
            )

            try:
                ref.edit(new_commit.sha)
                return True
            except GithubException as e:
                # 422 = concurrent writer won the ref.edit race; retry.
                if _is_fast_forward_conflict(e):
                    last_error = e
                    if attempt < max_retries:
                        continue
                    print(
                        f"github_store.write_atomic: exhausted {max_retries} "
                        f"retries on ref.edit 422 for {len(writes)} files"
                    )
                    return False
                raise

        except GithubException as e:
            last_error = e
            print(f"github_store.write_atomic attempt {attempt} error: {e}")
            if attempt == max_retries:
                return False

    print(f"github_store.write_atomic exhausted retries: {last_error}")
    return False


def _is_fast_forward_conflict(exc: GithubException) -> bool:
    """All 422s are retryable — refetching parent SHA is correct regardless of wording."""
    return getattr(exc, "status", None) == 422


def parse_metadata(content: str) -> tuple[dict, str]:
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
