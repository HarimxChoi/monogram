import os
import sys

import click

from . import __version__


def _test_llm_reachable(llm_config: dict, env_additions: dict) -> None:
    """Make a 'Say OK' test call for each configured tier. Non-fatal —
    asks user to continue if the call fails (may be network flake)."""
    import asyncio
    import os

    # Inject env so credentials flow into Pydantic config on next load
    for k, v in env_additions.items():
        if v:
            os.environ[k] = v

    async def _test_one(model: str) -> tuple[bool, str]:
        try:
            from .llm import complete
            out = await asyncio.wait_for(
                complete("Say OK", model=model, max_output_tokens=10),
                timeout=15.0,
            )
            return (True, out.strip()[:60])
        except Exception as e:
            return (False, f"{type(e).__name__}: {str(e)[:100]}")

    async def _run_all(models):
        return await asyncio.gather(*(_test_one(m) for m in models))

    models = [m for m in llm_config.get("llm_models", {}).values() if m]
    if not models:
        click.echo("  (no models to test)")
        return

    results = asyncio.run(_run_all(models))
    all_ok = True
    for model, (ok, msg) in zip(models, results):
        mark = "✓" if ok else "✗"
        click.echo(f"  {mark} {model}: {msg}")
        if not ok:
            all_ok = False

    if not all_ok:
        if not click.confirm(
            "\nSome models failed the test call. Save config anyway?",
            default=False,
        ):
            raise click.Abort()


@click.group()
@click.version_option(__version__, prog_name="monogram")
def main():
    """Monogram — your personal mark on everything you build and learn."""


# v0.6: register `monogram webui ...` subcommand group
from .cli_webui import webui_group  # noqa: E402
main.add_command(webui_group)


@main.command("mcp-serve")
def mcp_serve():
    """Run Monogram as an MCP server (stdio transport)."""
    from . import mcp_server

    mcp_server.main()


_DEFAULT_LIFE_CATS = [
    "shopping", "places", "credentials", "career",
    "read-watch", "meeting-notes", "health", "finance",
]


