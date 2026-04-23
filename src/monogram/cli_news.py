"""`monogram news *` CLI subgroup.

Wraps the source adapters in `src/monogram/news/` with a thin CLI so
end users can trigger a fetch manually or from cron without writing
their own Python glue:

    monogram news fetch                   # all configured sources, default series
    monogram news fetch --source fred     # one source
    monogram news fetch --series FEDFUNDS --series DGS10   # specific series

Output lands at `daily/<today>/signals.md` via github_store.write so
it stays in the same atomic-commit / backup path as any other vault
write. Morning brief (phase 2 integration) will read this file as
context for the "market / external" section.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import click

from . import github_store

log = logging.getLogger("monogram.cli_news")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


@click.group(name="news")
def news_group():
    """Pull external signals (FRED macro, etc.) into the vault."""


@news_group.command("fetch")
@click.option(
    "--source",
    type=click.Choice(["fred", "all"]),
    default="all",
    help="Which adapter(s) to run. Only 'fred' for now; more to come.",
)
@click.option(
    "--series", "series_list", multiple=True,
    help="Override default series list (repeatable). Only affects fred.",
)
@click.option(
    "--dry-run", is_flag=True,
    help="Print rendered markdown without writing to the vault.",
)
def fetch_cmd(source: str, series_list: tuple[str, ...], dry_run: bool):
    """Fetch news/signal sources and write daily/<today>/signals.md."""
    asyncio.run(_fetch(source, list(series_list) or None, dry_run))


async def _fetch(
    source: str,
    series_list: list[str] | None,
    dry_run: bool,
) -> None:
    sections: list[str] = []

    if source in ("fred", "all"):
        from .news.fred import fetch_and_render
        section = await fetch_and_render(series_list)
        if section:
            sections.append(section)
        else:
            click.echo("FRED: no observations (missing FRED_API_KEY, or all series failed)")

    if not sections:
        click.echo("news fetch: nothing to write")
        return

    body = "\n\n".join(sections).rstrip() + "\n"

    if dry_run:
        click.echo("--- dry-run, not writing ---")
        click.echo(body)
        return

    path = f"daily/{_today()}/signals.md"
    ok = github_store.write(path, body, f"monogram news: signals for {_today()}")
    if ok:
        click.echo(f"✓ wrote {path} ({len(body):,} bytes)")
    else:
        click.echo(f"✗ write failed for {path}")
