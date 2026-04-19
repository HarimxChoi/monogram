"""v0.6 — Web UI backends.

Three delivery modes share a single ABC interface:
- gcs         : upload to GCP Cloud Storage (stable URL)
- self-host   : aiohttp behind cloudflared quick tunnel (rotating URL)
- mcp-only    : no web UI; caller is expected to use MCP clients

Mode is read from VaultConfig.webui_mode, runtime-switchable via
/config_webui_mode.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class WebUIDisabledError(RuntimeError):
    """Raised when webui_mode=mcp-only and something attempts to publish."""


class WebUIBackend(ABC):
    @abstractmethod
    async def publish(self, encrypted_html: bytes) -> str:
        """Publish the encrypted shell. Return the URL."""

    @abstractmethod
    async def current_url(self) -> str | None:
        """Return the current URL if valid, else None."""

    @abstractmethod
    async def teardown(self) -> None:
        """Clean up resources (tunnel process, etc.)."""


def get_active_backend() -> WebUIBackend:
    """Return an instance of the backend configured in mono/config.md."""
    from ..vault_config import load_vault_config
    cfg = load_vault_config()
    mode = cfg.webui_mode or "mcp-only"
    if mode == "gcs":
        from .backend_gcs import GCSBackend
        return GCSBackend()
    if mode == "self-host":
        from .backend_self_host import SelfHostBackend
        return SelfHostBackend()
    if mode == "mcp-only":
        from .backend_mcp_only import MCPOnlyBackend
        return MCPOnlyBackend()
    raise ValueError(f"Unknown webui_mode: {mode!r}")