@main.command("init")
@click.option(
    "--non-interactive",
    is_flag=True,
    help="Use all defaults, read answers from env (for testing).",
)
def init(non_interactive: bool):
    """Interactive setup: GitHub, language, categories, keys, skeleton init."""
    import asyncio
    from pathlib import Path

    from github import Github, Auth

    click.echo("Monogram init — let's set up your vault.\n")

    # ── Step 1: GitHub credentials ──
    click.echo("Step 1/5: GitHub")
    pat = click.prompt(
        "  GitHub fine-grained PAT (Contents: R/W on the vault repo)",
        hide_input=True,
    )
    username = click.prompt("  GitHub username")
    repo_name = click.prompt("  Repo name for your vault", default="mono")
    full_repo = f"{username}/{repo_name}"

    try:
        gh = Github(auth=Auth.Token(pat))
        repo = gh.get_repo(full_repo)
        visibility = "private" if repo.private else "PUBLIC"
        click.echo(f"  ✓ Connected to {full_repo} ({visibility})")
        if not repo.private:
            if not click.confirm(
                "  Repo is PUBLIC. Credentials live in a subfolder with hardcoded "
                "LLM-skip, but public visibility is still a leak risk. Continue?",
                default=False,
            ):
                raise click.Abort()
    except Exception as e:
        click.echo(f"  ✗ Could not access {full_repo}: {e}")
        raise click.Abort()

    # ── Step 2: Language ──
    click.echo("\nStep 2/5: Language")
    click.echo(
        "  Vault content (briefs, life entries, wiki bodies) is written in this "
        "language.\n  Enum values and paths stay English."
    )
    language = click.prompt(
        "  Primary language (ISO 639-1, e.g. en, ko, ja, zh, es, fr)",
        default="en",
    )

    # ── Step 3: Life categories ──
    click.echo("\nStep 3/5: Life categories")
    click.echo(f"  Defaults: {', '.join(_DEFAULT_LIFE_CATS)}")
    if click.confirm("  Use defaults?", default=True):
        categories = list(_DEFAULT_LIFE_CATS)
    else:
        raw = click.prompt(
            "  Comma-separated category list (always include 'credentials')",
            default=",".join(_DEFAULT_LIFE_CATS),
        )
        categories = [c.strip() for c in raw.split(",") if c.strip()]
        if "credentials" not in categories:
            categories.append("credentials")

    # ── Step 4: Telegram (optional) ──
    click.echo("\nStep 4/5: Telegram (optional — leave blank to skip)")
    tg_bot_token = click.prompt("  Bot token", default="", show_default=False)
    tg_user_id = click.prompt("  Your user_id (integer)", default="", show_default=False)
    tg_api_id = click.prompt("  API ID (my.telegram.org)", default="", show_default=False)
    tg_api_hash = click.prompt("  API hash", default="", show_default=False)

    # ── Step 5: Gemini ──
    click.echo("\nStep 5/5: LLM setup\n")
    click.echo("Choose a path:")
    click.echo("  [1] Default — Gemini free tier (recommended for $0/month)")
    click.echo("  [2] Bring your own LLM")
    path = click.prompt("Choice", type=click.Choice(["1", "2"]), default="1")

    from .endpoint_docs import ENDPOINTS, format_endpoint_help

    llm_config: dict = {"llm_base_url": ""}
    env_additions: dict = {
        "GEMINI_API_KEY": "",
        "ANTHROPIC_API_KEY": "",
        "OPENAI_API_KEY": "",
    }

    if path == "1":
        click.echo("")
        click.echo(format_endpoint_help("gemini"))
        click.echo("")
        gemini_key = click.prompt("Gemini API key", hide_input=True)
        env_additions["GEMINI_API_KEY"] = gemini_key
        starter = ENDPOINTS["gemini"]["default_starter"]
        llm_config.update({
            "llm_provider": "gemini",
            "llm_mode": "tiered",
            "llm_models": dict(starter),
        })
        click.echo(
            "\nNote: starter model names written to mono/config.md. "
            "Edit that file anytime to update (e.g. when Google releases new models)."
        )
    else:
        click.echo("")
        click.echo("Endpoint type:")
        providers = list(ENDPOINTS.keys())
        for i, p in enumerate(providers, 1):
            click.echo(f"  [{i}] {p}")
        choice = click.prompt("Choice", type=click.IntRange(1, len(providers)))
        provider = providers[choice - 1]

        click.echo("")
        click.echo(format_endpoint_help(provider))
        click.echo("")

        llm_config["llm_provider"] = provider

        if provider == "anthropic":
            env_additions["ANTHROPIC_API_KEY"] = click.prompt(
                "Anthropic API key", hide_input=True
            )
        elif provider == "openai":
            env_additions["OPENAI_API_KEY"] = click.prompt(
                "OpenAI API key", hide_input=True
            )
        elif provider == "ollama":
            llm_config["llm_base_url"] = click.prompt(
                "Ollama base URL", default="http://localhost:11434"
            )
        elif provider == "openai-compat":
            llm_config["llm_base_url"] = click.prompt(
                "OpenAI-compatible endpoint URL (e.g. http://localhost:1234/v1)"
            )
            env_additions["OPENAI_API_KEY"] = "dummy"

        click.echo("")
        click.echo("Model mode:")
        click.echo("  [1] Single — one model handles everything (simpler)")
        click.echo("  [2] Tiered — low/mid/high (cost-optimized)")
        mode_choice = click.prompt(
            "Choice", type=click.Choice(["1", "2"]), default="2"
        )
        llm_config["llm_mode"] = "single" if mode_choice == "1" else "tiered"

        click.echo("")
        click.echo(
            f"Enter model strings (visit the docs URL above to see current names)."
            f"\nFormat: {ENDPOINTS[provider]['format']}"
        )
        models: dict[str, str] = {}
        if llm_config["llm_mode"] == "single":
            models["single"] = click.prompt("  Model string").strip()
        else:
            click.echo("")
            click.echo("  Low tier   (classifier, extractor, verifier):")
            models["low"] = click.prompt("    model").strip()
            click.echo("  Mid tier   (wiki synthesis, verifier escalation):")
            models["mid"] = click.prompt("    model").strip()
            click.echo("  High tier  (morning brief, weekly report):")
            models["high"] = click.prompt("    model").strip()
        llm_config["llm_models"] = models

    # The model used for the localization LLM call at skeleton-init time.
    # Prefer 'single' or 'low' — cheapest tier.
    init_call_model = (
        llm_config["llm_models"].get("single")
        or llm_config["llm_models"].get("low")
        or ""
    )

    # Optional reachability check
    click.echo("\nValidating...")
    _test_llm_reachable(llm_config, env_additions)

    # ── Step 6: Web UI (v0.6) ──
    click.echo("\nStep 6/6: Web UI delivery\n")
    click.echo("Choose a path:")
    click.echo("  [1] GCP Cloud Storage (stable URL, $0/month — see docs/setup/gcp-webui.md)")
    click.echo("  [2] Self-hosted (cloudflared quick tunnel, no cloud dep)")
    click.echo("  [3] None — use an MCP client (Claude Desktop / Cursor) instead")
    webui_choice = click.prompt(
        "Choice", type=click.Choice(["1", "2", "3"]), default="3"
    )
    webui_config: dict = {}
    if webui_choice == "1":
        webui_config["webui_mode"] = "gcs"
        webui_config["webui_gcs"] = {
            "bucket": click.prompt(
                "  Bucket name",
                default=f"{username.lower()}-monogram-webui",
            ).strip(),
            "path_slug": "main",
        }
        click.echo(
            "  Note: also set GOOGLE_APPLICATION_CREDENTIALS in .env pointing "
            "at your service account JSON. See docs/setup/gcp-webui.md."
        )
    elif webui_choice == "2":
        webui_config["webui_mode"] = "self-host"
        port = click.prompt("  Local port", default="8765")
        try:
            webui_config["webui_self_host"] = {"port": int(port)}
        except ValueError:
            webui_config["webui_self_host"] = {"port": 8765}
    else:
        webui_config["webui_mode"] = "mcp-only"

    # Password (required for gcs / self-host)
    if webui_config["webui_mode"] != "mcp-only":
        from .encryption_layer import MIN_PASSWORD_LEN, validate_password
        click.echo(
            f"\n  Choose a web UI password (min {MIN_PASSWORD_LEN} chars). "
            "Use a password manager's generator."
        )
        click.echo("  This password never transits Telegram.")
        while True:
            pw = click.prompt("  Password", hide_input=True)
            errors_pw = validate_password(pw)
            if errors_pw:
                for e in errors_pw:
                    click.echo(f"    ✗ {e}")
                continue
            confirm = click.prompt("  Confirm", hide_input=True)
            if pw != confirm:
                click.echo("    ✗ Mismatch.")
                continue
            break
        env_additions["MONOGRAM_WEBUI_PASSWORD"] = pw
    else:
        env_additions["MONOGRAM_WEBUI_PASSWORD"] = ""

    # ── Write .env ──
    env_path = Path(".env")
    if env_path.exists():
        if not click.confirm(".env exists. Overwrite?", default=False):
            click.echo("Aborted.")
            raise click.Abort()

    env_body = (
        "# Generated by monogram init\n"
        f"GITHUB_PAT={pat}\n"
        f"GITHUB_REPO={full_repo}\n"
        f"TELEGRAM_BOT_TOKEN={tg_bot_token}\n"
        f"TELEGRAM_USER_ID={tg_user_id}\n"
        f"TELEGRAM_API_ID={tg_api_id}\n"
        f"TELEGRAM_API_HASH={tg_api_hash}\n"
        f"GEMINI_API_KEY={env_additions['GEMINI_API_KEY']}\n"
        f"ANTHROPIC_API_KEY={env_additions['ANTHROPIC_API_KEY']}\n"
        f"OPENAI_API_KEY={env_additions['OPENAI_API_KEY']}\n"
        f"MONOGRAM_WEBUI_PASSWORD={env_additions.get('MONOGRAM_WEBUI_PASSWORD', '')}\n"
        "MONOGRAM_WATCH_REPOS=\n"
    )
    env_path.write_text(env_body)
    # On unix, tighten .env perms so the password isn't world-readable
    if not sys.platform.startswith("win"):
        try:
            import stat as _stat
            os.chmod(env_path, _stat.S_IRUSR | _stat.S_IWUSR)
        except OSError:
            pass
    click.echo(f"  ✓ Wrote {env_path}")

    # ── Initialize skeleton in the mono repo ──
    click.echo("\nInitializing skeleton in vault repo...")
    asyncio.run(_init_skeleton(
        language, categories, init_call_model, llm_config, webui_config
    ))

    click.echo("\nDone.")
    click.echo("Next:")
    click.echo("  1. monogram auth     # one-time Telegram SMS login")
    click.echo("  2. monogram run      # start listener + bot")
    click.echo("\nTo change settings later, edit <vault>/config.md + restart monogram run.")


