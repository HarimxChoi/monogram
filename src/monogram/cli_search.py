"""Vault search (ripgrep primary, Python fallback). shell=False+argv list prevents injection; fixed-string default (-F) prevents ReDoS."""
from __future__ import annotations

import base64
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
    base = Path.home() / ".cache" / "monogram" / "vault"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _refresh_vault_cache(max_age_minutes: int = 60) -> Path:
    """Uses GitHub API (not git clone) so search works without a local git binary."""
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
        dest = cache / element.path
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            # get_git_blob handles up to 100 MB; get_contents silently fails above 1 MB
            blob = repo.get_git_blob(element.sha)
            dest.write_bytes(base64.b64decode(blob.content))
        except Exception as e:
            log.debug("search: skipped %s (%s)", element.path, e)

    marker.touch()
    return cache


def _scope_filter(path: Path, kind: str | None, include_raw: bool) -> bool:
    # as_posix() so "/" prefix checks work on Windows
    rel = path.as_posix().lstrip("/")
    if not include_raw and rel.startswith("raw/"):
        return False
    if kind is None:
        return True
    return rel.startswith(f"{kind}/")


def _since_filter(path: Path, since: str | None) -> bool:
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
        # shell=False + argv list; query is a single item, never interpolated
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
        return

    if proc.returncode not in (0, 1):  # 1 = no matches, still OK
        log.warning("search: ripgrep failed: %s", proc.stderr[:200])
        return

    for line in proc.stdout.splitlines():
        if since:
            # ripgrep output: <path>:<linenum>:<content>
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
                        rel = path.relative_to(vault_dir).as_posix()
                        yield f"{rel}:{lineno}:{line.rstrip()}"
        except OSError:
            continue


def _run_graph(query: str, limit: int, hops: int = 1) -> None:
    import asyncio

    from . import graph_search

    results = asyncio.run(graph_search.graph_search(query, k=limit, hops=hops))
    if not results:
        click.echo(f"No graph hits for: {query}  (run `monogram reindex` + `monogram graph` first)")
        return
    for r in results:
        lbl = f" — {r['label']}" if r.get("label") else ""
        click.echo(f"{r['score']:>6.3f}  {r['path']}{lbl}")
        for nb in r.get("neighborhood", []):
            click.echo(f"          ─{nb['predicate']}→ {nb['node']}")
    click.echo(f"\n({len(results)} graph hits)")


def _run_semantic(query: str, kind: str | None, limit: int, rerank: bool = False) -> None:
    import asyncio

    from . import semantic_index

    areas = [kind] if kind else None
    hits = asyncio.run(semantic_index.semantic_search(
        query, k=limit, areas=areas, rerank=rerank or None
    ))
    if not hits:
        click.echo(f"No semantic hits for: {query}  (run `monogram reindex` if the index is empty)")
        return
    for h in hits:
        head = f" — {h['heading']}" if h.get("heading") else ""
        click.echo(f"{h['score']:>8.0f}  {h['path']}{head}")
        if h.get("excerpt"):
            click.echo(f"          {h['excerpt']}")
    click.echo(f"\n({len(hits)} semantic hits)")


@click.command(name="search")
@click.argument("query", required=True)
@click.option("--kind", type=click.Choice(["wiki", "life", "daily", "scheduler", "identity", "reports"]), default=None, help="Restrict to one vault section.")
@click.option("--since", default=None, help="Recency filter (7d, 24h, or YYYY-MM-DD).")
@click.option("--raw", "include_raw", is_flag=True, help="Include raw/ tier (excluded by default).")
@click.option("--regex", is_flag=True, help="Treat QUERY as regex (default: fixed-string).")
@click.option("--semantic", is_flag=True, help="Meaning-based hybrid search over the vector index (run `monogram reindex` first).")
@click.option("--rerank", is_flag=True, help="Cross-encoder rerank the top hits (needs [semantic-rerank]).")
@click.option("--graph", "graph_mode", is_flag=True, help="Graph-aware search: semantic seed → PageRank over the event graph → connected neighborhood.")
@click.option("--hops", type=int, default=1, help="Neighborhood depth for --graph (1 or 2).")
@click.option("--limit", type=int, default=50, help="Max hits to display.")
def search_cmd(query, kind, since, include_raw, regex, semantic, rerank, graph_mode, hops, limit):
    """Search the vault. Lexical (ripgrep/Python) by default, or semantic / graph.

    Examples:

        monogram search "pose estimation"
        monogram search "Q3 goals" --kind scheduler
        monogram search "how do I deploy the model" --semantic
        monogram search "the model deployment work" --graph
    """
    if graph_mode:
        _run_graph(query, limit, hops)
        return
    if semantic:
        _run_semantic(query, kind, limit, rerank)
        return

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
