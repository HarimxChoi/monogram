"""Idempotent v0.6 → v0.7+ migration: opt-in eval harness for existing installs."""
from __future__ import annotations

import sys

import click


@click.group(name="migrate")
def migrate_group():
    """v0.6 → v0.7+ migration helpers."""


@migrate_group.command("check")
def migrate_check():
    """Report what migration would do without changing anything."""
    findings = _run_migration_checks()
    _print_findings(findings)
    needs_action = any(f["needs_action"] for f in findings)
    if needs_action:
        click.echo(
            "\n→ Run `monogram migrate apply` to apply the changes above."
        )
        sys.exit(1)
    else:
        click.echo("\n✓ Vault is already on v0.7 schema. No migration needed.")


@migrate_group.command("apply")
@click.option(
    "--enable-eval",
    is_flag=True,
    help="Opt INTO eval harness (default is opt-out for existing users).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would change without writing.",
)
def migrate_apply(enable_eval: bool, dry_run: bool):
    """Apply v0.6 → v0.7+ migration."""
    findings = _run_migration_checks()
    _print_findings(findings)

    actions_needed = [f for f in findings if f["needs_action"]]
    if not actions_needed:
        click.echo("\n✓ Nothing to do. Vault already on v0.7 schema.")
        return

    click.echo(f"\n{'Dry-run' if dry_run else 'Applying'} {len(actions_needed)} change(s):")
    for f in actions_needed:
        click.echo(f"  • {f['action_description']}")

    if dry_run:
        click.echo("\n(dry-run — no changes written)")
        return

    if not click.confirm("\nProceed?", default=True):
        raise click.Abort()

    _apply_vault_config_migration(enable_eval=enable_eval)

    _apply_gitignore_migration()

    click.echo("\n✓ Migration complete.")
    click.echo("  Run `monogram eval status` to verify kill-switch state.")
    click.echo("  Run `monogram run` to start the listener with new config.")


def _run_migration_checks() -> list[dict]:
    findings: list[dict] = []

    try:
        from .vault_config import load_vault_config

        cfg = load_vault_config()
        has_eval_field = hasattr(cfg, "eval_enabled")
        findings.append({
            "name": "vault_config schema",
            "status": "v0.7+" if has_eval_field else "v0.6",
            "needs_action": not has_eval_field,
            "detail": (
                "config.md has eval_enabled field"
                if has_eval_field
                else "config.md lacks eval_enabled — will default True if not set"
            ),
            "action_description": (
                "Write eval_enabled: false to mono/config.md "
                "(opt-out default; use --enable-eval to opt in)"
            ),
        })
    except Exception as e:
        findings.append({
            "name": "vault_config schema",
            "status": "error",
            "needs_action": False,
            "detail": f"Cannot load vault_config: {e}",
            "action_description": "Fix vault_config issue before migrating",
        })

    from pathlib import Path

    gitignore = Path(".gitignore")
    required_patterns = [
        "*.session",
        "*.session-journal",
        ".env",
        "gcp-sa.json",
    ]
    if gitignore.exists():
        content = gitignore.read_text()
        missing = [p for p in required_patterns if p not in content]
        findings.append({
            "name": ".gitignore coverage",
            "status": "complete" if not missing else f"missing {len(missing)} pattern(s)",
            "needs_action": bool(missing),
            "detail": (
                "All secret/session patterns present"
                if not missing
                else f"Missing: {', '.join(missing)}"
            ),
            "action_description": (
                f"Append to .gitignore: {', '.join(missing)}"
                if missing
                else ""
            ),
        })
    else:
        findings.append({
            "name": ".gitignore coverage",
            "status": "no .gitignore",
            "needs_action": True,
            "detail": ".gitignore file does not exist",
            "action_description": "Create .gitignore with session/env/cred patterns",
        })

    log_dir = Path("log")
    findings.append({
        "name": "log/ structure",
        "status": "exists" if log_dir.exists() else "missing (lazy-created)",
        "needs_action": False,
        "detail": (
            "log/pipeline.jsonl + log/runs/ will be populated as the "
            "listener runs"
        ),
        "action_description": "",
    })

    return findings


def _print_findings(findings: list[dict]) -> None:
    click.echo("Migration check:\n")
    for f in findings:
        mark = "!" if f["needs_action"] else "✓"
        click.echo(f"  {mark} {f['name']}: {f['status']}")
        if f["detail"]:
            click.echo(f"      {f['detail']}")


def _apply_vault_config_migration(enable_eval: bool) -> None:
    try:
        from .vault_config import set_config_field
    except ImportError:
        click.echo(
            "  ✗ Cannot import set_config_field — skipping config migration."
        )
        return

    try:
        set_config_field("eval_enabled", enable_eval)
        click.echo(
            f"  ✓ Wrote eval_enabled: {str(enable_eval).lower()} to mono/config.md"
        )
    except Exception as e:
        click.echo(f"  ✗ Failed to write config: {e}")


def _apply_gitignore_migration() -> None:
    """Writes a timestamped .gitignore backup before modifying."""
    from datetime import datetime, timezone
    from pathlib import Path

    gitignore = Path(".gitignore")
    required = ["*.session", "*.session-journal", ".env", "gcp-sa.json"]

    if gitignore.exists():
        content = gitignore.read_text()
        missing = [p for p in required if p not in content]
    else:
        content = ""
        missing = required

    if not missing:
        return

    if gitignore.exists():
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        backup = gitignore.with_suffix(f".bak.{ts}")
        backup.write_text(content)
        click.echo(f"  ✓ Backed up .gitignore → {backup}")

    new_block = "\n# v0.7 migration: session files + secrets\n" + "\n".join(missing) + "\n"
    gitignore.write_text(content + new_block)
    click.echo(f"  ✓ Appended {len(missing)} pattern(s) to .gitignore")
