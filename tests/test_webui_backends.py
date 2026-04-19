"""v0.6 tests — WebUIBackend dispatcher + gcs/self-host/mcp-only backends."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from monogram.vault_config import VaultConfig, load_vault_config
from monogram.webui import (
    WebUIDisabledError,
    get_active_backend,
)
from monogram.webui.backend_gcs import GCSBackend
from monogram.webui.backend_mcp_only import MCPOnlyBackend
from monogram.webui.backend_self_host import SelfHostBackend


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr("monogram.vault_config.github_store.read", lambda p: "")
    load_vault_config.cache_clear()
    yield
    load_vault_config.cache_clear()


def _cfg(mode: str, **kwargs) -> VaultConfig:
    return VaultConfig(webui_mode=mode, **kwargs)


# ── dispatcher ──


def test_dispatcher_returns_gcs():
    with patch("monogram.vault_config.load_vault_config",
               return_value=_cfg("gcs")):
        b = get_active_backend()
        assert isinstance(b, GCSBackend)


def test_dispatcher_returns_self_host():
    with patch("monogram.vault_config.load_vault_config",
               return_value=_cfg("self-host")):
        b = get_active_backend()
        assert isinstance(b, SelfHostBackend)


def test_dispatcher_returns_mcp_only():
    with patch("monogram.vault_config.load_vault_config",
               return_value=_cfg("mcp-only")):
        b = get_active_backend()
        assert isinstance(b, MCPOnlyBackend)


def test_dispatcher_defaults_to_mcp_only_when_unset():
    with patch("monogram.vault_config.load_vault_config",
               return_value=_cfg("")):
        b = get_active_backend()
        assert isinstance(b, MCPOnlyBackend)


def test_dispatcher_rejects_unknown_mode():
    with patch("monogram.vault_config.load_vault_config",
               return_value=_cfg("nonsense")):
        with pytest.raises(ValueError):
            get_active_backend()


# ── mcp-only ──


def test_mcp_only_publish_raises():
    b = MCPOnlyBackend()
    with pytest.raises(WebUIDisabledError):
        asyncio.run(b.publish(b"<html>"))


def test_mcp_only_current_url_none():
    b = MCPOnlyBackend()
    assert asyncio.run(b.current_url()) is None


# ── gcs ──


def test_gcs_raises_without_bucket():
    cfg = VaultConfig(webui_mode="gcs", webui_gcs={"bucket": "", "path_slug": "main"})
    with patch("monogram.vault_config.load_vault_config", return_value=cfg):
        b = GCSBackend()
        with pytest.raises(RuntimeError, match="webui_gcs.bucket"):
            asyncio.run(b.publish(b"<html>"))


def test_gcs_current_url_format():
    cfg = VaultConfig(
        webui_mode="gcs",
        webui_gcs={"bucket": "my-bucket", "path_slug": "main"},
    )
    with patch("monogram.vault_config.load_vault_config", return_value=cfg):
        b = GCSBackend()
        url = asyncio.run(b.current_url())
        assert url == "https://storage.googleapis.com/my-bucket/main/index.html"


@patch.dict("os.environ", {"GOOGLE_APPLICATION_CREDENTIALS": "/nonexistent/path.json"})
def test_gcs_raises_without_valid_creds_file():
    cfg = VaultConfig(
        webui_mode="gcs",
        webui_gcs={"bucket": "my-bucket", "path_slug": "main"},
    )
    with patch("monogram.vault_config.load_vault_config", return_value=cfg):
        b = GCSBackend()
        with pytest.raises(RuntimeError, match="GOOGLE_APPLICATION_CREDENTIALS"):
            asyncio.run(b.publish(b"<html>"))


# ── self-host url regex ──


def test_self_host_tunnel_url_regex_matches():
    from monogram.webui.backend_self_host import _TUNNEL_URL_RE
    line = "2026-04-19T10:00:00Z INF |  https://bright-panda-42.trycloudflare.com                     |"
    m = _TUNNEL_URL_RE.search(line)
    assert m is not None
    assert m.group(0) == "https://bright-panda-42.trycloudflare.com"


def test_self_host_tunnel_url_regex_rejects_non_cloudflared():
    from monogram.webui.backend_self_host import _TUNNEL_URL_RE
    assert _TUNNEL_URL_RE.search("https://example.com") is None
