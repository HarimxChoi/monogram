"""`monogram search` — vault search via ripgrep, with Python regex
fallback when rg isn't on PATH.

Design:
  - ripgrep primary (2-10× faster than grep, respects .gitignore,
    handles gitignore-style patterns natively)
  - Python re fallback uses a local cache of the vault (auto-refreshed
    on stale). Works in pure-pip installs.
  - Scopes via --kind (wiki/life/daily/raw), --since (relative or
    absolute), --raw (include raw/ tier)

Command injection defense:
  - subprocess.run with shell=False and argv list (never shell=True)
  - user query is passed as a single argv item; ripgrep treats it as a
    literal PATTERN, not a shell argument
  - no os.system, no subprocess.Popen with shell=True anywhere

ReDoS defense:
  - We don't enable regex mode by default. Queries are fixed strings
    (ripgrep's -F flag). User has to opt in with --regex.
  - When --regex is enabled, ripgrep itself uses a finite automaton
    engine (not backtracking) — safe against classical ReDoS.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import click

log = logging.getLogger("monogram.search")


def _vault_cache_dir() -> Path:
    """Local cache location for the vault clone (used by Python fallback)."""
    base = Path.home() / ".cache" / "monogram" / "vault"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _refresh_vault_cache(max_age_minutes: int = 60) -> Path:
    """Download vault files to ~/.cache/monogram/vault/ if stale.

    Uses the GitHub API (same PAT as production) — not git clone.
    This lets monogram search work without a local git binary.
    Stale threshold: 60 minutes by default.
    """
    from . import github_store

    cache = _vault_cache_dir()
    marker = cache / ".last_refresh"
    now = datetime.now(timezone.utc)

    if marker.exists():
        last = datetime.fromtimestamp(marker.stat().st_mtime, tz=timezone.utc)
        if (now - last) < timedelta(minutes=max_age_minutes):
            return cache

    log.info("search: refreshing vault cache")
    repo = github_store._repo()
    branch = repo.get_branch(repo.default_branch)
    tree = repo.get_git_tree(branch.commit.sha, recursive=True)

    for element in tree.tree:
        if element.type != "blob":
            continue
        try:
            raw = repo.get_contents(element.path)
            dest = cache / element.path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(raw.decoded_content)
        except Exception as e:
            log.debug("search: skipped %s (%s)", element.path, e)

    marker.touch()
    return cache


def _scope_filter(path: Path, kind: str | None, include_raw: bool) -> bool:
    """Return True if path should be searched under the given scope."""
    rel = str(path).lstrip("/")
    if not include_raw and rel.startswith("raw/"):
        return False
    if kind is None:
        return True
    return rel.startswith(f"{kind}/")


def _since_filter(path: Path, since: str | None) -> bool:
    """True iff file mtime is within `since`. Accepts '7d', '30d',
    '2026-04-01' (absolute). None = no filter."""
    if since is None:
        return True
    now = datetime.now(timezone.utc)
    try:
        if since.endswith("d"):
            cutoff = now - timedelta(days=int(since[:-1]))
        elif since.endswith("h"):
            cutoff = now - timedelta(hours=int(since[:-1]))
        else:
            cutoff = datetime.fromisoformat(since).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return True  # unparseable — don't filter

    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return True
    return mtime >= cutoff


def _search_via_ripgrep(
    vault_dir: Path,
    query: str,
    kind: str | None,
    since: str | None,
    include_raw: bool,
    regex: bool,
) -> Iterator[str]:
    """Yield hit lines formatted "path:line_num:content"."""
    scope = str(vault_dir / kind) if kind else str(vault_dir)

    cmd = ["rg", "--no-heading", "--line-number", "--color=never"]
    if not regex:
        cmd.append("--fixed-strings")
    if not include_raw:
        cmd.append("--glob=!raw/**")
    # Limit to markdown + jsonl (skip binaries)
    cmd += ["--glob=*.md", "--glob=*.jsonl"]
    cmd += ["--", query, scope]

    try:
        # shell=False, argv list, no string interpolation — safe
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        log.warning("search: ripgrep timed out after 30s")
        return
    except FileNotFoundError:
        # rg not installed — caller should have caught this
        return

    if proc.returncode not in (0, 1):  # 1 = no matches, still OK
        log.warning("search: ripgrep failed: %s", proc.stderr[:200])
        return

    # Optional since-filter (ripgrep doesn't do mtime)
    for line in proc.stdout.splitlines():
        if since:
            # Extract path from ripgrep output: <path>:<linenum>:<content>
            parts = line.split(":", 2)
            if len(parts) >= 1 and not _since_filter(Path(parts[0]), since):
                continue
        yield line


def _search_via_python_re(
    vault_dir: Path,
    query: str,
    kind: str | None,
    since: str | None,
    include_raw: bool,
    regex: bool,
) -> Iterator[str]:
    """Pure-Python fallback. Slower, but zero external deps."""
    pattern: re.Pattern | None = None
    if regex:
        try:
            pattern = re.compile(query)
        except re.error as e:
            log.warning("search: invalid regex: %s", e)
            return

    for path in vault_dir.rglob("*.md"):
        if not _scope_filter(path.relative_to(vault_dir), kind, include_raw):
            continue
        if not _since_filter(path, since):
            continue
        try:
            with path.open(encoding="utf-8", errors="replace") as f:
                for lineno, line in enumerate(f, 1):
                    hit = (
                        pattern.search(line) is not None
                        if pattern
                        else query in line
                    )
                    if hit:
                        rel = path.relative_to(vault_dir)
                        yield f"{rel}:{lineno}:{line.rstrip()}"
        except OSError:
            continue


@click.command(name="search")
@click.argument("query", required=True)
@click.option("--kind", type=click.Choice(["wiki", "life", "daily", "scheduler", "identity", "reports"]), default=None, help="Restrict to one vault section.")
@click.option("--since", default=None, help="Recency filter (7d, 24h, or YYYY-MM-DD).")
@click.option("--raw", "include_raw", is_flag=True, help="Include raw/ tier (excluded by default).")
@click.option("--regex", is_flag=True, help="Treat QUERY as regex (default: fixed-string).")
@click.option("--limit", type=int, default=50, help="Max hits to display.")
def search_cmd(query, kind, since, include_raw, regex, limit):
    """Search the vault. Uses ripgrep if available, else pure Python.

    Examples:

        monogram search "pose estimation"
        monogram search "Q3 goals" --kind scheduler
        monogram search "ran into bug" --since 7d
    """
    vault_dir = _refresh_vault_cache()

    use_rg = shutil.which("rg") is not None
    impl = _search_via_ripgrep if use_rg else _search_via_python_re

    hits = 0
    for line in impl(vault_dir, query, kind, since, include_raw, regex):
        click.echo(line)
        hits += 1
        if hits >= limit:
            click.echo(f"... (limit reached, {limit} hits shown)")
            break

    if hits == 0:
        click.echo(f"No hits for: {query}")
    elif hits < limit:
        click.echo(f"\n({hits} hits)")

    if not use_rg:
        click.echo(
            "\nTip: install `ripgrep` for ~5× faster search: "
            "https://github.com/BurntSushi/ripgrep#installation",
            err=True,
        )
