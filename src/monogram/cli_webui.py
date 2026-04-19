"""v0.6 — `monogram webui ...` CLI subcommand group.

Commands:
  rotate-password   Prompt + write MONOGRAM_WEBUI_PASSWORD to .env (chmod 600)
  test              Dry-run render_bundle, report errors
  url               Print current backend URL
"""
from __future__ import annotations

import asyncio
import os
import stat
import sys
from pathlib import Path

import click


@click.group("webui")
def webui_group():
    """Web UI utilities — rotate password, dry-run render, print URL."""


@webui_group.command("rotate-password")
def rotate_password():
    """Interactively rotate MONOGRAM_WEBUI_PASSWORD in the local .env.

    Never transits Telegram. Password is prompted twice with validation;
    .env is rewritten in place with 0600 permissions where possible.
    """
    from .encryption_layer import MIN_PASSWORD_LEN, validate_password

    env_path = Path(".env")
    if not env_path.exists():
        click.echo(".env not found in current directory. Run `monogram init` first.")
        raise click.Abort()

    click.echo(f"Rotate MONOGRAM_WEBUI_PASSWORD (min {MIN_PASSWORD_LEN} chars).")
    click.echo("Use your password manager's generator.")
    while True:
        pw = click.prompt("  Password", hide_input=True)
        errors = validate_password(pw)
        if errors:
            for e in errors:
                click.echo(f"  ✗ {e}")
            continue
        confirm = click.prompt("  Confirm", hide_input=True)
        if pw != confirm:
            click.echo("  ✗ Mismatch. Try again.")
            continue
        break

    # Rewrite .env in place
    content = env_path.read_text(encoding="utf-8")
    new_lines: list[str] = []
    replaced = False
    for line in content.splitlines():
        if line.strip().startswith("MONOGRAM_WEBUI_PASSWORD="):
            new_lines.append(f"MONOGRAM_WEBUI_PASSWORD={pw}")
            replaced = True
        else:
            new_lines.append(line)
    if not replaced:
        new_lines.append(f"MONOGRAM_WEBUI_PASSWORD={pw}")
    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    # Tighten permissions on unix
    if not sys.platform.startswith("win"):
        try:
            os.chmod(env_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    click.echo("✓ Password rotated in .env.")
    click.echo("  Next dashboard regeneration uses the new password.")
    click.echo("  Restart `monogram run` for the new password to take effect.")


@webui_group.command("test")
def webui_test():
    """Dry-run: render the dashboard bundle and report any errors."""
    from .webgen import render_bundle

    click.echo("Rendering dashboard (dry-run, no publish)…")
    try:
        html = asyncio.run(render_bundle())
    except Exception as e:
        click.echo(f"✗ {type(e).__name__}: {e}")
        raise click.Abort()
    click.echo(f"✓ Rendered {len(html):,} bytes of HTML.")
    click.echo("  (not wrapped in encryption shell, not uploaded)")


@webui_group.command("url")
def webui_url():
    """Print the current stable URL without regenerating."""
    from .webui import get_active_backend

    try:
        backend = get_active_backend()
        url = asyncio.run(backend.current_url())
    except Exception as e:
        click.echo(f"✗ {type(e).__name__}: {e}")
        raise click.Abort()
    if url:
        click.echo(url)
    else:
        click.echo("No current URL (mcp-only mode, or never published).")
