"""VaultConfig tests — mocked github_store, no real repo reads."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from monogram.vault_config import VaultConfig, load_vault_config, reload_vault_config


@pytest.fixture(autouse=True)
def _clear_cache():
    load_vault_config.cache_clear()
    yield
    load_vault_config.cache_clear()


@patch("monogram.vault_config.github_store")
def test_missing_config_returns_defaults(mock_store):
    mock_store.read.return_value = ""
    cfg = load_vault_config()
    assert cfg.primary_language == "en"
    assert "shopping" in cfg.life_categories
    assert "credentials" in cfg.life_categories


@patch("monogram.vault_config.github_store")
def test_malformed_yaml_falls_back_to_defaults(mock_store):
    mock_store.read.return_value = "---\n{{not: valid yaml::\n---\n\nbody"
    mock_store.parse_metadata.side_effect = Exception("YAML parse error")
    cfg = load_vault_config()
    assert cfg.primary_language == "en"
    assert cfg.life_categories == VaultConfig().life_categories


@patch("monogram.vault_config.github_store")
def test_language_override(mock_store):
    mock_store.read.return_value = (
        "---\nprimary_language: ko\n---\n\nbody"
    )
    mock_store.parse_metadata.return_value = ({"primary_language": "ko"}, "body")
    cfg = load_vault_config()
    assert cfg.primary_language == "ko"


@patch("monogram.vault_config.github_store")
def test_life_categories_override(mock_store):
    mock_store.read.return_value = "---\nlife_categories: [a, b]\n---"
    mock_store.parse_metadata.return_value = (
        {"life_categories": ["hobbies", "travel"]},
        "",
    )
    cfg = load_vault_config()
    assert cfg.life_categories == ["hobbies", "travel"]
    # Defaults are NOT merged — user-supplied list is authoritative
    assert "shopping" not in cfg.life_categories


@patch("monogram.vault_config.github_store")
def test_never_read_paths_union_with_hardcoded(mock_store):
    """Hard-coded life/credentials/ is always present, even if user removes it."""
    mock_store.read.return_value = "---\nnever_read_paths: [other/secret/]\n---"
    mock_store.parse_metadata.return_value = (
        {"never_read_paths": ["other/secret/"]},
        "",
    )
    cfg = load_vault_config()
    assert "life/credentials/" in cfg.effective_never_read
    assert "other/secret/" in cfg.effective_never_read


@patch("monogram.vault_config.github_store")
def test_effective_never_read_includes_hardcoded_even_if_user_empties(mock_store):
    mock_store.read.return_value = "---\nnever_read_paths: []\n---"
    mock_store.parse_metadata.return_value = ({"never_read_paths": []}, "")
    cfg = load_vault_config()
    assert "life/credentials/" in cfg.effective_never_read


# ── v0.4 LLM configuration ──────────────────────────────────────────────


@patch("monogram.vault_config.github_store")
def test_llm_config_tiered_mode_parses(mock_store):
    mock_store.read.return_value = "---\n...\n---"
    mock_store.parse_metadata.return_value = (
        {
            "llm_provider": "anthropic",
            "llm_mode": "tiered",
            "llm_models": {
                "low": "anthropic/claude-haiku-4-5",
                "mid": "anthropic/claude-sonnet-4-6",
                "high": "anthropic/claude-opus-4-7",
            },
            "llm_base_url": "",
        },
        "",
    )
    cfg = load_vault_config()
    assert cfg.llm_provider == "anthropic"
    assert cfg.llm_mode == "tiered"
    assert cfg.llm_models["low"] == "anthropic/claude-haiku-4-5"
    assert cfg.llm_models["high"] == "anthropic/claude-opus-4-7"


@patch("monogram.vault_config.github_store")
def test_llm_config_single_mode_parses(mock_store):
    mock_store.read.return_value = "---\n...\n---"
    mock_store.parse_metadata.return_value = (
        {
            "llm_provider": "anthropic",
            "llm_mode": "single",
            "llm_models": {"single": "anthropic/claude-sonnet-4-6"},
        },
        "",
    )
    cfg = load_vault_config()
    assert cfg.llm_mode == "single"
    assert cfg.llm_models == {"single": "anthropic/claude-sonnet-4-6"}


@patch("monogram.vault_config.github_store")
def test_llm_config_defaults_when_missing(mock_store):
    mock_store.read.return_value = ""
    cfg = load_vault_config()
    assert cfg.llm_provider == ""  # legacy path
    assert cfg.llm_mode == "tiered"  # default
    assert cfg.llm_models == {}
    assert cfg.llm_base_url == ""


@patch("monogram.vault_config.github_store")
def test_malformed_llm_models_filters_out_bad_entries(mock_store):
    mock_store.read.return_value = "---\n...\n---"
    mock_store.parse_metadata.return_value = (
        {
            "llm_provider": "ollama",
            "llm_mode": "tiered",
            "llm_models": {
                "low": "ollama/qwen",
                "mid": "",  # empty string — filtered
                "high": 42,  # non-string — filtered
                5: "bad-key",  # non-string key — filtered
            },
        },
        "",
    )
    cfg = load_vault_config()
    assert cfg.llm_models == {"low": "ollama/qwen"}


@patch("monogram.vault_config.github_store")
def test_invalid_llm_mode_falls_back_to_tiered(mock_store):
    mock_store.read.return_value = "---\n...\n---"
    mock_store.parse_metadata.return_value = (
        {"llm_mode": "potato"},
        "",
    )
    cfg = load_vault_config()
    assert cfg.llm_mode == "tiered"


@patch("monogram.vault_config.github_store")
def test_llm_base_url_parses(mock_store):
    mock_store.read.return_value = "---\n...\n---"
    mock_store.parse_metadata.return_value = (
        {"llm_base_url": "http://localhost:11434"},
        "",
    )
    cfg = load_vault_config()
    assert cfg.llm_base_url == "http://localhost:11434"


@patch("monogram.vault_config.github_store")
def test_reload_clears_cache(mock_store):
    mock_store.read.return_value = ""
    cfg1 = load_vault_config()
    mock_store.read.return_value = "---\nprimary_language: ja\n---"
    mock_store.parse_metadata.return_value = ({"primary_language": "ja"}, "")
    cfg2 = load_vault_config()
    # lru_cache means same object until reload
    assert cfg1 is cfg2
    cfg3 = reload_vault_config()
    assert cfg3.primary_language == "ja"
