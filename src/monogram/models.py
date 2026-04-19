"""Model resolution and API credential routing (v0.4).

`get_model(tier)` reads the user's mono/config.md configuration only.
No hardcoded model names. No preset defaults (except via endpoint_docs
for the init wizard's "default Gemini" path).

Fallback chain:
  1. VaultConfig.llm_models[tier] (or .single if llm_mode=single)
  2. Legacy MonogramConfig.monogram_model env var (for v0.3 users who
     haven't re-run init)
  3. Raise RuntimeError with helpful message
"""
from __future__ import annotations

import logging
from typing import Literal

from .config import load_config
from .vault_config import load_vault_config

log = logging.getLogger("monogram.models")

Tier = Literal["low", "mid", "high"]


def get_model(tier: Tier) -> str:
    """Resolve the litellm model string for this tier.

    - Single mode: ignores tier, returns llm_models['single'].
    - Tiered mode: returns llm_models[tier].
    - Legacy: if vault config has no llm_provider set, falls back to
      MONOGRAM_MODEL env var (returns same string for every tier).
    """
    vcfg = load_vault_config()

    if vcfg.llm_mode == "single":
        model = vcfg.llm_models.get("single", "").strip()
        if model:
            return model
        raise RuntimeError(
            "llm_mode=single requires llm_models.single in mono/config.md. "
            "Run /config_llm_model_single <model-string> or edit config.md."
        )

    if vcfg.llm_provider:
        model = vcfg.llm_models.get(tier, "").strip()
        if model:
            return model
        raise RuntimeError(
            f"llm_mode=tiered requires llm_models.{tier} in mono/config.md. "
            f"Run /config_llm_model_{tier} <model-string> or edit config.md."
        )

    # Legacy fallback — v0.3 users who haven't updated config.md
    acfg = load_config()
    legacy = (acfg.monogram_model or "").strip()
    if legacy:
        log.warning(
            "Using legacy MONOGRAM_MODEL from .env for tier=%s. "
            "Migrate by setting llm_provider + llm_models in mono/config.md.",
            tier,
        )
        return legacy

    raise RuntimeError(
        "No LLM configured. Set llm_provider + llm_models in "
        "mono/config.md, or run `monogram init` again."
    )


def get_vision_model() -> str | None:
    """Resolve the model string for vision calls.

    Fallback chain:
      1. VaultConfig.llm_models['vision'] (explicit opt-in for BYO users)
      2. 'gemini/gemini-2.5-flash' if GEMINI_API_KEY is set
      3. None → caller should skip vision with a warning, not crash

    Text-only local models (Ollama text-tier qwen/llama/etc) cannot process
    images; routing an image drop to them would 400 or hallucinate.
    """
    vcfg = load_vault_config()
    if vcfg.llm_models.get("vision", "").strip():
        return vcfg.llm_models["vision"].strip()
    acfg = load_config()
    if acfg.gemini_api_key:
        log.info(
            "get_vision_model: no llm_models.vision set; using Gemini fallback"
        )
        return "gemini/gemini-2.5-flash"
    return None


def api_credentials(model: str) -> tuple[str | None, str | None]:
    """Return (api_key, api_base) for a litellm model string.

    Routes credentials by the model's provider prefix. Passes api_base for
    ollama and openai-compat endpoints.
    """
    prefix = model.split("/", 1)[0] if "/" in model else model
    acfg = load_config()
    vcfg = load_vault_config()
    base_url = vcfg.llm_base_url or None

    if prefix == "gemini":
        return (acfg.gemini_api_key or None, None)
    if prefix == "anthropic":
        return (acfg.anthropic_api_key or None, None)
    if prefix == "openai":
        if base_url:
            # openai-compat path (LM Studio, vLLM, LiteLLM proxy) — some
            # servers require any non-empty key; use "dummy" when no real
            # key is set since local servers ignore the value.
            return (acfg.openai_api_key or "dummy", base_url)
        # Real OpenAI — empty key propagates (None) so litellm errors clearly
        # instead of sending "dummy" to api.openai.com.
        return (acfg.openai_api_key or None, None)
    if prefix == "ollama":
        return (None, base_url or "http://localhost:11434")
    return (None, None)


