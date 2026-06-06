"""Scaffold vault GitHub Actions; workflows use built-in GITHUB_TOKEN (no PAT stored). PAT needs Contents+Workflows+Secrets write."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger("monogram.actions_setup")


_WORKFLOW_TEMPLATE = """name: monogram __NAME__
on:
  schedule:
    - cron: "__CRON__"
  workflow_dispatch: {}
permissions:
  contents: write
jobs:
  __NAME__:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install "mono-gram"
      - name: monogram __CMD__
        run: monogram __CMD__
        env:
          GITHUB_PAT: ${{ secrets.GITHUB_TOKEN }}
          GITHUB_REPO: ${{ github.repository }}
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
          TELEGRAM_API_ID: ${{ secrets.TELEGRAM_API_ID }}
          TELEGRAM_API_HASH: ${{ secrets.TELEGRAM_API_HASH }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_USER_ID: ${{ secrets.TELEGRAM_USER_ID }}
"""

_SECRET_FIELDS = (
    "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
    "TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_BOT_TOKEN", "TELEGRAM_USER_ID",
)

# reindex uses local EmbeddingGemma — no LLM/Telegram secret needed, only built-in token
_REINDEX_TEMPLATE = """name: monogram reindex
on:
  schedule:
    - cron: "__CRON__"
  workflow_dispatch: {}
permissions:
  contents: write
jobs:
  reindex:
    runs-on: ubuntu-latest
    timeout-minutes: 120
    steps:
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Cache embedding model
        uses: actions/cache@v4
        with:
          path: ~/.cache/huggingface
          key: monogram-emb-embeddinggemma-300m-v1
      - run: pip install "mono-gram[semantic-gemma]"
      - name: monogram reindex
        run: monogram reindex
        env:
          GITHUB_PAT: ${{ secrets.GITHUB_TOKEN }}
          GITHUB_REPO: ${{ github.repository }}
          HF_HOME: ~/.cache/huggingface
"""


def _reindex_workflow(cron: str) -> str:
    return _REINDEX_TEMPLATE.replace("__CRON__", cron)


# graph build is deterministic — no LLM secret, only vault token
_GRAPH_TEMPLATE = """name: monogram graph
on:
  schedule:
    - cron: "__CRON__"
  workflow_dispatch: {}
permissions:
  contents: write
jobs:
  graph:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install "mono-gram"
      - name: monogram graph
        run: monogram graph
        env:
          GITHUB_PAT: ${{ secrets.GITHUB_TOKEN }}
          GITHUB_REPO: ${{ github.repository }}
"""


def _graph_workflow(cron: str) -> str:
    return _GRAPH_TEMPLATE.replace("__CRON__", cron)


def local_to_utc_cron(
    hour: int, minute: int, offset_hours: int, dow: int | None = None
) -> str:
    """dow uses Python Mon=0..Sun=6; None means daily."""
    if dow is None:
        total = (hour * 60 + minute - offset_hours * 60) % 1440
        return f"{total % 60} {total // 60} * * *"
    base = datetime(2024, 1, 1)  # a Monday
    local_dt = (base + timedelta(days=dow)).replace(
        hour=hour, minute=minute, tzinfo=timezone(timedelta(hours=offset_hours))
    )
    utc_dt = local_dt.astimezone(timezone.utc)
    cron_dow = (utc_dt.weekday() + 1) % 7  # convert Python weekday to cron (Sun=0)
    return f"{utc_dt.minute} {utc_dt.hour} * * {cron_dow}"


def _workflow(name: str, cmd: str, cron: str) -> str:
    return (
        _WORKFLOW_TEMPLATE
        .replace("__NAME__", name)
        .replace("__CMD__", cmd)
        .replace("__CRON__", cron)
    )


def _provision_secrets() -> list[str]:
    from . import github_store
    from .config import load_config

    cfg = load_config()
    values = {
        "GEMINI_API_KEY": cfg.gemini_api_key,
        "ANTHROPIC_API_KEY": cfg.anthropic_api_key,
        "OPENAI_API_KEY": cfg.openai_api_key,
        "TELEGRAM_API_ID": str(cfg.telegram_api_id or ""),
        "TELEGRAM_API_HASH": cfg.telegram_api_hash,
        "TELEGRAM_BOT_TOKEN": cfg.telegram_bot_token,
        "TELEGRAM_USER_ID": str(cfg.telegram_user_id or ""),
    }
    repo = github_store._repo()
    done: list[str] = []
    for name in _SECRET_FIELDS:
        value = values.get(name) or ""
        if not value:
            continue
        repo.create_secret(name, value)  # PyGithub encrypts with repo public key via pynacl
        done.append(name)
    return done


def setup_vault_actions(
    brief_hour: int,
    brief_minute: int,
    offset_hours: int,
    weekly_dow: int = 6,
    weekly_hour: int = 21,
) -> str:
    """Write workflows + provision secrets; raises on write failure (PAT scope)."""
    from . import github_store

    morning_cron = local_to_utc_cron(brief_hour, brief_minute, offset_hours)
    weekly_cron = local_to_utc_cron(weekly_hour, 0, offset_hours, dow=weekly_dow)
    reindex_cron = local_to_utc_cron(2, 0, offset_hours)
    graph_cron = local_to_utc_cron(2, 30, offset_hours)    # after reindex

    writes = {
        ".github/workflows/monogram-morning.yml": _workflow("morning", "morning", morning_cron),
        ".github/workflows/monogram-weekly.yml": _workflow("weekly", "weekly", weekly_cron),
        ".github/workflows/monogram-reindex.yml": _reindex_workflow(reindex_cron),
        ".github/workflows/monogram-graph.yml": _graph_workflow(graph_cron),
    }
    if not github_store.write_multi(writes, "monogram: scheduled jobs (morning + weekly + reindex + graph)"):
        raise RuntimeError(
            "could not write .github/workflows/ — PAT needs 'Workflows: write'"
        )

    secrets = _provision_secrets()
    return (
        f"workflows written (morning '{morning_cron}', weekly '{weekly_cron}', "
        f"reindex '{reindex_cron}', graph '{graph_cron}'); "
        f"secrets set: {', '.join(secrets) or 'none'}"
    )
