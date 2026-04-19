"""B1 tests — language injection into llm.py system prompts."""
from __future__ import annotations

from unittest.mock import patch

from monogram.llm import _apply_language, _language_instruction
from monogram.vault_config import VaultConfig, load_vault_config


def test_english_returns_empty_instruction():
    assert _language_instruction("en") == ""
    assert _language_instruction("") == ""


def test_korean_instruction_names_language():
    instr = _language_instruction("ko")
    assert "Korean" in instr
    assert "(ko)" in instr
    # Preserves the guardrails for paths/enums
    assert "slug" in instr.lower()
    assert "yaml" in instr.lower() or "frontmatter" in instr.lower()


def test_unknown_code_uses_code_as_name():
    instr = _language_instruction("xx")
    assert "xx" in instr  # uses code when name is unknown
    # Still has the guardrails
    assert "slug" in instr.lower()


def test_apply_language_none_system_for_english(monkeypatch):
    load_vault_config.cache_clear()
    with patch("monogram.vault_config.load_vault_config", return_value=VaultConfig()):
        assert _apply_language(None) is None
        assert _apply_language("existing system") == "existing system"


def test_apply_language_prepends_for_korean(monkeypatch):
    load_vault_config.cache_clear()
    with patch("monogram.vault_config.load_vault_config",
               return_value=VaultConfig(primary_language="ko")):
        result = _apply_language("You are a helper.")
        assert "Korean" in result
        assert "You are a helper." in result
        # Directive comes first
        assert result.index("Korean") < result.index("You are a helper.")


def test_apply_language_handles_none_system_with_korean(monkeypatch):
    load_vault_config.cache_clear()
    with patch("monogram.vault_config.load_vault_config",
               return_value=VaultConfig(primary_language="ja")):
        result = _apply_language(None)
        assert result is not None
        assert "Japanese" in result


def test_apply_language_survives_vault_load_failure(monkeypatch):
    """If VaultConfig fails to load, don't crash LLM calls — just pass through."""
    def boom():
        raise RuntimeError("network down")
    with patch("monogram.vault_config.load_vault_config", side_effect=boom):
        assert _apply_language("hello") == "hello"
        assert _apply_language(None) is None


def test_language_directive_never_touches_english_call_perf():
    """Zero overhead for English users."""
    assert _language_instruction("en") == ""
