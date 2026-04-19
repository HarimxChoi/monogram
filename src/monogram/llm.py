"""LLM wrapper — single entry point for all model calls.

Design (see docs/architecture.md for sources):
- Provider-agnostic via litellm.
- Pydantic-first structured extraction via provider-native structured-output.
- Usage logged per call (tokens / model) so cost + audit work downstream.
- v0.3b: auto-injects VaultConfig.primary_language into every system prompt
  so free-form LLM output (reasoning, titles, summaries, brief bodies) is
  written in the user's language. Enum/Literal values, slugs, YAML keys,
  and paths stay English.
- v0.7 (D1-A): optional `agent_tag` parameter threads an identifier into a
  ContextVar that eval-layer code can read to route cassette replays per
  agent. Production code passes "classifier" / "extractor" / "verifier" /
  "orchestrator"; the tag is stripped before the litellm call — it never
  crosses the provider boundary. When eval is not installed or not running,
  the tag is a no-op.
"""
from __future__ import annotations

import base64
import logging
from contextvars import ContextVar
from typing import Type, TypeVar

import litellm
from pydantic import BaseModel

from .config import load_config

log = logging.getLogger("monogram.llm")

_config = load_config()
T = TypeVar("T", bound=BaseModel)


# ─── Public: eval layer reads this ContextVar ────────────────────────────
#
# Agent modules pass agent_tag="classifier" etc. to complete/extract/
# complete_vision. This sets the ContextVar for the duration of the call.
# evals/cassette.py reads `current_agent_tag.get()` to decide which
# per-agent cassette file to route a given litellm call to.
#
# Production never reads this — the shim only exists in test/eval context.
# Default None means "no agent context" — cassette falls through to _misc.
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
    """Return a language directive to prepend to system prompts.

    Empty string for English (default) — zero token overhead for the common case.
    """
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
    """Prepend language directive to a system prompt. Safe to call with None.

    Lazily imports VaultConfig to avoid circular-import issues if llm is
    imported before vault_config is ready.
    """
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
    """Lazy wrapper around models.api_credentials to avoid circular import."""
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
    """Text completion. Returns raw string content.

    Auto-injects VaultConfig.primary_language directive into the system prompt.

    `agent_tag` (optional): identifier like "classifier" or "extractor". Sets
    a ContextVar for the duration of the call so the eval cassette shim can
    route to per-agent cassette files. The tag is NOT forwarded to litellm.
    """
    chosen = model or _config.monogram_model
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
        response = await litellm.acompletion(**kwargs)
    finally:
        if token is not None:
            current_agent_tag.reset(token)
    _log_usage(response, chosen)
    return response.choices[0].message.content


async def extract(
    prompt: str,
    schema: Type[T],
    *,
    system: str | None = None,
    model: str | None = None,
    temperature: float = 0.1,
    agent_tag: str | None = None,
) -> T:
    """Structured extraction — validated Pydantic instance via native schema mode.

    `agent_tag` is passed through to `complete()`; see its docstring.
    """
    raw = await complete(
        prompt,
        system=system,
        model=model,
        temperature=temperature,
        response_format=schema,
        agent_tag=agent_tag,
    )
    return schema.model_validate_json(raw)


async def complete_vision(
    image_bytes: bytes,
    prompt: str,
    *,
    mime_type: str = "image/jpeg",
    model: str | None = None,
    temperature: float = 0.3,
    agent_tag: str | None = None,
) -> str:
    """Multimodal call. Returns raw text. Language directive applied to system-role
    message would help, but vision calls here have no system prompt by convention;
    the vision prompt itself should mention language if needed.

    `agent_tag` routes cassette for eval. See `complete()` docstring.
    """
    chosen = model or _config.monogram_model
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
        response = await litellm.acompletion(**vision_kwargs)
    finally:
        if token is not None:
            current_agent_tag.reset(token)
    _log_usage(response, chosen)
    return response.choices[0].message.content
