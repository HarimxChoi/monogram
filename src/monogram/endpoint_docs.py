"""Provider endpoint reference — docs, format, notes. No model names except Gemini default_starter."""
from __future__ import annotations

ENDPOINTS: dict[str, dict] = {
    "gemini": {
        "docs_url": "https://ai.google.dev/gemini-api/docs/models",
        "format": "gemini/<model-name>",
        "notes": (
            "Free tier at aistudio.google.com — generous limits on the "
            "lite-class model. No billing required for personal use."
        ),
        # Used only by the "default" wizard path; users update via config.md.
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
