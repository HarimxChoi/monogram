"""Vault-side configuration — user-editable fields in config.md, separate from .env."""
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

    llm_provider: str = ""
    llm_mode: str = "tiered"
    llm_models: dict[str, str] = field(default_factory=dict)
    llm_base_url: str = ""

    # Decoupled from chat LLM; empty = local EmbeddingGemma (no key, CI-safe).
    embedding_model: str = ""
    embedding_base_url: str = ""
    embedding_dimensions: int = 0
    embedding_rerank: bool = False

    webui_mode: str = "mcp-only"
    webui_gcs: dict[str, str] = field(
        default_factory=lambda: {"bucket": "", "path_slug": "main"}
    )
    webui_self_host: dict[str, int] = field(
        default_factory=lambda: {"port": 8765}
    )

    eval_enabled: bool = True
    harvest_onboarding_complete: bool = False

    # Layer-4 kill-switch for Track B classifier few-shot; off by default.
    classifier_few_shot_enabled: bool = False
    classifier_few_shot_max_examples: int = 5
    classifier_few_shot_path: str = "examples/harvested.jsonl"

    ingestion_enabled: bool = True
    ingestion_timeout_seconds: float = 10.0
    ingestion_max_urls_per_drop: int = 3
    youtube_whisper_fallback: bool = False
    arxiv_enrichment: bool = True

    # HARD-CODED defense in depth — user removing this from config.md cannot bypass it.
    _HARD_NEVER_READ: ClassVar[tuple[str, ...]] = ("life/credentials/",)

    @property
    def effective_never_read(self) -> list[str]:
        return sorted(set(self._HARD_NEVER_READ) | set(self.never_read_paths))


@lru_cache(maxsize=1)
def load_vault_config() -> VaultConfig:
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

    if isinstance(meta.get("embedding_model"), str):
        cfg.embedding_model = meta["embedding_model"].strip()
    if isinstance(meta.get("embedding_base_url"), str):
        cfg.embedding_base_url = meta["embedding_base_url"].strip()
    if isinstance(meta.get("embedding_dimensions"), int):
        cfg.embedding_dimensions = max(0, meta["embedding_dimensions"])
    if isinstance(meta.get("embedding_rerank"), bool):
        cfg.embedding_rerank = meta["embedding_rerank"]

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

    if isinstance(meta.get("eval_enabled"), bool):
        cfg.eval_enabled = meta["eval_enabled"]
    if isinstance(meta.get("harvest_onboarding_complete"), bool):
        cfg.harvest_onboarding_complete = meta["harvest_onboarding_complete"]

    if isinstance(meta.get("classifier_few_shot_enabled"), bool):
        cfg.classifier_few_shot_enabled = meta["classifier_few_shot_enabled"]
    if isinstance(meta.get("classifier_few_shot_max_examples"), int):
        cfg.classifier_few_shot_max_examples = max(
            0, min(10, meta["classifier_few_shot_max_examples"])
        )
    # classifier_few_shot_path NOT user-overridable — vault write access must not redirect the classifier prompt.

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
    load_vault_config.cache_clear()
    return load_vault_config()


def set_config_field(key: str, value) -> bool:
    import yaml
    try:
        content = github_store.read(_CONFIG_PATH) or ""
    except Exception as e:
        log.error("set_config_field: read failed: %s", e)
        return False

    meta, body = github_store.parse_metadata(content)
    meta = dict(meta or {})
    meta[key] = value

    new_content = "---\n" + yaml.safe_dump(meta, sort_keys=False).rstrip() + "\n---\n" + (body or "")
    ok = github_store.write(_CONFIG_PATH, new_content, f"monogram: {key}={value}")
    if ok:
        reload_vault_config()
    return ok