async def _init_skeleton(
    language: str,
    categories: list[str],
    init_call_model: str = "",
    llm_config: dict | None = None,
    webui_config: dict | None = None,
):
    """Write vault skeleton to the configured repo, localized via LLM if needed.

    init_call_model: model string to use for the one-shot localization call
                    (selected by wizard). Empty → skip localization.
    llm_config: dict with llm_provider, llm_mode, llm_models, llm_base_url
               to embed in config.md frontmatter. None → legacy config.md.
    webui_config: dict with webui_mode, webui_gcs, webui_self_host to
                 embed. None → mcp-only default.
    """
    import yaml as _yaml
    from . import github_store
    from .llm import complete

    translations: dict[str, str] = {}
    if language != "en" and init_call_model:
        phrases = [
            "Personal data vault",
            "edit freely, restart monogram run to apply",
            "active deadlined projects",
            "ongoing life areas",
            "flat knowledge base (tags in frontmatter)",
            "episodic drops + commits + daily reports",
            "weekly rollups",
            "system telemetry",
            "agent pointer index",
            "project board",
            "user-editable settings",
            "Morning briefs live at",
            "Append-only log. Latest at bottom.",
            "This file is NEVER READ by the LLM. Safe to store sensitive values here.",
        ]
        localization_prompt = (
            f"Translate each English phrase to {language} (ISO 639-1). "
            "Return a strict JSON object mapping English→translation. "
            "Keep it concise and natural. No preamble.\n\n"
            + "\n".join(f"- {p}" for p in phrases)
        )
        try:
            raw = await complete(
                localization_prompt,
                model=init_call_model,
            )
            import json
            translations = json.loads(raw)
        except Exception as e:
            click.echo(f"  (localization LLM call failed: {e}; using English templates)")
            translations = {}

    def t(phrase: str) -> str:
        return translations.get(phrase, phrase)

    # Build config.md frontmatter — use yaml.safe_dump so nested
    # llm_models dict serializes cleanly.
    frontmatter_dict: dict = {
        "primary_language": language,
        "life_categories": list(categories),
        "never_read_paths": ["life/credentials/"],
    }
    if llm_config:
        frontmatter_dict["llm_provider"] = llm_config.get("llm_provider", "")
        frontmatter_dict["llm_mode"] = llm_config.get("llm_mode", "tiered")
        frontmatter_dict["llm_models"] = llm_config.get("llm_models", {})
        frontmatter_dict["llm_base_url"] = llm_config.get("llm_base_url", "")
    if webui_config:
        frontmatter_dict["webui_mode"] = webui_config.get("webui_mode", "mcp-only")
        if webui_config.get("webui_gcs"):
            frontmatter_dict["webui_gcs"] = webui_config["webui_gcs"]
        if webui_config.get("webui_self_host"):
            frontmatter_dict["webui_self_host"] = webui_config["webui_self_host"]
    frontmatter_yaml = _yaml.safe_dump(
        frontmatter_dict, default_flow_style=False, sort_keys=False
    )
    config_md = (
        f"---\n{frontmatter_yaml}---\n\n"
        f"# Mono — {t('Personal data vault')}\n\n"
        f"{t('edit freely, restart monogram run to apply')}\n"
    )

    readme_md = (
        f"# mono\n\n"
        f"{t('Personal data vault')}.\n\n"
        f"- projects/ — {t('active deadlined projects')}\n"
        f"- life/ — {t('ongoing life areas')}\n"
        f"- wiki/ — {t('flat knowledge base (tags in frontmatter)')}\n"
        f"- daily/ — {t('episodic drops + commits + daily reports')}\n"
        f"- reports/weekly/ — {t('weekly rollups')}\n"
        f"- log/ — {t('system telemetry')}\n"
        f"- MEMORY.md — {t('agent pointer index')}\n"
        f"- board.md — {t('project board')}\n"
        f"- config.md — {t('user-editable settings')}\n\n"
        f"{t('Morning briefs live at')} `daily/YYYY-MM-DD/report.md`.\n"
    )

    def life_template(name: str) -> str:
        return (
            f"# life/{name}\n\n"
            f"{t('Append-only log. Latest at bottom.')}\n\n"
        )

    credential_warning = (
        "# Credentials\n\n"
        f"{t('This file is NEVER READ by the LLM. Safe to store sensitive values here.')}\n\n"
    )

    writes = {
        "config.md": config_md,
        "README.md": readme_md,
        "MEMORY.md": "# MEMORY.md\n\n",
        "board.md": "# Board\n\n## Active\n\n## Inactive\n\n## Done\n",
        "wiki/index.md": "# Wiki Index\n\n",
        "life/credentials/credentials.md": credential_warning,
        "log/decisions.md": "# Decisions Log\n\n",
        "projects/archive/.gitkeep": "",
        "log/runs/.gitkeep": "",
        "reports/weekly/.gitkeep": "",
        "daily/.gitkeep": "",
    }
    for cat in categories:
        if cat == "credentials":
            continue
        writes[f"life/{cat}.md"] = life_template(cat)

    ok = github_store.write_multi(writes, "monogram init: skeleton + localization")
    if ok:
        click.echo(f"  ✓ Wrote {len(writes)} files to vault")
    else:
        click.echo("  ✗ Some writes failed — review repo state")


