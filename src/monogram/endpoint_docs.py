"""Per-endpoint reference — docs URLs, format examples, starter values.

NO specific model names in code except Gemini's `default_starter`, which is
used solely by the "default (Gemini free tier)" wizard path. Every other
provider requires the user to enter model strings from the provider's
current docs.

When a provider releases a new model generation, users edit mono/config.md
directly or use the /config_llm_model_* bot commands. No Monogram update
required.
"""
from __future__ import annotations

ENDPOINTS: dict[str, dict] = {
    "gemini": {
        "docs_url": "https://ai.google.dev/gemini-api/docs/models",
        "format": "gemini/<model-name>",
        "notes": (
            "Free tier at aistudio.google.com — generous limits on the "
            "lite-class model. No billing required for personal use."
        ),
        # Used ONLY by "default" wizard path for quick-start.
        # Users can and should edit these in mono/config.md when
        # Google releases new model generations.
        "default_starter": {
            "low": "gemini/gemini-2.5-flash-lite",
            "mid": "gemini/gemini-2.5-flash",
            "high": "gemini/gemini-2.5-pro",
        },
    },
    "anthropic": {
        "docs_url": "https://docs.anthropic.com/en/docs/about-claude/models",
        "format": "anthropic/<model-name>",
        "notes": "API key at console.anthropic.com. Billing required.",
    },
    "openai": {
        "docs_url": "https://platform.openai.com/docs/models",
        "format": "openai/<model-name>",
        "notes": "API key at platform.openai.com. Billing required.",
    },
    "ollama": {
        "docs_url": "https://ollama.com/library",
        "format": "ollama/<model-name>[:tag]",
        "notes": (
            "Requires Ollama running locally or on a reachable host. "
            "Run `ollama list` to see models already installed. "
            "Default base URL: http://localhost:11434"
        ),
    },
    "openai-compat": {
        "docs_url": "https://docs.litellm.ai/docs/providers/openai_compatible",
        "format": "openai/<server-specific-model-name>",
        "notes": (
            "Works with LM Studio, vLLM, LiteLLM proxy, OpenRouter, "
            "text-generation-inference, and any OpenAI-compatible server. "
            "Model names depend on your server — check its /v1/models "
            "endpoint or dashboard. Requires llm_base_url set."
        ),
    },
}

LITELLM_REFERENCE_URL = "https://docs.litellm.ai/docs/providers"


def format_endpoint_help(provider: str) -> str:
    """Return a user-facing multi-line string for CLI/bot display."""
    info = ENDPOINTS.get(provider)
    if not info:
        return (
            f"Unknown provider: {provider}\n"
            f"Supported: {', '.join(ENDPOINTS.keys())}\n"
            f"For other providers, see {LITELLM_REFERENCE_URL}"
        )
    lines = [
        f"→ {provider}",
        f"  Docs:   {info['docs_url']}",
        f"  Format: {info['format']}",
        f"  Notes:  {info['notes']}",
    ]
    return "\n".join(lines)
