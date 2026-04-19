"""v0.5.1 tests — mcp_pending GitHub-backed queue.

The v0.4 in-memory queue was cross-process broken (MCP subprocess vs bot
process). v0.5.1 stores entries in `.monogram/pending/<token>.json` via
github_store. Tests mock github_store.{read,write,_repo}.
"""
from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from monogram.mcp_pending import (
    _TTL_SECONDS,
    _looks_like_token,
    new_pending,
    peek_pending,
    pop_pending,
)


class _FakeRepo:
    """In-memory GitHub substitute used to drive pending queue tests."""

    def __init__(self):
        self.files: dict[str, str] = {}

    def get_contents(self, path: str):
        if path not in self.files:
            from github import UnknownObjectException
            raise UnknownObjectException(404, {"message": "Not Found"}, None)
        entry = MagicMock()
        entry.decoded_content = self.files[path].encode()
        entry.sha = "sha_" + path
        entry.path = path
        return entry

    def create_file(self, path, msg, content):
        self.files[path] = content

    def update_file(self, path, msg, content, sha):
        self.files[path] = content

    def delete_file(self, path, msg, sha):
        self.files.pop(path, None)


@pytest.fixture
def fake_store(monkeypatch):
    """Wire a FakeRepo into github_store. Patch the module directly
    (mcp_pending.github_store is the same module)."""
    repo = _FakeRepo()

    def fake_read(path):
        return repo.files.get(path, "")

    def fake_write(path, content, msg):
        repo.files[path] = content
        return True

    monkeypatch.setattr("monogram.github_store.read", fake_read)
    monkeypatch.setattr("monogram.github_store.write", fake_write)
    monkeypatch.setattr("monogram.github_store._repo", lambda: repo)
    return repo


def test_new_pending_writes_json_to_vault(fake_store):
    entry = new_pending("set_llm_config", {"llm_provider": "anthropic"}, "preview")
    assert _looks_like_token(entry.token)
    # 128-bit entropy → token_urlsafe(16) yields ~22 chars
    assert len(entry.token) >= 20
    assert entry.kind == "set_llm_config"
    assert entry.payload["llm_provider"] == "anthropic"
    # File was created in the vault
    expected_path = f".monogram/pending/{entry.token}.json"
    assert expected_path in fake_store.files
    data = json.loads(fake_store.files[expected_path])
    assert data["kind"] == "set_llm_config"


def test_pop_pending_consumes_and_returns(fake_store):
    entry = new_pending("set_llm_config", {"llm_mode": "single"}, "x")
    popped = pop_pending(entry.token)
    assert popped is not None
    assert popped.token == entry.token
    # Second pop returns None — entry deleted
    assert pop_pending(entry.token) is None
    # Vault file was cleaned up
    assert f".monogram/pending/{entry.token}.json" not in fake_store.files


def test_peek_does_not_remove(fake_store):
    entry = new_pending("x", {}, "p")
    peeked = peek_pending(entry.token)
    assert peeked is not None
    # Still poppable
    assert pop_pending(entry.token) is not None


def test_expired_entry_returns_none(fake_store, monkeypatch):
    entry = new_pending("x", {}, "p")
    # Tamper with the stored file to look expired
    path = f".monogram/pending/{entry.token}.json"
    data = json.loads(fake_store.files[path])
    data["expires_at"] = time.time() - 1000
    fake_store.files[path] = json.dumps(data)

    assert pop_pending(entry.token) is None
    # Expired entry was cleaned up as a side effect
    assert path not in fake_store.files


def test_unknown_token_returns_none(fake_store):
    assert pop_pending("nonexistenttokenxx") is None
    assert peek_pending("nonexistenttokenxx") is None


def test_looks_like_token_rejects_garbage():
    assert not _looks_like_token("")
    assert not _looks_like_token("xx")  # too short
    assert not _looks_like_token("abc!@#$")  # bad chars
    assert not _looks_like_token("x" * 200)  # too long
    assert _looks_like_token("abcdefgh12345678")


def test_token_length_is_at_least_128_bits(fake_store):
    """Regression check: v0.4's 8-hex (32-bit) tokens were brute-forceable."""
    entry = new_pending("x", {}, "p")
    # 128 bits ≈ 22 url-safe chars; spec is 16 bytes → ~22 chars
    assert len(entry.token) >= 20