@main.command("auth")
def auth():
    """One-time Telegram login (SMS code). Creates monogram_session.session file."""
    import asyncio

    from telethon import TelegramClient

    from .config import load_config

    cfg = load_config()

    async def _login():
        client = TelegramClient(
            "monogram_session", cfg.telegram_api_id, cfg.telegram_api_hash
        )
        await client.start()
        me = await client.get_me()
        click.echo(f"✓ logged in as @{me.username or me.id} (id={me.id})")
        click.echo("session file: monogram_session.session — keep it safe")
        await client.disconnect()

    asyncio.run(_login())


@main.command("run")
def run():
    """Run the full Monogram agent (listener + bot, concurrent)."""
    import asyncio

    from .models import validate_llm_config, validate_webui_config

    errors = validate_llm_config() + validate_webui_config()
    if errors:
        click.echo("Configuration errors:")
        for e in errors:
            click.echo(f"  ✗ {e}")
        click.echo(
            "\nFix by editing mono/config.md, running /config_* bot "
            "commands, or re-running `monogram init`."
        )
        raise click.Abort()

    from . import bot, listener
    from .queue_poller import run_queue_poller

    # v0.7: kill-switch startup log — one line showing effective eval
    # state and which layer set it. Covers audit need without per-command
    # overhead. See docs/eval.md.
    _log_eval_state_at_startup()

    async def main_loop():
        await asyncio.gather(
            listener.run_listener(bot.send_reply),
            bot.run_bot(),
            run_queue_poller(),
        )

    asyncio.run(main_loop())


