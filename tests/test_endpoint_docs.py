"""A1 tests — endpoint_docs data module."""
from __future__ import annotations

from monogram.endpoint_docs import ENDPOINTS, format_endpoint_help


def test_all_expected_providers_present():
    for p in ("gemini", "anthropic", "openai", "ollama", "openai-compat"):
        assert p in ENDPOINTS
        assert "docs_url" in ENDPOINTS[p]
        assert "format" in ENDPOINTS[p]
        assert "notes" in ENDPOINTS[p]


def test_only_gemini_has_default_starter():
    assert "default_starter" in ENDPOINTS["gemini"]
    for p in ("anthropic", "openai", "ollama", "openai-compat"):
        assert "default_starter" not in ENDPOINTS[p], (
            f"{p} must NOT have hardcoded model starters"
        )


def test_format_help_for_known_provider():
    out = format_endpoint_help("anthropic")
    assert "anthropic" in out
    assert "https://docs.anthropic.com" in out
    assert "anthropic/<model-name>" in out


def test_format_help_for_unknown_provider():
    out = format_endpoint_help("totally-fake-provider")
    assert "Unknown provider" in out
    assert "Supported:" in out
    assert "litellm" in out.lower()


def test_gemini_default_starter_has_three_tiers():
    ds = ENDPOINTS["gemini"]["default_starter"]
    assert set(ds.keys()) == {"low", "mid", "high"}
    for v in ds.values():
        assert v.startswith("gemini/")
