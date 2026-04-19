"""A12 — legacy MONOGRAM_MODEL fallback for v0.3 users.

Scenario: user upgrades from v0.3 to v0.4 but doesn't re-run `monogram init`.
Their .env still has MONOGRAM_MODEL, their mono/config.md has no llm_provider.
Pipeline should still work; all tiers resolve to the legacy env var.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from monogram.models import get_model, validate_llm_config
from monogram.vault_config import VaultConfig, load_vault_config


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr("monogram.vault_config.github_store.read", lambda p: "")
    load_vault_config.cache_clear()
    yield
    load_vault_config.cache_clear()


class _LegacyAppCfg:
    monogram_model = "gemini/gemini-2.5-flash-lite"
    gemini_api_key = "AIza_LEGACY"
    anthropic_api_key = ""
    openai_api_key = ""


def test_legacy_env_var_falls_back_all_tiers(caplog):
    # Empty vault config (no llm_provider) + legacy env var
    with patch("monogram.models.load_vault_config", return_value=VaultConfig()), \
         patch("monogram.models.load_config", return_value=_LegacyAppCfg()):
        with caplog.at_level("WARNING"):
            assert get_model("low") == "gemini/gemini-2.5-flash-lite"
            assert get_model("mid") == "gemini/gemini-2.5-flash-lite"
            assert get_model("high") == "gemini/gemini-2.5-flash-lite"
        # Warning should fire at least once
        assert any(
            "legacy MONOGRAM_MODEL" in r.message for r in caplog.records
        )


def test_validate_passes_on_legacy_mode():
    """Legacy setup should not raise validation errors."""
    with patch("monogram.models.load_vault_config", return_value=VaultConfig()), \
         patch("monogram.models.load_config", return_value=_LegacyAppCfg()):
        errs = validate_llm_config()
        assert errs == []


def test_validate_errors_when_everything_empty():
    class Empty:
        monogram_model = ""
        gemini_api_key = ""
        anthropic_api_key = ""
        openai_api_key = ""
    with patch("monogram.models.load_vault_config", return_value=VaultConfig()), \
         patch("monogram.models.load_config", return_value=Empty()):
        errs = validate_llm_config()
        assert errs
        assert any("No LLM configured" in e for e in errs)
