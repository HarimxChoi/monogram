"""v0.2.3 — GitHub commit digest.

Fetches commits from each watched repo since the last digest time,
aggregates them into daily/YYYY-MM-DD/commits.md. Morning job reads
this file to populate scheduler project activity.

PAT requirements:
  Fine-grained PAT must include `metadata: read` + `contents: read`
  for every repo listed in MONOGRAM_WATCH_REPOS.
  If a repo returns 404/403, it's logged to log/unattributed.md
  instead of failing the whole digest.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from github import Github
from github.Auth import Token
from github.GithubException import GithubException

from . import github_store
from .config import load_config

config = load_config()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _watch_repos() -> list[str]:
    """Return the comma-separated watched-repos list, excluding scheduler itself."""
    raw = config.monogram_watch_repos or ""
    return [r.strip() for r in raw.split(",") if r.strip()]


def _fetch_commits_since(full_name: str, since: datetime) -> list[dict]:
    """Return a list of {sha, time, author, message} for commits in `full_name`."""
    g = Github(auth=Token(config.github_pat))
    repo = g.get_repo(full_name)
    commits = repo.get_commits(since=since)
    out: list[dict] = []
    for c in commits:
        commit = c.commit
        out.append(
            {
                "sha": c.sha[:7],
                "time": commit.author.date.strftime("%Y-%m-%d %H:%M"),
                "author": commit.author.name,
                "message": commit.message.split("\n", 1)[0][:120],
                "repo": full_name,
            }
        )
    return out


def _format_commits_block(commits: list[dict]) -> str:
    """Render commits as markdown lines grouped by repo."""
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
    """Fetch commits from watched repos in the last N hours, commit to daily/.

    Returns {"repos_fetched": N, "commits": N, "skipped": [...], "errors": [...]}.
    """
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

    if errors:
        # Surface errors into an unattributed log so user can fix PAT scope.
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
        "skipped": [],
        "errors": errors,
    }
