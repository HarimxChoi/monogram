"""v0.6 — /webui and /config_webui_* bot command tests."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monogram.vault_config import VaultConfig, load_vault_config


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr("monogram.vault_config.github_store.read", lambda p: "")
    load_vault_config.cache_clear()
    yield
    load_vault_config.cache_clear()


def _make_msg(text: str, user_id: int | None = None):
    from monogram.bot_webui_cmds import _cfg
    uid = user_id if user_id is not None else _cfg().telegram_user_id
    msg = MagicMock()
    msg.text = text
    msg.from_user = SimpleNamespace(id=uid)
    msg.answer = AsyncMock()
    return msg


@patch("monogram.bot_webui_cmds.reload_vault_config")
@patch("monogram.bot_webui_cmds.github_store")
def test_set_mode_writes_config(mock_store, mock_reload):
    from monogram.bot_webui_cmds import set_mode
    mock_store.read.return_value = "---\n---\n"
    mock_store.parse_metadata.return_value = ({}, "")
    captured: dict = {}

    def fake_serialize(meta, body):
        captured["meta"] = dict(meta)
        return f"---\n{meta}\n---\n\n{body}"

    mock_store.serialize_with_metadata.side_effect = fake_serialize
    mock_store.write.return_value = True

    msg = _make_msg("/config_webui_mode gcs")
    asyncio.run(set_mode(msg))

    assert captured["meta"].get("webui_mode") == "gcs"
    mock_reload.assert_called_once()
    msg.answer.assert_awaited_once()


@patch("monogram.bot_webui_cmds.github_store")
def test_set_mode_rejects_invalid(mock_store):
    from monogram.bot_webui_cmds import set_mode
    msg = _make_msg("/config_webui_mode potato")
    asyncio.run(set_mode(msg))
    mock_store.write.assert_not_called()
    reply = msg.answer.call_args[0][0]
    assert "Usage" in reply


@patch("monogram.bot_webui_cmds.reload_vault_config")
@patch("monogram.bot_webui_cmds.github_store")
def test_set_port_coerces_to_int(mock_store, mock_reload):
    from monogram.bot_webui_cmds import set_port
    mock_store.read.return_value = ""
    mock_store.parse_metadata.return_value = ({}, "")
    captured: dict = {}
    mock_store.serialize_with_metadata.side_effect = (
        lambda m, b: (captured.setdefault("meta", dict(m)), "---\n")[1]
    )
    mock_store.write.return_value = True

    msg = _make_msg("/config_webui_self_host_port 9000")
    asyncio.run(set_port(msg))
    assert captured["meta"]["webui_self_host"]["port"] == 9000


@patch("monogram.bot_webui_cmds.github_store")
def test_set_port_out_of_range(mock_store):
    from monogram.bot_webui_cmds import set_port
    msg = _make_msg("/config_webui_self_host_port 80")
    asyncio.run(set_port(msg))
    mock_store.write.assert_not_called()
    reply = msg.answer.call_args[0][0]
    assert "range" in reply.lower()


def test_webui_command_mcp_only_replies_suggestion(monkeypatch):
    from monogram.bot_webui_cmds import cmd_webui

    cfg = VaultConfig(webui_mode="mcp-only")
    monkeypatch.setattr("monogram.vault_config.load_vault_config",
                         lambda: cfg)
    monkeypatch.setattr("monogram.bot_webui_cmds.load_vault_config",
                         lambda: cfg)
    msg = _make_msg("/webui")
    asyncio.run(cmd_webui(msg))
    reply = msg.answer.call_args[0][0]
    assert "mcp-only" in reply.lower()


def test_webui_command_unauthorized_noop():
    from monogram.bot_webui_cmds import cmd_webui
    msg = _make_msg("/webui", user_id=99999999)
    asyncio.run(cmd_webui(msg))
    msg.answer.assert_not_called()


def test_show_webui_mentions_password_state():
    from monogram.bot_webui_cmds import show_webui
    msg = _make_msg("/config_webui")
    asyncio.run(show_webui(msg))
    reply = msg.answer.call_args[0][0]
    assert "Password" in reply
