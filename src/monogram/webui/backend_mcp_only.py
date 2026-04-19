"""Stub backend: no web UI published. Use MCP client instead."""
from __future__ import annotations

from . import WebUIBackend, WebUIDisabledError


class MCPOnlyBackend(WebUIBackend):
    async def publish(self, encrypted_html: bytes) -> str:
        raise WebUIDisabledError(
            "Web UI disabled (webui_mode=mcp-only). "
            "Use /config_webui_mode gcs or /config_webui_mode self-host."
        )

    async def current_url(self) -> str | None:
        return None

    async def teardown(self) -> None:
        return None
