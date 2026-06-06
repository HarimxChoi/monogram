"""Web UI backends (gcs/self-host/mcp-only); mode from VaultConfig.webui_mode."""
from __future__ import annotations

from abc import ABC, abstractmethod


class WebUIDisabledError(RuntimeError):
    """Raised when webui_mode=mcp-only and publish is attempted."""


class WebUIBackend(ABC):
    @abstractmethod
    async def publish(self, encrypted_html: bytes) -> str:
        """Publish encrypted shell; return URL."""

    @abstractmethod
    async def current_url(self) -> str | None:
        """Return current URL if valid, else None."""

    @abstractmethod
    async def teardown(self) -> None:
        """Clean up resources."""


def get_active_backend() -> WebUIBackend:
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
