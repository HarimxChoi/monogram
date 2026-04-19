"""safe_read tests — mocked github_store + vault_config."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from monogram.safe_read import is_blocked, safe_read
from monogram.vault_config import VaultConfig, load_vault_config


@pytest.fixture(autouse=True)
def _clear_vault_cache():
    load_vault_config.cache_clear()
    yield
    load_vault_config.cache_clear()


@patch("monogram.vault_config.github_store")
def test_is_blocked_credentials_hardcoded(mock_vault_store):
    mock_vault_store.read.return_value = ""  # defaults
    assert is_blocked("life/credentials/anything.md") is True
    assert is_blocked("life/credentials/") is True


@patch("monogram.vault_config.github_store")
def test_is_blocked_allows_normal_paths(mock_vault_store):
    mock_vault_store.read.return_value = ""
    assert is_blocked("wiki/rtmpose.md") is False
    assert is_blocked("projects/paper-a.md") is False
    assert is_blocked("life/shopping.md") is False


@patch("monogram.vault_config.github_store")
def test_is_blocked_empty_path_is_false(mock_vault_store):
    mock_vault_store.read.return_value = ""
    assert is_blocked("") is False


@patch("monogram.safe_read.github_store")
@patch("monogram.vault_config.github_store")
def test_safe_read_blocks_credential_path(mock_vault_store, mock_store):
    mock_vault_store.read.return_value = ""
    mock_store.read.return_value = "SECRET_VALUE"
    assert safe_read("life/credentials/openai-key.md") == ""
    # The underlying github_store.read was NOT called for the blocked path
    mock_store.read.assert_not_called()


@patch("monogram.safe_read.github_store")
@patch("monogram.vault_config.github_store")
def test_safe_read_returns_content_for_allowed_path(mock_vault_store, mock_store):
    mock_vault_store.read.return_value = ""
    mock_store.read.return_value = "wiki content"
    assert safe_read("wiki/rtmpose.md") == "wiki content"


@patch("monogram.vault_config.github_store")
def test_user_added_never_read_paths_are_honored(mock_vault_store):
    mock_vault_store.read.return_value = "---\nnever_read_paths: [private/]\n---"
    mock_vault_store.parse_metadata.return_value = (
        {"never_read_paths": ["private/"]},
        "",
    )
    assert is_blocked("private/diary.md") is True
    # Hardcoded still applies
    assert is_blocked("life/credentials/x.md") is True