def validate_llm_config() -> list[str]:
    """Return list of human-readable errors. Empty list = config is OK.

    Checks ordering:
      1. Provider set? (else legacy path — check legacy var presence)
      2. Model strings present for the active mode
      3. Credentials present for the configured provider
      4. Mode value sane
    """
    errors: list[str] = []
    vcfg = load_vault_config()
    acfg = load_config()

    provider = vcfg.llm_provider.strip()
    legacy = (acfg.monogram_model or "").strip()

    # v0.5.1: catch the ambiguous state where both are set.
    # vault wins, but silently ignoring the user's MONOGRAM_MODEL is worse
    # than a clear error telling them which to remove.
    if provider and legacy:
        errors.append(
            "Conflicting LLM config: both mono/config.md (llm_provider) and "
            "legacy MONOGRAM_MODEL env var are set. Remove MONOGRAM_MODEL "
            "from .env or clear llm_provider in mono/config.md."
        )
        # continue — still validate the vault path since it's what get_model() will use

    if not provider:
        if not legacy:
            errors.append(
                "No LLM configured. Set llm_provider in mono/config.md "
                "(or fall back to legacy MONOGRAM_MODEL in .env)."
            )
        return errors  # legacy path — no further validation possible

    if vcfg.llm_mode == "single":
        if not vcfg.llm_models.get("single", "").strip():
            errors.append(
                "llm_mode=single requires llm_models.single in config.md"
            )
    elif vcfg.llm_mode == "tiered":
        for tier in ("low", "mid", "high"):
            if not vcfg.llm_models.get(tier, "").strip():
                errors.append(
                    f"llm_mode=tiered requires llm_models.{tier} in config.md"
                )
    else:
        errors.append(
            f"llm_mode must be 'tiered' or 'single', got: {vcfg.llm_mode!r}"
        )

    if provider == "gemini" and not acfg.gemini_api_key:
        errors.append("llm_provider=gemini requires GEMINI_API_KEY in .env")
    elif provider == "anthropic" and not acfg.anthropic_api_key:
        errors.append("llm_provider=anthropic requires ANTHROPIC_API_KEY in .env")
    elif provider == "openai" and not acfg.openai_api_key:
        errors.append("llm_provider=openai requires OPENAI_API_KEY in .env")
    elif provider == "openai-compat":
        if not vcfg.llm_base_url:
            errors.append(
                "llm_provider=openai-compat requires llm_base_url in config.md "
                "(e.g. http://localhost:1234/v1 for LM Studio)"
            )

    return errors


def validate_webui_config() -> list[str]:
    """v0.6 — validate web UI settings. Empty list = OK."""
    import os
    errors: list[str] = []
    vcfg = load_vault_config()
    acfg = load_config()

    mode = vcfg.webui_mode or "mcp-only"
    if mode not in ("gcs", "self-host", "mcp-only"):
        errors.append(
            f"webui_mode must be one of gcs/self-host/mcp-only, got: {mode!r}"
        )
        return errors

    if mode == "mcp-only":
        return errors  # no further validation needed

    # Password required for gcs and self-host.
    from .encryption_layer import validate_password
    pw_errors = validate_password(acfg.monogram_webui_password)
    for err in pw_errors:
        errors.append(f"MONOGRAM_WEBUI_PASSWORD: {err}")

    if mode == "gcs":
        bucket = (vcfg.webui_gcs or {}).get("bucket", "").strip()
        if not bucket:
            errors.append(
                "webui_mode=gcs requires webui_gcs.bucket in mono/config.md"
            )
        creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
        if not creds_path:
            errors.append(
                "webui_mode=gcs requires GOOGLE_APPLICATION_CREDENTIALS in .env"
            )
        elif not os.path.exists(creds_path):
            errors.append(
                f"GOOGLE_APPLICATION_CREDENTIALS points to missing file: {creds_path}"
            )

    if mode == "self-host":
        port = (vcfg.webui_self_host or {}).get("port", 8765)
        try:
            port_int = int(port)
        except (TypeError, ValueError):
            errors.append(f"webui_self_host.port must be an integer, got: {port!r}")
        else:
            if port_int < 1024 or port_int > 65535:
                errors.append(
                    f"webui_self_host.port must be 1024-65535, got: {port_int}"
                )

    return errors
