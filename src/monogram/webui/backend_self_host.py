"""Self-host backend — aiohttp + cloudflared quick tunnel.

Serves the encrypted shell over localhost, wraps with a cloudflared
tunnel so the URL is reachable from anywhere. Quick tunnels rotate
per restart (trycloudflare.com subdomain); for a stable URL use gcs.

cloudflared is **not auto-downloaded**. Earlier versions pulled a
binary from GitHub releases without checksum verification — a
supply-chain risk we chose not to keep. Users install it themselves
via a package manager (Homebrew, apt, winget, the official installer)
and we error with a clear message if it's missing from PATH.
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import re
import shutil
import sys
from pathlib import Path

from . import WebUIBackend

log = logging.getLogger("monogram.webui.self_host")


# Download URLs kept only so the error message can point the user at the
# right artifact when cloudflared is missing. We never fetch them.
_CLOUDFLARED_URLS = {
    ("linux", "x86_64"): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64",
    ("linux", "aarch64"): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64",
    ("darwin", "x86_64"): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-amd64.tgz",
    ("darwin", "arm64"): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-arm64.tgz",
    ("windows", "amd64"): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe",
}

_TUNNEL_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def _platform_key() -> tuple[str, str]:
    sysname = platform.system().lower()  # linux, darwin, windows
    mach = platform.machine().lower()
    if mach in ("x86_64", "amd64"):
        mach = "x86_64" if sysname != "windows" else "amd64"
    if mach in ("aarch64", "arm64"):
        mach = "arm64" if sysname == "darwin" else "aarch64"
    return sysname, mach


def _locate_cloudflared() -> Path:
    """Find cloudflared on PATH. Raise with an install hint if missing."""
    found = shutil.which("cloudflared") or shutil.which("cloudflared.exe")
    if found:
        return Path(found)

    sysname, mach = _platform_key()
    url = _CLOUDFLARED_URLS.get((sysname, mach), "(platform not listed)")
    hints = (
        "brew install cloudflare/cloudflare/cloudflared"      # macOS
        if sysname == "darwin"
        else "apt-get install cloudflared"                     # linux hint
        if sysname == "linux"
        else "winget install cloudflare.cloudflared"           # windows
    )
    raise RuntimeError(
        "cloudflared not found on PATH. Install it first, then re-run.\n"
        f"  Suggested: {hints}\n"
        f"  Direct download ({sysname}/{mach}): {url}\n"
        "  (Auto-download was removed for supply-chain safety.)"
    )


class SelfHostBackend(WebUIBackend):
    def __init__(self) -> None:
        self._runner = None  # aiohttp AppRunner
        self._site = None
        self._tunnel_proc: asyncio.subprocess.Process | None = None
        self._tunnel_url: str | None = None
        self._current_html: bytes = b""
        # Keeps cloudflared's stdout pipe drained after URL capture so a
        # chatty tunnel doesn't fill its OS buffer and block. Cancelled in
        # teardown.
        self._stdout_drain_task: asyncio.Task | None = None

    def _port(self) -> int:
        from ..vault_config import load_vault_config
        vcfg = load_vault_config()
        return int((vcfg.webui_self_host or {}).get("port", 8765))

    async def _ensure_server(self) -> None:
        if self._runner is not None:
            return
        from aiohttp import web

        async def index(request):
            return web.Response(
                body=self._current_html,
                content_type="text/html",
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )

        async def refresh(request):
            # Regenerate: re-encrypt from latest webgen + current password.
            try:
                from ..webgen import render_bundle
                from ..encryption_layer import wrap
                from ..config import load_config
                plaintext = await render_bundle()
                password = load_config().monogram_webui_password
                if not password:
                    return web.json_response(
                        {"error": "MONOGRAM_WEBUI_PASSWORD not set"}, status=500
                    )
                self._current_html = wrap(plaintext, password)
                return web.json_response({"ok": True})
            except Exception as e:
                return web.json_response({"error": str(e)}, status=500)

        app = web.Application()
        app.router.add_get("/", index)
        app.router.add_post("/api/refresh", refresh)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "127.0.0.1", self._port())
        await self._site.start()
        log.info("self_host: aiohttp listening on 127.0.0.1:%s", self._port())

    async def _ensure_tunnel(self) -> None:
        if self._tunnel_proc is not None and self._tunnel_proc.returncode is None:
            return
        binary = _locate_cloudflared()
        port = self._port()
        self._tunnel_proc = await asyncio.create_subprocess_exec(
            str(binary),
            "tunnel", "--url", f"http://localhost:{port}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        # Read stdout lines until we find the tunnel URL (or give up after 30s)
        async def _read_url() -> str | None:
            assert self._tunnel_proc and self._tunnel_proc.stdout
            while True:
                line = await self._tunnel_proc.stdout.readline()
                if not line:
                    return None
                text = line.decode("utf-8", errors="replace")
                log.debug("cloudflared: %s", text.rstrip())
                m = _TUNNEL_URL_RE.search(text)
                if m:
                    return m.group(0)

        try:
            url = await asyncio.wait_for(_read_url(), timeout=30.0)
        except asyncio.TimeoutError:
            url = None
        if not url:
            raise RuntimeError(
                "cloudflared did not emit a tunnel URL within 30s. "
                "Check network / firewall."
            )
        self._tunnel_url = url
        log.info("self_host: tunnel URL %s", url)

        # Keep consuming stdout in the background — otherwise cloudflared's
        # pipe buffer fills on a chatty tunnel and the process blocks on
        # write, which manifests as a silent tunnel hang.
        async def _drain_stdout() -> None:
            try:
                assert self._tunnel_proc and self._tunnel_proc.stdout
                while True:
                    line = await self._tunnel_proc.stdout.readline()
                    if not line:
                        return
                    log.debug("cloudflared: %s", line.decode("utf-8", "replace").rstrip())
            except asyncio.CancelledError:
                raise
            except Exception as e:  # pragma: no cover — defensive
                log.debug("cloudflared stdout drain stopped: %s", e)

        self._stdout_drain_task = asyncio.create_task(_drain_stdout())

    async def publish(self, encrypted_html: bytes) -> str:
        self._current_html = encrypted_html
        await self._ensure_server()
        await self._ensure_tunnel()
        assert self._tunnel_url is not None
        return self._tunnel_url

    async def current_url(self) -> str | None:
        return self._tunnel_url

    async def teardown(self) -> None:
        if self._stdout_drain_task is not None:
            self._stdout_drain_task.cancel()
            try:
                await self._stdout_drain_task
            except (asyncio.CancelledError, Exception):
                pass
            self._stdout_drain_task = None

        if self._tunnel_proc and self._tunnel_proc.returncode is None:
            self._tunnel_proc.terminate()
            try:
                await asyncio.wait_for(self._tunnel_proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._tunnel_proc.kill()
        self._tunnel_proc = None
        self._tunnel_url = None

        if self._site is not None:
            await self._site.stop()
            self._site = None
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
