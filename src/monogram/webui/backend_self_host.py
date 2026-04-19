"""Self-host backend — aiohttp + cloudflared quick tunnel.

Serves the encrypted shell over localhost, wraps with a cloudflared
tunnel so the URL is reachable from anywhere. Quick tunnels rotate
per restart (trycloudflare.com subdomain); for a stable URL use gcs.

cloudflared binary is auto-downloaded to ~/.local/bin/cloudflared on
first publish if missing.
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import re
import stat
import sys
import urllib.request
from pathlib import Path

from . import WebUIBackend

log = logging.getLogger("monogram.webui.self_host")


_CLOUDFLARED_URLS = {
    ("linux", "x86_64"): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64",
    ("linux", "aarch64"): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64",
    ("darwin", "x86_64"): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-amd64.tgz",
    ("darwin", "arm64"): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-arm64.tgz",
    ("windows", "amd64"): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe",
}

_TUNNEL_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def _binary_path() -> Path:
    """Cross-platform cloudflared binary path."""
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".local" / "bin"
        return base / "cloudflared.exe"
    return Path.home() / ".local" / "bin" / "cloudflared"


def _platform_key() -> tuple[str, str]:
    sysname = platform.system().lower()  # linux, darwin, windows
    mach = platform.machine().lower()
    # Normalize
    if mach in ("x86_64", "amd64"):
        mach = "x86_64" if sysname != "windows" else "amd64"
    if mach in ("aarch64", "arm64"):
        mach = "arm64" if sysname == "darwin" else "aarch64"
    return sysname, mach


def _download_cloudflared() -> Path:
    sysname, mach = _platform_key()
    url = _CLOUDFLARED_URLS.get((sysname, mach))
    if not url:
        raise RuntimeError(
            f"No cloudflared binary for {sysname}/{mach}. "
            f"Download manually from https://github.com/cloudflare/cloudflared/releases"
        )
    binary = _binary_path()
    binary.parent.mkdir(parents=True, exist_ok=True)
    log.info("cloudflared: downloading %s → %s", url, binary)
    urllib.request.urlretrieve(url, str(binary))
    if not sys.platform.startswith("win"):
        st = os.stat(binary)
        os.chmod(binary, st.st_mode | stat.S_IXUSR | stat.S_IRUSR | stat.S_IWUSR)
    return binary


def _ensure_cloudflared() -> Path:
    binary = _binary_path()
    if binary.exists():
        return binary
    return _download_cloudflared()


class SelfHostBackend(WebUIBackend):
    def __init__(self) -> None:
        self._runner = None  # aiohttp AppRunner
        self._site = None
        self._tunnel_proc: asyncio.subprocess.Process | None = None
        self._tunnel_url: str | None = None
        self._current_html: bytes = b""

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
        binary = _ensure_cloudflared()
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

    async def publish(self, encrypted_html: bytes) -> str:
        self._current_html = encrypted_html
        await self._ensure_server()
        await self._ensure_tunnel()
        assert self._tunnel_url is not None
        return self._tunnel_url

    async def current_url(self) -> str | None:
        return self._tunnel_url

    async def teardown(self) -> None:
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
