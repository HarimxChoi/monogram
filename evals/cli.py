"""`monogram eval *` CLI — click subgroup registered by main cli.py.

All commands respect the three-layer kill-switch (evals/kill_switch.py)
except `enable` / `disable` / `status` themselves, which need to work
even when eval is disabled (otherwise you couldn't turn it back on).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import click

from evals.kill_switch import is_eval_enabled, is_few_shot_enabled


_EVAL_ROOT = Path(__file__).parent


@click.group(name="eval")
def eval_group():
    """Evaluation harness — cassette replay + harvest loop.

    See MONOGRAM_EVAL_PLAN.md for the full design.
    """
    pass


# ── Kill-switch commands (always available) ───────────────────────────

@eval_group.command("status")
def status_cmd():
    """Show the effective kill-switch state across all 3 layers."""
    env_off = os.environ.get("MONOGRAM_EVAL_DISABLED") == "1"
    try:
        from monogram.vault_config import reload_vault_config
        cfg = reload_vault_config()
        cfg_ok = True
        cfg_eval = cfg.eval_enabled
        cfg_fewshot = cfg.classifier_few_shot_enabled
    except Exception as e:
        cfg_ok = False
        cfg_eval = cfg_fewshot = None
        click.echo(f"  vault_config: read failed: {e}")

    click.echo("Eval kill-switch state:")
    click.echo(f"  Layer 2 env MONOGRAM_EVAL_DISABLED: {'1 (OFF)' if env_off else 'unset (pass)'}")
    if cfg_ok:
        click.echo(f"  Layer 3 config eval_enabled:        {cfg_eval}")
        click.echo(f"  Layer 4 classifier_few_shot:        {cfg_fewshot}")

    enabled, reason = is_eval_enabled()
    click.echo()
    if enabled:
        click.echo(click.style("  Effective: ENABLED", fg="green"))
    else:
        click.echo(click.style(f"  Effective: DISABLED ({reason})", fg="red"))


@eval_group.command("enable")
def enable_cmd():
    """Write eval_enabled: true to mono/config.md."""
    from monogram.vault_config import set_config_field
    ok = set_config_field("eval_enabled", True)
    if ok:
        click.secho("✓ eval_enabled: true in mono/config.md", fg="green")
        if os.environ.get("MONOGRAM_EVAL_DISABLED") == "1":
            click.secho("⚠ env MONOGRAM_EVAL_DISABLED=1 still overrides.", fg="yellow")
    else:
        click.secho("✗ write failed; see logs", fg="red")
        sys.exit(1)


@eval_group.command("disable")
def disable_cmd():
    """Write eval_enabled: false to mono/config.md."""
    from monogram.vault_config import set_config_field
    ok = set_config_field("eval_enabled", False)
    click.echo("✓ eval_enabled: false" if ok else "✗ write failed")
    sys.exit(0 if ok else 1)


@eval_group.command("disable-few-shot")
def disable_fewshot_cmd():
    """Kill-switch for Track B classifier few-shot."""
    from monogram.vault_config import set_config_field
    ok = set_config_field("classifier_few_shot_enabled", False)
    click.echo("✓ classifier_few_shot_enabled: false" if ok else "✗ write failed")
    sys.exit(0 if ok else 1)


@eval_group.command("enable-few-shot")
@click.confirmation_option(
    prompt="Enable classifier few-shot? Per plan §10, measure for 2 weeks against pre-committed rule.",
)
def enable_fewshot_cmd():
    """Enable Track B — with the P7 2-week measurement rule reminder."""
    from monogram.vault_config import set_config_field
    ok = set_config_field("classifier_few_shot_enabled", True)
    if ok:
        click.secho("✓ classifier_few_shot_enabled: true", fg="green")
        click.echo(
            "\nRemember the pre-committed failure rule:\n"
            "  • accuracy drop >1pp vs baseline      → disable\n"
            "  • any credential fixture fails        → disable\n"
            "  • any injection fixture fails         → disable\n"
            "  • escalation rate change >5pp         → disable\n"
        )


# ── Eval commands (gated) ─────────────────────────────────────────────

def _require_enabled():
    enabled, reason = is_eval_enabled()
    if not enabled:
        click.secho(f"eval disabled: {reason}", fg="yellow")
        click.echo("Enable via: `monogram eval enable` or /eval_enable in bot")
        sys.exit(0)


@eval_group.command("run")
@click.option("--record", is_flag=True, help="Force re-record all cassettes (serial).")
@click.option("--auto-record", is_flag=True, help="Record on cache miss only.")
@click.option("--category", default=None, help="Run one category.")
@click.option("--fixture", default=None, help="Run one fixture by id.")
@click.option("-v", "--verbose", is_flag=True)
def run_cmd(record, auto_record, category, fixture, verbose):
    """Run the eval suite. Replay mode by default (zero LLM cost)."""
    _require_enabled()
    args = ["pytest", str(_EVAL_ROOT), "--tb=short"]
    if record:
        args.append("--record")
    if auto_record:
        args.append("--auto-record")
    if category:
        args.extend(["-k", category])
    if fixture:
        args.extend(["-k", fixture])
    if verbose:
        args.append("-v")
    else:
        args.append("-q")
    result = subprocess.run(args)
    sys.exit(result.returncode)


@eval_group.command("report")
@click.option("--last", is_flag=True, help="Render last run's markdown report.")
def report_cmd(last):
    """Render a markdown report from the most recent eval run."""
    _require_enabled()
    from evals.report import render_last_report
    path = render_last_report()
    click.echo(f"Report: {path}")


@eval_group.command("baseline")
@click.option("--save", is_flag=True, help="Save current results as baseline.")
def baseline_cmd(save):
    """Manage the committed baseline results."""
    _require_enabled()
    from evals.report import save_baseline
    if save:
        path = save_baseline()
        click.echo(f"Baseline saved: {path}")
    else:
        click.echo("Use --save to commit current results as baseline.")


@eval_group.command("drift")
def drift_cmd():
    """Re-record cassettes side-by-side and diff structurally."""
    _require_enabled()
    from evals.report import run_drift_comparison
    result = run_drift_comparison()
    click.echo(json.dumps(result, indent=2, ensure_ascii=False))


@eval_group.command("harvest")
@click.option("--since", "since_days", default=7, type=int, help="Harvest window in days.")
@click.option("--dry-run", is_flag=True, help="Write audit copy only; skip replay + Telegram.")
def harvest_cmd(since_days, dry_run):
    """Track A — harvest production drops into fixtures."""
    _require_enabled()
    from evals.harvest import run_harvest
    result = run_harvest(since_days=since_days, dry_run=dry_run)
    click.echo(json.dumps(result, indent=2, ensure_ascii=False))


@eval_group.command("rollback")
@click.option("--harvest-id", required=True, help="Harvest date, e.g. 2026-04-26")
def rollback_cmd(harvest_id):
    """Remove all fixtures from a given harvest_id."""
    _require_enabled()
    from evals.harvest import rollback_harvest
    result = rollback_harvest(harvest_id)
    click.echo(json.dumps(result, indent=2, ensure_ascii=False))


@eval_group.command("ablate-diff")
@click.option("--against", default="main", help="Baseline branch/commit.")
def ablate_diff_cmd(against):
    """Orchestrator ablation — compare current branch's cassette vs baseline."""
    _require_enabled()
    from evals.report import run_ablation_diff
    result = run_ablation_diff(against=against)
    click.echo(json.dumps(result, indent=2, ensure_ascii=False))


@eval_group.command("show")
@click.argument("fixture_id")
def show_cmd(fixture_id):
    """Dump a fixture's full content for debugging."""
    _require_enabled()
    from evals.fixtures import find_by_id
    f = find_by_id(fixture_id)
    if f is None:
        click.secho(f"Fixture not found: {fixture_id}", fg="red")
        sys.exit(1)
    click.echo(json.dumps(f, indent=2, ensure_ascii=False))
