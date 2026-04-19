"""`monogram stats` — pipeline health from the terminal.

Prints the same content as the /stats Telegram command, with richer
formatting (full markdown tables) for terminal viewing. Optional
--save flag writes to evals/baselines/ for drift comparison.

Usage:
    monogram stats                       # last 7d, terminal output
    monogram stats --window 30           # last 30d
    monogram stats --markdown            # full markdown table format
    monogram stats --save                # write snapshot to evals/baselines/
"""
from __future__ import annotations

from datetime import datetime, timezone

import click


@click.command(name="stats")
@click.option(
    "--window",
    type=int,
    default=7,
    show_default=True,
    help="Look-back window in days (1-90).",
)
@click.option(
    "--markdown",
    is_flag=True,
    help="Full markdown output (tables, headings).",
)
@click.option(
    "--save",
    is_flag=True,
    help="Save snapshot to evals/baselines/<date>-stats.md.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="JSON output (scriptable).",
)
def stats_cmd(window, markdown, save, as_json):
    """Pipeline health — latency distribution, error rate, stage breakdown.

    Reads log/pipeline.jsonl from your vault repo.
    """
    window = max(1, min(90, window))

    from .pipeline_stats import fetch_stats

    try:
        stats = fetch_stats(window_days=window)
    except Exception as e:
        click.echo(f"stats error: {type(e).__name__}: {e}", err=True)
        raise click.Abort()

    if stats is None or stats.total_runs == 0:
        click.echo(
            f"No pipeline data in last {window}d.\n"
            f"Drop something into Saved Messages to populate log/pipeline.jsonl."
        )
        return

    if as_json:
        import json
        click.echo(json.dumps(stats.to_dict(), indent=2, default=str))
        return

    if markdown or save:
        content = stats.to_markdown()
    else:
        # Compact terminal-friendly format
        content = _compact_render(stats)

    click.echo(content)

    if save:
        _save_baseline(stats)


def _compact_render(stats) -> str:
    """Dense one-screen summary — same info as /stats, slightly richer."""
    warmup = stats.latency.samples < 10
    lines = [
        f"Pipeline stats — last {stats.window_days}d",
        f"─" * 50,
        f"Runs:         {stats.total_runs}",
        f"Error rate:   {stats.error_rate:.1%}",
        f"Escalations:  {stats.escalation_rate:.1%}",
    ]
    if warmup:
        lines.append("⚠  n<10: p95/p99 not yet reliable")
    lines += [
        "",
        f"Latency (ms): "
        f"p50={stats.latency.p50_ms}  "
        f"p95={stats.latency.p95_ms}  "
        f"p99={stats.latency.p99_ms}",
        f"              "
        f"mean={stats.latency.mean_ms}  "
        f"min={stats.latency.min_ms}  "
        f"max={stats.latency.max_ms}",
    ]

    if stats.per_stage:
        lines += ["", "Per-stage (ms):"]
        for s in stats.per_stage:
            lines.append(
                f"  {s.stage:<13} n={s.samples:<5} "
                f"p50={s.p50_ms:<6} p95={s.p95_ms:<6} mean={s.mean_ms}"
            )

    if stats.by_target_kind:
        top = sorted(stats.by_target_kind.items(), key=lambda x: -x[1])[:5]
        lines += ["", "Top target kinds:"]
        for kind, count in top:
            lines.append(f"  {kind:<15} {count}")

    return "\n".join(lines)


def _save_baseline(stats) -> None:
    """Write markdown snapshot to evals/baselines/<date>-stats-<sha>.md."""
    from . import github_store
    from pathlib import Path

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    short_id = now.strftime("%H%M")  # time disambiguates same-day saves

    # Best effort — try to include a content-hash suffix for the commit
    # message (mirrors the plan's intent of "commit the baseline").
    baseline_path = f"evals/baselines/{date_str}-stats-{short_id}.md"
    body = stats.to_markdown()

    # Try vault write via github_store first (this is the canonical
    # location). If that fails (no PAT, no connection), fall back to
    # local filesystem so users can still capture a snapshot.
    try:
        ok = github_store.write(
            baseline_path,
            body,
            f"monogram stats: baseline snapshot ({date_str})",
        )
        if ok:
            click.echo(f"\n✓ Saved baseline to vault: {baseline_path}")
            return
    except Exception as e:
        click.echo(f"\n(vault write failed: {e} — writing local copy)", err=True)

    # Local fallback
    local_dir = Path("evals/baselines")
    local_dir.mkdir(parents=True, exist_ok=True)
    local_path = local_dir / f"{date_str}-stats-{short_id}.md"
    local_path.write_text(body)
    click.echo(f"\n✓ Saved local baseline: {local_path}")
