"""Model resolution and API credential routing."""
from __future__ import annotations

import logging
from typing import Literal

from .config import load_config
from .vault_config import load_vault_config

log = logging.getLogger("monogram.models")

Tier = Literal["low", "mid", "high"]


def get_model(tier: Tier) -> str:
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

    # Legacy fallback for v0.3 users who haven't re-run init.
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
    # Text-only Ollama models 400 or hallucinate on images; None signals caller to skip.
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


def embedding_credentials(model: str) -> tuple[str | None, str | None]:
    # embedding_base_url is decoupled from chat base_url so chat=cloud + embed=local works.
    acfg = load_config()
    vcfg = load_vault_config()
    prefix = model.split("/", 1)[0]
    base = (vcfg.embedding_base_url or "").strip() or None

    if prefix == "gemini":
        return (acfg.gemini_api_key or None, None)
    if prefix == "openai":
        if base:
            return (acfg.openai_api_key or "dummy", base)
        return (acfg.openai_api_key or None, None)
    if prefix == "ollama":
        return (None, base or vcfg.llm_base_url or "http://localhost:11434")
    return (None, base)  # voyage/cohere/jina/etc — litellm reads the key from env


def api_credentials(model: str) -> tuple[str | None, str | None]:
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
            # openai-compat servers (LM Studio, vLLM) ignore key value; "dummy" avoids empty-key rejection.
            return (acfg.openai_api_key or "dummy", base_url)
        # Real OpenAI: propagate None so litellm errors clearly instead of sending "dummy".
        return (acfg.openai_api_key or None, None)
    if prefix == "ollama":
        return (None, base_url or "http://localhost:11434")
    return (None, None)


def validate_llm_config() -> list[str]:
    errors: list[str] = []
    vcfg = load_vault_config()
    acfg = load_config()

    provider = vcfg.llm_provider.strip()
    legacy = (acfg.monogram_model or "").strip()

    # Warn explicitly: vault wins, but silently ignoring MONOGRAM_MODEL would confuse users.
    if provider and legacy:
        errors.append(
            "Conflicting LLM config: both mono/config.md (llm_provider) and "
            "legacy MONOGRAM_MODEL env var are set. Remove MONOGRAM_MODEL "
            "from .env or clear llm_provider in mono/config.md."
        )

    if not provider:
        if not legacy:
            errors.append(
                "No LLM configured. Set llm_provider in mono/config.md "
                "(or fall back to legacy MONOGRAM_MODEL in .env)."
            )
        return errors

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
        return errors

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
