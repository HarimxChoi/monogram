"""A10 tests — /config_llm_* bot commands.

Uses mocked github_store + VaultConfig; no real Telegram, no real LLM.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monogram.vault_config import VaultConfig, load_vault_config


@pytest.fixture(autouse=True)
def _clear_cache():
    load_vault_config.cache_clear()
    yield
    load_vault_config.cache_clear()


def _make_msg(text: str, user_id: int | None = None):
    """Fake aiogram Message with answer() as AsyncMock."""
    from monogram.bot_config_cmds import _cfg
    uid = user_id if user_id is not None else _cfg.telegram_user_id
    msg = MagicMock()
    msg.text = text
    msg.from_user = SimpleNamespace(id=uid)
    msg.answer = AsyncMock()
    return msg


@patch("monogram.bot_config_cmds.reload_vault_config")
@patch("monogram.bot_config_cmds.github_store")
def test_set_provider_writes_config(mock_store, mock_reload):
    from monogram.bot_config_cmds import set_provider

    mock_store.read.return_value = "---\nprimary_language: en\n---\n"
    mock_store.parse_metadata.return_value = ({"primary_language": "en"}, "")
    mock_store.serialize_with_metadata.side_effect = (
        lambda m, b: f"---\n{m}\n---\n\n{b}"
    )
    mock_store.write.return_value = True

    msg = _make_msg("/config_llm_provider anthropic")
    asyncio.run(set_provider(msg))

    # github_store.write was called; meta dict has new llm_provider
    write_call = mock_store.write.call_args
    assert write_call is not None
    content = write_call[0][1]
    assert "'llm_provider': 'anthropic'" in content or "anthropic" in content
    mock_reload.assert_called_once()
    msg.answer.assert_awaited_once()


@patch("monogram.bot_config_cmds.github_store")
def test_set_provider_rejects_unknown(mock_store):
    from monogram.bot_config_cmds import set_provider
    msg = _make_msg("/config_llm_provider totally-fake")
    asyncio.run(set_provider(msg))
    msg.answer.assert_awaited_once()
    reply = msg.answer.call_args[0][0]
    assert "Unknown provider" in reply
    # Should not write
    mock_store.write.assert_not_called()


@patch("monogram.bot_config_cmds.reload_vault_config")
@patch("monogram.bot_config_cmds.github_store")
def test_set_mode_rejects_invalid(mock_store, mock_reload):
    from monogram.bot_config_cmds import set_mode
    msg = _make_msg("/config_llm_mode potato")
    asyncio.run(set_mode(msg))
    mock_store.write.assert_not_called()
    mock_reload.assert_not_called()


@patch("monogram.bot_config_cmds.github_store")
def test_unauthorized_user_noop(mock_store):
    from monogram.bot_config_cmds import set_provider
    msg = _make_msg("/config_llm_provider anthropic", user_id=99999999)
    asyncio.run(set_provider(msg))
    msg.answer.assert_not_called()
    mock_store.write.assert_not_called()


@patch("monogram.bot_config_cmds.reload_vault_config")
@patch("monogram.bot_config_cmds.github_store")
def test_set_model_low_updates_nested_dict(mock_store, mock_reload):
    from monogram.bot_config_cmds import set_low
    mock_store.read.return_value = "---\nllm_models:\n  low: old\n---"
    mock_store.parse_metadata.return_value = (
        {"llm_models": {"low": "old"}}, ""
    )
    captured = {}

    def fake_serialize(meta, body):
        captured["meta"] = meta
        return "---\nsome-yaml\n---\n\n"

    mock_store.serialize_with_metadata.side_effect = fake_serialize
    mock_store.write.return_value = True

    msg = _make_msg("/config_llm_model_low anthropic/claude-haiku-4-5")
    asyncio.run(set_low(msg))

    assert captured["meta"]["llm_models"]["low"] == "anthropic/claude-haiku-4-5"
    mock_reload.assert_called_once()


@patch("monogram.bot_config_cmds.reload_vault_config")
@patch("monogram.bot_config_cmds.github_store")
def test_base_url_empty_clears_field(mock_store, mock_reload):
    from monogram.bot_config_cmds import set_base_url
    mock_store.read.return_value = "---\nllm_base_url: http://localhost:11434\n---"
    mock_store.parse_metadata.return_value = (
        {"llm_base_url": "http://localhost:11434"}, ""
    )
    captured = {}
    mock_store.serialize_with_metadata.side_effect = (
        lambda m, b: (captured.setdefault("meta", m), "---\n---")[1]
    )
    mock_store.write.return_value = True

    msg = _make_msg("/config_llm_base_url")
    asyncio.run(set_base_url(msg))
    assert captured["meta"]["llm_base_url"] == ""


@patch("monogram.vault_config.github_store.read", return_value="")
def test_show_llm(mock_vault_read):
    from monogram.bot_config_cmds import show_llm
    msg = _make_msg("/config_llm")
    asyncio.run(show_llm(msg))
    reply = msg.answer.call_args[0][0]
    assert "LLM configuration" in reply
    assert "Provider" in reply
    assert "Mode" in reply


def test_help_for_known_endpoint():
    from monogram.bot_config_cmds import llm_help
    msg = _make_msg("/config_llm_help anthropic")
    asyncio.run(llm_help(msg))
    reply = msg.answer.call_args[0][0]
    assert "anthropic" in reply
    assert "docs" in reply.lower()


def test_help_no_arg_lists_endpoints():
    from monogram.bot_config_cmds import llm_help
    msg = _make_msg("/config_llm_help")
    asyncio.run(llm_help(msg))
    reply = msg.answer.call_args[0][0]
    assert "Usage" in reply
    assert "gemini" in reply


@patch("monogram.bot_config_cmds.reload_vault_config")
@patch("monogram.vault_config.github_store.read", return_value="")
def test_config_reload(mock_vread, mock_reload):
    from monogram.bot_config_cmds import reload_config
    msg = _make_msg("/config_reload")
    asyncio.run(reload_config(msg))
    mock_reload.assert_called_once()
    reply = msg.answer.call_args[0][0]
    assert "reloaded" in reply.lower()
