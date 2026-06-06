"""LLM wrapper — single entry point for all model calls."""
from __future__ import annotations

import asyncio
import base64
import logging
from contextvars import ContextVar
from functools import cache
from typing import Type, TypeVar

import litellm
from pydantic import BaseModel, ValidationError

from .config import load_config

log = logging.getLogger("monogram.llm")


@cache
def _cfg():
    # Lazy import so tests/--help/MCP don't require a valid .env at import time.
    return load_config()


T = TypeVar("T", bound=BaseModel)


_RETRY_DELAYS = (1.0, 3.0, 7.0)


def _is_transient(exc: Exception) -> bool:
    name = type(exc).__name__
    if name in {
        "RateLimitError", "ServiceUnavailableError", "InternalServerError",
        "APIConnectionError", "Timeout", "APIError",
    }:
        return True
    if getattr(exc, "status_code", None) in {408, 429, 500, 502, 503, 504}:
        return True
    msg = str(exc).lower()
    return any(t in msg for t in (
        "overload", "rate limit", "service unavailable",
        "503", "504", "429", "timeout",
    ))


async def _acompletion_with_retry(kwargs: dict, model_name: str):
    last_exc: Exception | None = None
    for attempt, delay in enumerate(_RETRY_DELAYS):
        try:
            return await litellm.acompletion(**kwargs)
        except Exception as e:  # noqa: BLE001
            last_exc = e
            if not _is_transient(e) or attempt + 1 >= len(_RETRY_DELAYS):
                raise
            log.warning(
                "llm: %s on attempt %d/%d (model=%s); retrying in %.1fs",
                type(e).__name__, attempt + 1, len(_RETRY_DELAYS), model_name, delay,
            )
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc


# Eval-only: evals/cassette.py reads this to route per-agent cassettes; production ignores it.
current_agent_tag: ContextVar[str | None] = ContextVar(
    "monogram_agent_tag", default=None
)


_LANGUAGE_NAMES = {
    "en": "English",
    "ko": "Korean",
    "ja": "Japanese",
    "zh": "Chinese",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
    "it": "Italian",
    "ru": "Russian",
    "ar": "Arabic",
    "hi": "Hindi",
    "vi": "Vietnamese",
    "th": "Thai",
    "id": "Indonesian",
}


def _language_instruction(language: str) -> str:
    # Empty string for English — zero token overhead for the common case.
    if language == "en" or not language:
        return ""
    name = _LANGUAGE_NAMES.get(language, language)
    return (
        "LANGUAGE DIRECTIVE:\n"
        f"The user's primary language is {name} ({language}).\n"
        f"Write all free-form narrative output in {name}: reasoning, title, "
        "summary, content, progress_note, report body, H3 titles in life "
        "entries, and all prose in generated briefs/reports.\n"
        "Keep these fields in lowercase English regardless of language:\n"
        "- target_kind, drop_type, confidence, severity, scope, time_range\n"
        "- life_area values (always match the configured category list)\n"
        "- slugs ([a-z0-9-]+ ASCII)\n"
        "- file paths (projects/, life/, wiki/, daily/, ...)\n"
        "- YAML frontmatter keys (confidence:, sources:, tags:, created:, ...)\n"
        "- JSON field names\n"
        "\n"
    )


def _apply_language(system: str | None) -> str | None:
    # Lazy import to avoid circular-import if llm is loaded before vault_config.
    try:
        from .vault_config import load_vault_config
        cfg = load_vault_config()
    except Exception:
        return system
    directive = _language_instruction(cfg.primary_language)
    if not directive:
        return system
    if system is None:
        return directive.rstrip()
    return directive + system


def _credentials_for(model: str) -> tuple[str | None, str | None]:
    # Lazy import to avoid circular dependency (models imports llm).
    from .models import api_credentials
    return api_credentials(model)


def _log_usage(response, model: str) -> None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    log.debug(
        "llm.call model=%s prompt=%s completion=%s total=%s",
        model,
        getattr(usage, "prompt_tokens", "?"),
        getattr(usage, "completion_tokens", "?"),
        getattr(usage, "total_tokens", "?"),
    )


async def complete(
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
    temperature: float = 0.3,
    response_format: dict | Type[BaseModel] | None = None,
    max_output_tokens: int | None = None,
    agent_tag: str | None = None,
) -> str:
    chosen = model or _cfg().monogram_model
    system = _apply_language(system)

    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    api_key, api_base = _credentials_for(chosen)
    kwargs: dict = {
        "model": chosen,
        "messages": messages,
        "temperature": temperature,
    }
    if api_key:
        kwargs["api_key"] = api_key
    if api_base:
        kwargs["api_base"] = api_base
    if response_format is not None:
        kwargs["response_format"] = response_format
    if max_output_tokens is not None:
        kwargs["max_tokens"] = max_output_tokens

    token = current_agent_tag.set(agent_tag) if agent_tag is not None else None
    try:
        response = await _acompletion_with_retry(kwargs, chosen)
    finally:
        if token is not None:
            current_agent_tag.reset(token)
    _log_usage(response, chosen)
    # content is None on provider safety-filter block; "" is safer downstream.
    return response.choices[0].message.content or ""


async def extract(
    prompt: str,
    schema: Type[T],
    *,
    system: str | None = None,
    model: str | None = None,
    temperature: float = 0.1,
    agent_tag: str | None = None,
) -> T:
    last_exc: Exception | None = None
    for attempt in range(2):
        raw = await complete(
            prompt,
            system=system,
            model=model,
            # Vary temperature on retry so a deterministic bad parse can differ.
            temperature=temperature if attempt == 0 else max(temperature, 0.5),
            response_format=schema,
            agent_tag=agent_tag,
        )
        try:
            return schema.model_validate_json(raw)
        except ValidationError as e:
            last_exc = e
            log.warning("llm.extract: validation failed (attempt %d/2): %s", attempt + 1, e)
    assert last_exc is not None
    raise last_exc


async def complete_vision(
    image_bytes: bytes,
    prompt: str,
    *,
    mime_type: str = "image/jpeg",
    model: str | None = None,
    temperature: float = 0.3,
    agent_tag: str | None = None,
) -> str:
    chosen = model or _cfg().monogram_model
    b64 = base64.b64encode(image_bytes).decode()
    api_key, api_base = _credentials_for(chosen)
    vision_kwargs: dict = {
        "model": chosen,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": f"data:{mime_type};base64,{b64}",
                    },
                ],
            }
        ],
        "temperature": temperature,
    }
    if api_key:
        vision_kwargs["api_key"] = api_key
    if api_base:
        vision_kwargs["api_base"] = api_base

    token = current_agent_tag.set(agent_tag) if agent_tag is not None else None
    try:
        response = await _acompletion_with_retry(vision_kwargs, chosen)
    finally:
        if token is not None:
            current_agent_tag.reset(token)
    _log_usage(response, chosen)
    return response.choices[0].message.content or ""