def _log_eval_state_at_startup() -> None:
    """Emit one log line at service start documenting effective eval state.

    Resolution order (first match wins):
      L1 — evals package not installed / not importable
      L2 — MONOGRAM_EVAL_DISABLED=1 env var
      L3 — eval_enabled: false in mono/config.md
      default — enabled
    """
    import logging

    log = logging.getLogger("monogram.startup")

    # Layer 1: package presence
    try:
        from evals.kill_switch import is_eval_enabled  # type: ignore
    except ImportError:
        log.info("eval state: DISABLED (layer=1, evals package not installed)")
        return

    # Layer 2: env var (re-check here rather than rely on kill_switch internal)
    import os as _os
    if _os.environ.get("MONOGRAM_EVAL_DISABLED", "").strip() == "1":
        log.info("eval state: DISABLED (layer=2, MONOGRAM_EVAL_DISABLED=1)")
        return

    # Layer 3: vault_config flag
    try:
        from .vault_config import load_vault_config
        cfg = load_vault_config()
        if not getattr(cfg, "eval_enabled", True):
            log.info(
                "eval state: DISABLED (layer=3, eval_enabled: false in config.md)"
            )
            return
    except Exception as e:
        log.warning("eval state: unknown (vault_config load failed: %s)", e)
        return

    log.info("eval state: ENABLED (all 3 layers clear)")


