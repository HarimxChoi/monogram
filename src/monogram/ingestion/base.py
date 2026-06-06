"""ExtractionResult dataclass and shared helpers for ingestion extractors."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class ExtractionResult:

    source_type: str                      # "youtube" | "arxiv" | "pdf" | "web" | "image"
    url: str                              # the original URL the drop referenced
    text: str                             # enrichment text appended to drop
    metadata: dict[str, Any] = field(default_factory=dict)
    extraction_method: str = "unknown"    # "transcript" | "whisper_fallback" | etc
    success: bool = True                  # False if extraction degraded to metadata-only
    warning: str | None = None            # user-visible warning if success is partial

    def raw_path(self) -> str:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        slug = _derive_slug(self.url)
        return f"raw/{today}-{self.source_type}-{slug}.md"

    def to_pipeline_snippet(self, max_chars: int = 2000) -> str:
        snippet = self.text.strip()
        if len(snippet) <= max_chars:
            body = snippet
        else:
            body = snippet[:max_chars].rstrip() + "…\n[truncated — full text in raw/]"

        header = f"\n\n[Extracted from {self.url} ({self.source_type})]\n"
        return header + body

    def to_raw_markdown(self) -> str:
        lines = [
            f"# {self.metadata.get('title') or self.url}",
            "",
            f"- Source: {self.source_type}",
            f"- URL: {self.url}",
            f"- Extracted: {datetime.now(timezone.utc).isoformat()}",
            f"- Method: {self.extraction_method}",
        ]
        if self.warning:
            lines.append(f"- Warning: {self.warning}")
        lines.append("")

        for key in ("authors", "channel", "duration", "upload_date",
                    "published", "citations", "categories"):
            if key in self.metadata:
                val = self.metadata[key]
                if isinstance(val, list):
                    val = ", ".join(str(v) for v in val)
                lines.append(f"- {key}: {val}")

        lines.extend(["", "---", "", self.text])
        return "\n".join(lines)


def _derive_slug(url: str) -> str:
    tail = url.rstrip("/").split("/")[-1]
    tail = re.sub(r"[?#].*$", "", tail)
    tail = re.sub(r"[^a-zA-Z0-9._-]+", "-", tail).strip("-")
    if 3 <= len(tail) <= 50:
        return tail.lower()
    return hashlib.sha256(url.encode()).hexdigest()[:8]


_URL_RE = re.compile(
    r"https?://[^\s<>()\[\]{}\"']+[^\s<>()\[\]{}\"'.,;:!?]",
    re.IGNORECASE,
)


def extract_urls(text: str, max_urls: int = 3) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for match in _URL_RE.finditer(text):
        url = match.group()
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
        if len(out) >= max_urls:
            break
    return out


def is_youtube(url: str) -> bool:
    return "youtube.com/watch" in url or "youtu.be/" in url or "youtube.com/shorts/" in url


def is_arxiv(url: str) -> bool:
    return "arxiv.org/abs/" in url or "arxiv.org/pdf/" in url


def is_pdf_url(url: str) -> bool:
    return url.lower().endswith(".pdf")


def is_hwp(url: str) -> bool:
    return url.lower().endswith((".hwp", ".hwpx"))


# SSRF protection — blocks private IPs, cloud-metadata hosts, non-HTTP(S) schemes, and encoded bypasses.
# Does NOT protect against DNS rebinding (out of scope for a personal tool).

import ipaddress as _ipaddress
import socket as _socket
from urllib.parse import urlparse as _urlparse

_METADATA_HOSTS = frozenset({
    "metadata.google.internal",
    "metadata",
    "instance-data",  # AWS cloud-metadata convenience DNS alias
})

# stdlib is_private misses CGNAT and IPv4-mapped IPv6; block explicitly.
_EXTRA_BLOCKED_NETS = tuple(
    _ipaddress.ip_network(cidr) for cidr in (
        "100.64.0.0/10",   # RFC 6598 CGNAT — Alibaba Cloud metadata at 100.100.100.200
        "::ffff:0:0/96",   # IPv4-mapped IPv6 — checked after ip_address normalization
    )
)


class UnsafeURLError(ValueError):
    """URL targets internal/cloud-metadata/dangerous infra (SSRF)."""


def is_safe_url(url: str) -> tuple[bool, str]:
    """Return (ok, reason); pre-request SSRF validation only, no DNS rebinding protection."""
    try:
        parsed = _urlparse(url)
    except Exception as e:
        return False, f"parse_error: {e}"

    if parsed.scheme not in ("http", "https"):
        return False, f"scheme_not_allowed: {parsed.scheme!r}"

    if not parsed.hostname:
        return False, "no_hostname"

    hostname = parsed.hostname.lower()

    if hostname in _METADATA_HOSTS:
        return False, f"metadata_host: {hostname}"

    try:
        infos = _socket.getaddrinfo(hostname, None)
        ips = {info[4][0] for info in infos}
    except _socket.gaierror as e:
        return False, f"dns_error: {e}"
    except Exception as e:
        return False, f"resolution_error: {e}"

    for ip_str in ips:
        try:
            ip = _ipaddress.ip_address(ip_str)
        except ValueError:
            return False, f"invalid_ip: {ip_str}"

        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_multicast or ip.is_reserved or ip.is_unspecified):
            return False, f"private_ip: {ip_str}"

        for net in _EXTRA_BLOCKED_NETS:
            if ip in net:
                return False, f"blocked_net {net}: {ip_str}"

    return True, "ok"


def require_safe_url(url: str) -> None:
    ok, reason = is_safe_url(url)
    if not ok:
        raise UnsafeURLError(f"unsafe URL ({reason}): {url}")


def safe_stream_bytes(
    url: str,
    max_bytes: int,
    timeout: float = 15.0,
    max_redirects: int = 5,
) -> bytes | None:
    """SSRF-safe download: validates every redirect hop; does not trust Content-Length."""
    try:
        import httpx
    except ImportError:
        return None

    current = url
    for _ in range(max_redirects + 1):
        try:
            require_safe_url(current)
        except UnsafeURLError:
            return None

        try:
            with httpx.stream(
                "GET", current, timeout=timeout, follow_redirects=False
            ) as resp:
                if 300 <= resp.status_code < 400:
                    loc = resp.headers.get("location", "")
                    if not loc:
                        return None
                    from urllib.parse import urljoin
                    current = urljoin(current, loc)
                    continue

                if resp.status_code != 200:
                    return None

                chunks: list[bytes] = []
                total = 0
                for chunk in resp.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        break
                    chunks.append(chunk)
                return b"".join(chunks)[:max_bytes]
        except Exception:
            return None

    return None  # redirect cap exceeded

