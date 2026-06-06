"""GitHub commit digest — fetches watched repos and writes daily/YYYY-MM-DD/commits.md."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from github import Github
from github.Auth import Token
from github.GithubException import GithubException

from . import github_store
from .commit_parse import parse_commit
from .config import load_config
from .secret_filter import redact

config = load_config()

_MAX_FILES = 50  # cap captured file paths per commit so the sidecar stays small


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _watch_repos() -> list[str]:
    raw = config.monogram_watch_repos or ""
    return [r.strip() for r in raw.split(",") if r.strip()]


def _enrich_commit(c, full_name: str) -> dict:
    commit = c.commit
    parsed = parse_commit(commit.message)
    try:
        parents = [p.sha[:7] for p in c.parents]
    except Exception:
        parents = []
    files: list[dict] = []
    try:
        for f in (c.files or [])[:_MAX_FILES]:
            files.append({
                "path": redact(f.filename or ""),
                "additions": getattr(f, "additions", 0),
                "deletions": getattr(f, "deletions", 0),
                "status": getattr(f, "status", ""),
            })
    except Exception:
        files = []
    return {
        "sha": c.sha[:7],
        "full_sha": c.sha,
        "time": commit.author.date.strftime("%Y-%m-%d %H:%M"),
        "author": commit.author.name,
        "message": redact(commit.message.split("\n", 1)[0][:120]),
        "full_message": redact(commit.message),
        "repo": full_name,
        "parents": parents,
        "is_merge": len(parents) > 1,
        "files": files,
        "type": parsed.type,
        "scope": parsed.scope,
        "breaking": parsed.breaking,
        "issues": parsed.issues,
        "co_authors": [redact(ca) for ca in parsed.co_authors],
    }


def _fetch_commits_since(full_name: str, since: datetime) -> list[dict]:
    g = Github(auth=Token(config.github_pat))
    repo = g.get_repo(full_name)
    return [_enrich_commit(c, full_name) for c in repo.get_commits(since=since)]


def _write_commit_sidecar(today: str, commits: list[dict]) -> int:
    """Merge into commits.jsonl deduped by full_sha; returns count of new records."""
    if not commits:
        return 0
    path = f"daily/{today}/commits.jsonl"
    existing = github_store.read(path)
    seen: set[str] = set()
    for line in existing.splitlines():
        try:
            seen.add(json.loads(line).get("full_sha"))
        except json.JSONDecodeError:
            continue
    new = [json.dumps(c, ensure_ascii=False) for c in commits if c.get("full_sha") not in seen]
    if not new:
        return 0
    merged = (existing.rstrip() + "\n" if existing.strip() else "") + "\n".join(new) + "\n"
    github_store.write(path, merged, f"monogram digest: +{len(new)} commit records")
    return len(new)


def _format_commits_block(commits: list[dict]) -> str:
    if not commits:
        return ""
    by_repo: dict[str, list[dict]] = {}
    for c in commits:
        by_repo.setdefault(c["repo"], []).append(c)

    blocks: list[str] = []
    for repo, items in sorted(by_repo.items()):
        lines = [f"### {repo}"]
        for c in items:
            lines.append(
                f"- `{c['sha']}` {c['time']} [{c['author']}] {c['message']}"
            )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


async def run_digest(since_hours: int = 24) -> dict:
    repos = _watch_repos()
    today = _today()
    since = datetime.now(timezone.utc) - timedelta(hours=since_hours)

    if not repos:
        return {
            "repos_fetched": 0,
            "commits": 0,
            "skipped": [],
            "errors": ["MONOGRAM_WATCH_REPOS not set — digest no-op"],
        }

    all_commits: list[dict] = []
    errors: list[str] = []
    for full_name in repos:
        try:
            all_commits.extend(_fetch_commits_since(full_name, since))
        except GithubException as e:
            errors.append(f"{full_name}: {getattr(e, 'status', '?')} {e.data}")
        except Exception as e:
            errors.append(f"{full_name}: {type(e).__name__}: {e}")

    block = _format_commits_block(all_commits)
    if not block:
        block = f"_(no commits in last {since_hours}h across {len(repos)} watched repos)_"

    path = f"daily/{today}/commits.md"
    existing = github_store.read(path)
    run_stamp = datetime.now(timezone.utc).strftime("%H:%M UTC")
    section = f"\n## Digest @ {run_stamp} (last {since_hours}h)\n\n{block}\n"
    content = f"{existing.rstrip()}\n{section}" if existing else f"# Commits — {today}\n{section}"

    github_store.write(path, content, f"monogram digest: {len(all_commits)} commits")

    records_added = _write_commit_sidecar(today, all_commits)

    if errors:
        err_log = "\n".join(
            [f"- {datetime.now(timezone.utc).isoformat()}  {e}" for e in errors]
        )
        existing_err = github_store.read("log/unattributed.md")
        merged = f"{existing_err.rstrip()}\n{err_log}\n" if existing_err else err_log + "\n"
        github_store.write(
            "log/unattributed.md",
            merged,
            f"monogram digest: {len(errors)} errors",
        )

    return {
        "repos_fetched": len(repos) - len(errors),
        "commits": len(all_commits),
        "records_added": records_added,
        "skipped": [],
        "errors": errors,
    }