@main.command("digest")
@click.option("--hours", default=24, help="Look-back window in hours.")
def digest(hours: int):
    """Fetch recent commits from MONOGRAM_WATCH_REPOS into daily/*/commits.md."""
    import asyncio

    from .digest import run_digest

    result = asyncio.run(run_digest(since_hours=hours))
    click.echo(
        f"digest: {result['commits']} commits across "
        f"{result['repos_fetched']} repos"
    )
    if result.get("errors"):
        click.echo("errors:")
        for e in result["errors"]:
            click.echo(f"  {e}")


@main.command("morning")
@click.option(
    "--no-push",
    is_flag=True,
    help="Skip Telegram delivery (still commits brief to GitHub).",
)
def morning(no_push: bool):
    """Run the morning job — project updates, board, brief → Telegram."""
    import asyncio

    from .morning_job import run_morning_job

    result = asyncio.run(run_morning_job(push_to_telegram=not no_push))
    click.echo(f"morning: {result}")


@main.command("weekly")
@click.option(
    "--no-push",
    is_flag=True,
    help="Skip Telegram delivery (still commits report to GitHub).",
)
@click.option(
    "--force",
    is_flag=True,
    help="Run even if today is not Sunday.",
)
def weekly(no_push: bool, force: bool):
    """Run the Sunday weekly job — report + archival sweep → Telegram."""
    import asyncio

    from .weekly_job import run_weekly_job

    result = asyncio.run(
        run_weekly_job(push_to_telegram=not no_push, force=force)
    )
    click.echo(f"weekly: {result}")


# v0.7: register `monogram eval *` subcommands if the optional extras
# are installed. `pip install -e '.[eval]'` pulls in evals/ as a
# sibling package; without it, `monogram eval` simply doesn't appear.
try:
    from evals.cli import eval_group  # type: ignore

    main.add_command(eval_group)
except ImportError:
    pass


# v0.7: `monogram migrate` helper for v0.6 → v0.7+ schema opt-in.
from .cli_migrate import migrate_group as _migrate_group  # noqa: E402
main.add_command(_migrate_group)


# v0.8: `monogram backup mirror|verify`.
from .backup import backup_group as _backup_group  # noqa: E402
main.add_command(_backup_group)


# v0.8: `monogram search "query"`.
from .cli_search import search_cmd as _search_cmd  # noqa: E402
main.add_command(_search_cmd)


# v0.8 Tier 4: `monogram stats` — pipeline health from terminal.
from .cli_stats import stats_cmd as _stats_cli_cmd  # noqa: E402
main.add_command(_stats_cli_cmd)


if __name__ == "__main__":
    main()
