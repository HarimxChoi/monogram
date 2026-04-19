"""Vault-side configuration — loaded from mono/config.md at startup.

Separates USER-EDITABLE config (language, life categories, never-read paths)
from APP-LEVEL config (.env: PATs, tokens, repo name). App config is in
config.py; vault config is here.

The vault config lives in the data repo (example-org/mono/config.md) so it's
portable across deployments and editable directly in Obsidian.

Hard-coded defense: life/credentials/ is ALWAYS blocked from LLM reads,
even if the user removes it from never_read_paths. See _HARD_NEVER_READ.

v0.7 eval fields (new):
- eval_enabled: layer-3 kill-switch for the eval/harvest system.
  Layer 1 is not installing `.[eval]`; layer 2 is MONOGRAM_EVAL_DISABLED env.
  See evals/kill_switch.py for precedence.
- classifier_few_shot_enabled: layer-4 kill-switch specific to Track B's
  classifier few-shot. Independent of eval_enabled — you can keep eval
  harvesting while turning off the production-side few-shot.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from typing import ClassVar

from . import github_store

log = logging.getLogger("monogram.vault_config")

_CONFIG_PATH = "config.md"

_DEFAULT_LIFE_CATEGORIES = [
    "shopping",
    "places",
    "credentials",
    "career",
    "read-watch",
    "meeting-notes",
    "health",
    "finance",
]

_DEFAULT_NEVER_READ = ["life/credentials/"]


@dataclass
class VaultConfig:
    primary_language: str = "en"
    life_categories: list[str] = field(
        default_factory=lambda: list(_DEFAULT_LIFE_CATEGORIES)
    )
    never_read_paths: list[str] = field(
        default_factory=lambda: list(_DEFAULT_NEVER_READ)
    )

    # v0.4 — LLM configuration (vault-level, portable across deployments)
    llm_provider: str = ""                       # "" = legacy MONOGRAM_MODEL fallback
    llm_mode: str = "tiered"                     # "tiered" | "single"
    llm_models: dict[str, str] = field(default_factory=dict)
    llm_base_url: str = ""                       # ollama / openai-compat

    # v0.6 — Web UI configuration
    webui_mode: str = "mcp-only"                 # gcs | self-host | mcp-only
    webui_gcs: dict[str, str] = field(
        default_factory=lambda: {"bucket": "", "path_slug": "main"}
    )
    webui_self_host: dict[str, int] = field(
        default_factory=lambda: {"port": 8765}
    )

    # v0.7 — Eval harness (layer 3 of kill-switch).
    # Default True: harness works out-of-box once `.[eval]` is installed.
    # User sets to False to stop scheduled cron and bot /eval_* commands
    # without uninstalling. CLI still works for manual, intentional runs.
    eval_enabled: bool = True

    # v0.7 onboarding state — auto-set True after the 3rd successful
    # /approve of a harvest (§6.3). Controls whether the approval message
    # includes the full onboarding checklist.
    harvest_onboarding_complete: bool = False

    # v0.8 — Classifier few-shot (layer 4, Track B only).
    # OFF by default. Flipped to True only after P7 2-week measurement passes.
    classifier_few_shot_enabled: bool = False
    classifier_few_shot_max_examples: int = 5
    classifier_few_shot_path: str = "examples/harvested.jsonl"

    # v0.8 — URL ingestion configuration
    # Master switch; set False to disable all URL extraction in listener.
    ingestion_enabled: bool = True
    # Per-URL hard timeout. Slow extractors can't block the pipeline.
    ingestion_timeout_seconds: float = 10.0
    # Cap on URLs processed per drop (guards against URL-spam).
    ingestion_max_urls_per_drop: int = 3
    # YouTube: use Whisper fallback when transcript is unavailable.
    # Opt-in because Whisper is CPU/GPU-heavy (5-30s per minute of video).
    youtube_whisper_fallback: bool = False
    # arXiv: enrich with Semantic Scholar citation count (adds 1-2s/URL)
    arxiv_enrichment: bool = True

    # HARD-CODED — defense in depth. Even if the user deletes
    # `life/credentials/` from never_read_paths in config.md, this tuple
    # ensures the LLM still skips it.
    _HARD_NEVER_READ: ClassVar[tuple[str, ...]] = ("life/credentials/",)

    @property
    def effective_never_read(self) -> list[str]:
        """Union of hard-coded + user-added never-read paths."""
        return sorted(set(self._HARD_NEVER_READ) | set(self.never_read_paths))


@lru_cache(maxsize=1)
def load_vault_config() -> VaultConfig:
    """Load and cache the vault config. Restart `monogram run` to re-read.

    Returns defaults if config.md is missing, empty, or malformed.
    """
    try:
        content = github_store.read(_CONFIG_PATH)
    except Exception as e:
        log.warning("vault_config: repo read failed, using defaults: %s", e)
        return VaultConfig()

    if not content:
        log.info("vault_config: %s not found, using defaults", _CONFIG_PATH)
        return VaultConfig()

    try:
        meta, _body = github_store.parse_metadata(content)
    except Exception as e:
        log.warning("vault_config: YAML parse failed, using defaults: %s", e)
        return VaultConfig()

    if not meta:
        return VaultConfig()

    cfg = VaultConfig()
    if isinstance(meta.get("primary_language"), str) and meta["primary_language"]:
        cfg.primary_language = meta["primary_language"]
    if isinstance(meta.get("life_categories"), list):
        cats = [c for c in meta["life_categories"] if isinstance(c, str) and c]
        if cats:
            cfg.life_categories = cats
    if isinstance(meta.get("never_read_paths"), list):
        paths = [p for p in meta["never_read_paths"] if isinstance(p, str) and p]
        if paths:
            cfg.never_read_paths = paths

    # v0.4: LLM configuration
    if isinstance(meta.get("llm_provider"), str):
        cfg.llm_provider = meta["llm_provider"].strip()
    if isinstance(meta.get("llm_mode"), str):
        mode = meta["llm_mode"].strip()
        if mode in ("tiered", "single"):
            cfg.llm_mode = mode
    if isinstance(meta.get("llm_models"), dict):
        cfg.llm_models = {
            k: v.strip()
            for k, v in meta["llm_models"].items()
            if isinstance(k, str) and isinstance(v, str) and v.strip()
        }
    if isinstance(meta.get("llm_base_url"), str):
        cfg.llm_base_url = meta["llm_base_url"].strip()

    # v0.6: Web UI configuration
    if isinstance(meta.get("webui_mode"), str):
        mode = meta["webui_mode"].strip()
        if mode in ("gcs", "self-host", "mcp-only"):
            cfg.webui_mode = mode
    if isinstance(meta.get("webui_gcs"), dict):
        bucket = meta["webui_gcs"].get("bucket", "")
        slug = meta["webui_gcs"].get("path_slug", "main")
        cfg.webui_gcs = {
            "bucket": str(bucket).strip() if bucket else "",
            "path_slug": str(slug).strip() if slug else "main",
        }
    if isinstance(meta.get("webui_self_host"), dict):
        port = meta["webui_self_host"].get("port", 8765)
        try:
            cfg.webui_self_host = {"port": int(port)}
        except (TypeError, ValueError):
            pass  # keep default

    # v0.7: Eval kill-switch + onboarding state
    if isinstance(meta.get("eval_enabled"), bool):
        cfg.eval_enabled = meta["eval_enabled"]
    if isinstance(meta.get("harvest_onboarding_complete"), bool):
        cfg.harvest_onboarding_complete = meta["harvest_onboarding_complete"]

    # v0.8: Classifier few-shot (Track B)
    if isinstance(meta.get("classifier_few_shot_enabled"), bool):
        cfg.classifier_few_shot_enabled = meta["classifier_few_shot_enabled"]
    if isinstance(meta.get("classifier_few_shot_max_examples"), int):
        cfg.classifier_few_shot_max_examples = max(
            0, min(10, meta["classifier_few_shot_max_examples"])
        )
    # classifier_few_shot_path is intentionally NOT user-overridable.
    # Letting config.md redirect it would allow an attacker with write
    # access to the vault to steer the classifier prompt at an arbitrary
    # file and inject crafted exemplars. Path stays at the code default.

    # v0.8: URL ingestion
    if isinstance(meta.get("ingestion_enabled"), bool):
        cfg.ingestion_enabled = meta["ingestion_enabled"]
    if isinstance(meta.get("ingestion_timeout_seconds"), (int, float)):
        t = float(meta["ingestion_timeout_seconds"])
        cfg.ingestion_timeout_seconds = max(1.0, min(60.0, t))
    if isinstance(meta.get("ingestion_max_urls_per_drop"), int):
        cfg.ingestion_max_urls_per_drop = max(
            0, min(10, meta["ingestion_max_urls_per_drop"])
        )
    if isinstance(meta.get("youtube_whisper_fallback"), bool):
        cfg.youtube_whisper_fallback = meta["youtube_whisper_fallback"]
    if isinstance(meta.get("arxiv_enrichment"), bool):
        cfg.arxiv_enrichment = meta["arxiv_enrichment"]

    return cfg


def reload_vault_config() -> VaultConfig:
    """Force re-read of config.md. Clears the lru_cache."""
    load_vault_config.cache_clear()
    return load_vault_config()


def set_config_field(key: str, value) -> bool:
    """Update a single YAML frontmatter field in config.md and commit.

    Used by bot commands like /eval_enable. Preserves other fields and
    body. Returns True on success.
    """
    import yaml
    try:
        content = github_store.read(_CONFIG_PATH) or ""
    except Exception as e:
        log.error("set_config_field: read failed: %s", e)
        return False

    meta, body = github_store.parse_metadata(content)
    meta = dict(meta or {})
    meta[key] = value

    # Re-serialize. Matches github_store's conventions (YAML block + body).
    new_content = "---\n" + yaml.safe_dump(meta, sort_keys=False).rstrip() + "\n---\n" + (body or "")
    ok = github_store.write(_CONFIG_PATH, new_content, f"monogram: {key}={value}")
    if ok:
        reload_vault_config()
    return ok
