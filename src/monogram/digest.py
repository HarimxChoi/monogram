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

import re
from datetime import datetime, timedelta, timezone
from functools import cache

from github import Github
from github.Auth import Token
from github.GithubException import GithubException

from . import github_store
from .config import load_config


@cache
def _cfg():
    """Lazy app-config accessor — defers .env loading until first use."""
    return load_config()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _watch_repos() -> list[str]:
    """Return the comma-separated watched-repos list, excluding scheduler itself."""
    raw = _cfg().monogram_watch_repos or ""
    return [r.strip() for r in raw.split(",") if r.strip()]


# Canonical Conventional Commits prefixes plus a catch-all. Order
# matters only for display — `_CONV_ORDER` drives render grouping.
_CONV_ORDER = (
    "feat", "fix", "perf", "refactor", "test",
    "docs", "build", "ci", "chore", "style", "revert", "other",
)
_CONV_RE = re.compile(
    r"^(?P<type>feat|fix|docs|chore|refactor|test|style|perf|build|ci|revert)"
    r"(?:\([^)]*\))?(?P<bang>!)?:\s*(?P<subject>.+)$",
    re.IGNORECASE,
)


def _parse_conventional(first_line: str) -> tuple[str, str, bool]:
    """Return (type, subject, breaking). `type` is one of _CONV_ORDER.

    `breaking` is true when the subject carries a `!` between the type
    and the colon (e.g. `feat!: drop Python 3.9`). Body-level
    `BREAKING CHANGE:` footers are handled separately in _render.
    """
    m = _CONV_RE.match((first_line or "").strip())
    if not m:
        return "other", (first_line or "").strip(), False
    return m.group("type").lower(), m.group("subject").strip(), bool(m.group("bang"))


def _fetch_commits_since(full_name: str, since: datetime) -> list[dict]:
    """Return structured commit records for `full_name` since `since`.

    Upgraded from first-line-only capture: also records the full body
    (up to 2 KiB) and detects BREAKING CHANGE / breaking-bang markers.
    Downstream callers stay backward-compatible via the `message` key,
    which still carries just the truncated subject (preserves the
    line-level format `_commits_for_project` relies on).
    """
    g = Github(auth=Token(_cfg().github_pat))
    repo = g.get_repo(full_name)
    commits = repo.get_commits(since=since)
    out: list[dict] = []
    for c in commits:
        commit = c.commit
        full_msg = commit.message or ""
        first_line, _, rest = full_msg.partition("\n")
        body = rest.strip()
        conv_type, subject, breaking_bang = _parse_conventional(first_line)
        breaking_footer = "BREAKING CHANGE" in body.upper()
        out.append({
            "sha": c.sha[:7],
            "time": commit.author.date.strftime("%Y-%m-%d %H:%M"),
            "author": commit.author.name,
            "message": first_line[:120],        # compat: line-format consumers
            "subject": subject[:160],           # conv-parsed display subject
            "body": body[:2048],
            "type": conv_type,
            "breaking": breaking_bang or breaking_footer,
            "repo": full_name,
        })
    return out


# Short, human labels for grouped rendering. The key order drives the
# display order within a repo block.
_CONV_LABEL = {
    "feat":     "Features",
    "fix":      "Fixes",
    "perf":     "Performance",
    "refactor": "Refactors",
    "test":     "Tests",
    "docs":     "Docs",
    "build":    "Build",
    "ci":       "CI",
    "chore":    "Chores",
    "style":    "Style",
    "revert":   "Reverts",
    "other":    "Other",
}


def _format_commits_block(commits: list[dict]) -> str:
    """Render commits as markdown, grouped by repo and then by
    conventional-commit type.

    Per-commit line format is preserved (`` - `sha` time [author] msg ``)
    so `morning_job._commits_for_project` continues to filter them by
    substring match. Typed sub-groupings sit above the lines as
    `**Label (N)**` headers — those are cosmetic and don't affect the
    filter, which is line-oriented.

    Commits flagged `breaking` are prefixed with `⚠ ` and repeated in a
    trailing `**Breaking changes**` block for scan-ability.
    """
    if not commits:
        return ""
    by_repo: dict[str, list[dict]] = {}
    for c in commits:
        by_repo.setdefault(c["repo"], []).append(c)

    blocks: list[str] = []
    for repo, items in sorted(by_repo.items()):
        lines = [f"### {repo} ({len(items)})"]

        # Bucket commits by conventional type while preserving order.
        # `.get()` lets callers pass thin dicts (from older test cases
        # or direct API use) that predate the enriched capture format.
        by_type: dict[str, list[dict]] = {}
        for c in items:
            by_type.setdefault(c.get("type", "other"), []).append(c)

        for conv_type in _CONV_ORDER:
            bucket = by_type.get(conv_type)
            if not bucket:
                continue
            lines.append("")
            lines.append(f"**{_CONV_LABEL[conv_type]} ({len(bucket)})**")
            for c in bucket:
                flag = "⚠ " if c.get("breaking") else ""
                lines.append(
                    f"- `{c['sha']}` {c['time']} [{c['author']}] "
                    f"{flag}{c['message']}"
                )

        # Call out breaking changes in a dedicated block. Duplicates
        # the entries above — redundancy here is worth the scan time
        # saved when triaging a morning with a breaking change buried
        # among 40 other commits.
        breakers = [c for c in items if c.get("breaking")]
        if breakers:
            lines.append("")
            lines.append("**Breaking changes**")
            for c in breakers:
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
