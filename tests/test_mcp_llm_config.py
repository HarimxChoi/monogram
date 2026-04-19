"""A11 + v0.5.1 tests — MCP tools get_llm_config + set_llm_config with
GitHub-backed pending queue."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monogram.mcp_pending import peek_pending, pop_pending
from monogram.mcp_server import _get_llm_config, _set_llm_config
from monogram.vault_config import VaultConfig, load_vault_config


class _FakeRepo:
    def __init__(self):
        self.files: dict[str, str] = {}

    def get_contents(self, path):
        if path not in self.files:
            from github import UnknownObjectException
            raise UnknownObjectException(404, {"message": "Not Found"}, None)
        e = MagicMock()
        e.decoded_content = self.files[path].encode()
        e.sha = "sha_" + path
        e.path = path
        return e

    def create_file(self, path, msg, content): self.files[path] = content
    def update_file(self, path, msg, content, sha): self.files[path] = content
    def delete_file(self, path, msg, sha): self.files.pop(path, None)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    """Single github_store patch that serves both mcp_pending and vault_config."""
    repo = _FakeRepo()

    def _read(p):
        # vault_config reads config.md → empty; anything else → in-memory fake
        if p == "config.md":
            return ""
        return repo.files.get(p, "")

    def _write(p, c, m):
        repo.files[p] = c
        return True

    monkeypatch.setattr("monogram.github_store.read", _read)
    monkeypatch.setattr("monogram.github_store.write", _write)
    monkeypatch.setattr("monogram.github_store._repo", lambda: repo)
    load_vault_config.cache_clear()
    yield
    load_vault_config.cache_clear()


def test_get_llm_config_returns_json():
    vc = VaultConfig(
        llm_provider="anthropic",
        llm_mode="tiered",
        llm_models={"low": "anthropic/a"},
        llm_base_url="",
    )
    with patch("monogram.vault_config.load_vault_config", return_value=vc):
        result = asyncio.run(_get_llm_config())
        data = json.loads(result)
        assert data["provider"] == "anthropic"
        assert data["mode"] == "tiered"
        assert data["models"]["low"] == "anthropic/a"


@patch("monogram.bot_notify.push_to_telegram", new_callable=AsyncMock)
def test_set_llm_config_enqueues_pending(mock_push):
    mock_push.return_value = True
    result = asyncio.run(_set_llm_config(provider="openai"))
    assert "Token:" in result
    token = result.rsplit(":", 1)[1].strip()
    entry = peek_pending(token)
    assert entry is not None
    assert entry.kind == "set_llm_config"
    assert entry.payload == {"llm_provider": "openai"}
    mock_push.assert_awaited_once()


@patch("monogram.bot_notify.push_to_telegram", new_callable=AsyncMock)
def test_set_llm_config_no_args_returns_noop(mock_push):
    mock_push.return_value = True
    result = asyncio.run(_set_llm_config())
    assert "nothing to change" in result.lower()
    mock_push.assert_not_awaited()


@patch("monogram.bot_notify.push_to_telegram", new_callable=AsyncMock)
def test_set_llm_config_multiple_fields(mock_push):
    mock_push.return_value = True
    result = asyncio.run(_set_llm_config(
        provider="ollama",
        mode="single",
        models={"single": "ollama/qwen2.5:7b"},
        base_url="http://localhost:11434",
    ))
    token = result.rsplit(":", 1)[1].strip()
    entry = pop_pending(token)
    assert entry is not None
    payload = entry.payload
    assert payload["llm_provider"] == "ollama"
    assert payload["llm_mode"] == "single"
    assert payload["llm_models"]["single"] == "ollama/qwen2.5:7b"
    assert payload["llm_base_url"] == "http://localhost:11434"
