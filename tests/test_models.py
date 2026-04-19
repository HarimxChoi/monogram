"""A3 tests — model resolution + credential routing + config validation."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from monogram.models import api_credentials, get_model, validate_llm_config
from monogram.vault_config import VaultConfig, load_vault_config


@pytest.fixture(autouse=True)
def _clear_cache(monkeypatch):
    monkeypatch.setattr("monogram.vault_config.github_store.read", lambda p: "")
    load_vault_config.cache_clear()
    yield
    load_vault_config.cache_clear()


def _tiered_cfg(**models):
    return VaultConfig(
        llm_provider="anthropic",
        llm_mode="tiered",
        llm_models=models,
    )


def _single_cfg(model):
    return VaultConfig(
        llm_provider="anthropic",
        llm_mode="single",
        llm_models={"single": model},
    )


# ── get_model tiered ──


def test_get_model_tiered_returns_per_tier():
    with patch("monogram.models.load_vault_config", return_value=_tiered_cfg(
        low="anthropic/claude-haiku-4-5",
        mid="anthropic/claude-sonnet-4-6",
        high="anthropic/claude-opus-4-7",
    )):
        assert get_model("low") == "anthropic/claude-haiku-4-5"
        assert get_model("mid") == "anthropic/claude-sonnet-4-6"
        assert get_model("high") == "anthropic/claude-opus-4-7"


def test_get_model_tiered_missing_tier_raises():
    with patch("monogram.models.load_vault_config", return_value=_tiered_cfg(
        low="anthropic/claude-haiku-4-5",
        # mid missing
        high="anthropic/claude-opus-4-7",
    )):
        with pytest.raises(RuntimeError, match="llm_models.mid"):
            get_model("mid")


# ── get_model single ──


def test_get_model_single_ignores_tier():
    with patch("monogram.models.load_vault_config",
               return_value=_single_cfg("anthropic/claude-sonnet-4-6")):
        assert get_model("low") == "anthropic/claude-sonnet-4-6"
        assert get_model("mid") == "anthropic/claude-sonnet-4-6"
        assert get_model("high") == "anthropic/claude-sonnet-4-6"


def test_get_model_single_missing_raises():
    with patch("monogram.models.load_vault_config",
               return_value=VaultConfig(
                   llm_provider="anthropic",
                   llm_mode="single",
                   llm_models={},  # no 'single'
               )):
        with pytest.raises(RuntimeError, match="llm_models.single"):
            get_model("low")


# ── legacy fallback ──


def test_get_model_legacy_env_var_fallback(caplog):
    """v0.3 users who haven't re-run init: no llm_provider set in vault,
    but MONOGRAM_MODEL is set in .env."""
    class FakeAppCfg:
        monogram_model = "gemini/gemini-2.5-flash-lite"
    with patch("monogram.models.load_vault_config", return_value=VaultConfig()), \
         patch("monogram.models.load_config", return_value=FakeAppCfg()):
        with caplog.at_level("WARNING"):
            for tier in ("low", "mid", "high"):
                assert get_model(tier) == "gemini/gemini-2.5-flash-lite"
        assert any("legacy MONOGRAM_MODEL" in r.message for r in caplog.records)


def test_get_model_nothing_configured_raises():
    class FakeAppCfg:
        monogram_model = ""
    with patch("monogram.models.load_vault_config", return_value=VaultConfig()), \
         patch("monogram.models.load_config", return_value=FakeAppCfg()):
        with pytest.raises(RuntimeError, match="No LLM configured"):
            get_model("low")


# ── api_credentials ──


def test_api_credentials_gemini():
    class FakeAppCfg:
        gemini_api_key = "AIza_TEST"
        anthropic_api_key = ""
        openai_api_key = ""
    with patch("monogram.models.load_config", return_value=FakeAppCfg()), \
         patch("monogram.models.load_vault_config", return_value=VaultConfig()):
        key, base = api_credentials("gemini/gemini-2.5-flash")
        assert key == "AIza_TEST"
        assert base is None


def test_api_credentials_anthropic():
    class FakeAppCfg:
        gemini_api_key = ""
        anthropic_api_key = "sk-ant-TEST"
        openai_api_key = ""
    with patch("monogram.models.load_config", return_value=FakeAppCfg()), \
         patch("monogram.models.load_vault_config", return_value=VaultConfig()):
        key, base = api_credentials("anthropic/claude-haiku-4-5")
        assert key == "sk-ant-TEST"
        assert base is None


def test_api_credentials_ollama_uses_base_url():
    class FakeAppCfg:
        gemini_api_key = ""
        anthropic_api_key = ""
        openai_api_key = ""
    vcfg = VaultConfig(llm_base_url="http://localhost:11434")
    with patch("monogram.models.load_config", return_value=FakeAppCfg()), \
         patch("monogram.models.load_vault_config", return_value=vcfg):
        key, base = api_credentials("ollama/qwen2.5:7b")
        assert key is None
        assert base == "http://localhost:11434"


def test_api_credentials_openai_compat_uses_dummy_key():
    """Local OpenAI-compatible server: no real key needed."""
    class FakeAppCfg:
        gemini_api_key = ""
        anthropic_api_key = ""
        openai_api_key = ""
    vcfg = VaultConfig(llm_base_url="http://localhost:1234/v1")
    with patch("monogram.models.load_config", return_value=FakeAppCfg()), \
         patch("monogram.models.load_vault_config", return_value=vcfg):
        key, base = api_credentials("openai/some-local-model")
        assert key == "dummy"
        assert base == "http://localhost:1234/v1"


def test_api_credentials_real_openai_no_dummy():
    """No base_url → real OpenAI → empty key propagates as None, NOT 'dummy'."""
    class FakeAppCfg:
        gemini_api_key = ""
        anthropic_api_key = ""
        openai_api_key = ""
    with patch("monogram.models.load_config", return_value=FakeAppCfg()), \
         patch("monogram.models.load_vault_config", return_value=VaultConfig()):
        key, base = api_credentials("openai/gpt-5")
        assert key is None  # Do NOT send "dummy" to api.openai.com
        assert base is None


# ── validate_llm_config ──


def _fake_app(**kw):
    defaults = dict(
        monogram_model="", gemini_api_key="", anthropic_api_key="",
        openai_api_key="",
    )
    defaults.update(kw)
    ns = type("FakeCfg", (), defaults)
    return ns


def test_validate_no_provider_no_legacy():
    with patch("monogram.models.load_vault_config", return_value=VaultConfig()), \
         patch("monogram.models.load_config", return_value=_fake_app()):
        errs = validate_llm_config()
        assert any("No LLM configured" in e for e in errs)


def test_validate_provider_missing_tier():
    vcfg = VaultConfig(
        llm_provider="anthropic",
        llm_mode="tiered",
        llm_models={"low": "anthropic/a"},  # missing mid, high
    )
    app = _fake_app(anthropic_api_key="sk-TEST")
    with patch("monogram.models.load_vault_config", return_value=vcfg), \
         patch("monogram.models.load_config", return_value=app):
        errs = validate_llm_config()
        assert any("llm_models.mid" in e for e in errs)
        assert any("llm_models.high" in e for e in errs)


def test_validate_missing_credentials():
    vcfg = VaultConfig(
        llm_provider="anthropic",
        llm_mode="single",
        llm_models={"single": "anthropic/a"},
    )
    app = _fake_app()  # no anthropic_api_key
    with patch("monogram.models.load_vault_config", return_value=vcfg), \
         patch("monogram.models.load_config", return_value=app):
        errs = validate_llm_config()
        assert any("ANTHROPIC_API_KEY" in e for e in errs)


def test_validate_openai_compat_without_base_url():
    vcfg = VaultConfig(
        llm_provider="openai-compat",
        llm_mode="single",
        llm_models={"single": "openai/local"},
        llm_base_url="",
    )
    app = _fake_app(openai_api_key="dummy")
    with patch("monogram.models.load_vault_config", return_value=vcfg), \
         patch("monogram.models.load_config", return_value=app):
        errs = validate_llm_config()
        assert any("openai-compat requires llm_base_url" in e for e in errs)


def test_validate_invalid_llm_mode():
    vcfg = VaultConfig(
        llm_provider="anthropic",
        llm_mode="potato",  # invalid
        llm_models={"single": "anthropic/a"},
    )
    app = _fake_app(anthropic_api_key="sk-TEST")
    with patch("monogram.models.load_vault_config", return_value=vcfg), \
         patch("monogram.models.load_config", return_value=app):
        errs = validate_llm_config()
        assert any("llm_mode must be" in e for e in errs)


def test_validate_legacy_path_ok():
    """No provider set + MONOGRAM_MODEL set → no errors (legacy OK)."""
    with patch("monogram.models.load_vault_config", return_value=VaultConfig()), \
         patch("monogram.models.load_config",
               return_value=_fake_app(monogram_model="gemini/gemini-2.5-flash-lite")):
        errs = validate_llm_config()
        assert errs == []


def test_validate_tiered_ok():
    vcfg = VaultConfig(
        llm_provider="anthropic",
        llm_mode="tiered",
        llm_models={"low": "anthropic/a", "mid": "anthropic/b", "high": "anthropic/c"},
    )
    with patch("monogram.models.load_vault_config", return_value=vcfg), \
         patch("monogram.models.load_config",
               return_value=_fake_app(anthropic_api_key="sk-TEST")):
        errs = validate_llm_config()
        assert errs == []
