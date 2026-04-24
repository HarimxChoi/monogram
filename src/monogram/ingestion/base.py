"""ExtractionResult dataclass and shared helpers for ingestion extractors.

Every extractor in this package returns an ExtractionResult, giving the
listener a uniform interface regardless of source type. The result has:

- text: what gets appended to the drop before the pipeline runs
- metadata: source-specific facts (title, authors, duration, etc.)
- extraction_method: string indicating which code path produced the text
  (useful for evals and debugging)
- raw_markdown(): rendering for the raw/ tier — always the fullest
  possible version, not the pipeline-enriching snippet
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class ExtractionResult:
    """Uniform return type from every extractor."""

    source_type: str                      # "youtube" | "arxiv" | "pdf" | "web" | "image"
    url: str                              # the original URL the drop referenced
    text: str                             # enrichment text appended to drop
    metadata: dict[str, Any] = field(default_factory=dict)
    extraction_method: str = "unknown"    # "transcript" | "whisper_fallback" | etc
    success: bool = True                  # False if extraction degraded to metadata-only
    warning: str | None = None            # user-visible warning if success is partial
    # Optional full-fidelity body for the raw/ tier when `text` was
    # condensed for the pipeline (see ingestion/text.py::condense_for_pipeline).
    # When None, `to_raw_markdown()` falls back to `text`.
    raw_text: str | None = None

    def raw_path(self) -> str:
        """Slug for the raw/ tier file.

        Format: YYYY-MM-DD-<source>-<slug>.md
        Slug is the first 40 chars of the URL's last path component,
        or a hash if that's not useful.
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        slug = _derive_slug(self.url)
        return f"raw/{today}-{self.source_type}-{slug}.md"

    def to_pipeline_snippet(self, max_chars: int = 2000) -> str:
        """What gets appended to the drop before the pipeline runs.

        Capped to max_chars to avoid exploding pipeline token usage. The
        FULL text is still preserved in the raw/ tier.
        """
        snippet = self.text.strip()
        if len(snippet) <= max_chars:
            body = snippet
        else:
            body = snippet[:max_chars].rstrip() + "…\n[truncated — full text in raw/]"

        header = f"\n\n[Extracted from {self.url} ({self.source_type})]\n"
        return header + body

    def to_raw_markdown(self) -> str:
        """Full-fidelity markdown for the raw/ tier.

        Never truncated. Metadata first, then the raw text.
        """
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

        # Render known metadata keys in a stable order
        for key in ("authors", "channel", "duration", "upload_date",
                    "published", "citations", "categories"):
            if key in self.metadata:
                val = self.metadata[key]
                if isinstance(val, list):
                    val = ", ".join(str(v) for v in val)
                lines.append(f"- {key}: {val}")

        body = self.raw_text if self.raw_text is not None else self.text
        lines.extend(["", "---", "", body])
        return "\n".join(lines)


def _derive_slug(url: str) -> str:
    """Produce a filesystem-safe slug from a URL."""
    # Try last path component
    tail = url.rstrip("/").split("/")[-1]
    tail = re.sub(r"[?#].*$", "", tail)
    tail = re.sub(r"[^a-zA-Z0-9._-]+", "-", tail).strip("-")
    if 3 <= len(tail) <= 50:
        return tail.lower()
    # Fallback: 8-char hash
    return hashlib.sha256(url.encode()).hexdigest()[:8]


# Shared URL pattern recognition ------------------------------------------

_URL_RE = re.compile(
    r"https?://[^\s<>()\[\]{}\"']+[^\s<>()\[\]{}\"'.,;:!?]",
    re.IGNORECASE,
)


def extract_urls(text: str, max_urls: int = 3) -> list[str]:
    """Return URLs found in text, deduplicated, order-preserved.

    Capped at max_urls (from vault_config.ingestion_max_urls_per_drop,
    default 3). Guards against URL-spam drops overwhelming the extractor.
    """
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


# ---------------------------------------------------------------------------
# SSRF protection
# ---------------------------------------------------------------------------
#
# Monogram fetches URLs from the user's own Telegram drops. Risk is low but
# defense is cheap. We block the classical SSRF attack surfaces:
#
#   - Non-HTTP(S) schemes: file://, gopher://, dict://, ftp://
#   - Private IP ranges: 10/8, 172.16/12, 192.168/16, 127/8, 169.254/16,
#     ::1, ::ffff:0:0/96 (IPv4-mapped IPv6), fc00::/7, fe80::/10
#   - Cloud metadata endpoints: 169.254.169.254 (AWS/Azure),
#     metadata.google.internal (GCP), fd00:ec2::254 (AWS IPv6)
#   - Encoded bypasses: http://127.1/, http://0x7f000001/,
#     http://2130706433/ — ipaddress.ip_address normalizes these
#
# We DON'T defend against DNS rebinding (requires pinning the resolved IP
# through the entire request lifetime). That's out of scope for a personal
# tool; the defense would cost more than it protects against here.

import ipaddress as _ipaddress
import socket as _socket
from urllib.parse import urlparse as _urlparse

_METADATA_HOSTS = frozenset({
    "metadata.google.internal",
    "metadata",
    "instance-data",  # AWS convenience DNS
})

# Networks Python's is_private does not cover. Explicit blocklist.
_EXTRA_BLOCKED_NETS = tuple(
    _ipaddress.ip_network(cidr) for cidr in (
        "100.64.0.0/10",   # RFC 6598 carrier-grade NAT
                           # Alibaba Cloud metadata lives at 100.100.100.200
        "::ffff:0:0/96",   # IPv4-mapped IPv6 — ip_address strips the prefix
                           # but we re-check after normalization anyway
    )
)


class UnsafeURLError(ValueError):
    """Raised when a URL targets internal/unreachable/dangerous infra."""


def is_safe_url(url: str) -> tuple[bool, str]:
    """Return (ok, reason). ok=True means URL is safe to fetch.

    Does NOT do DNS rebinding protection — just pre-request validation.
    Callers still need request timeouts and response-size limits.
    """
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

        # Stdlib covers: private, loopback, link_local, multicast,
        # reserved, unspecified. That's 127/8, 10/8, 172.16/12,
        # 192.168/16, 169.254/16, ::1, fc00::/7, fe80::/10.
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_multicast or ip.is_reserved or ip.is_unspecified):
            return False, f"private_ip: {ip_str}"

        # CGNAT is not is_private. Alibaba Cloud metadata is at
        # 100.100.100.200 — inside this range.
        for net in _EXTRA_BLOCKED_NETS:
            if ip in net:
                return False, f"blocked_net {net}: {ip_str}"

    return True, "ok"


def require_safe_url(url: str) -> None:
    """Raise UnsafeURLError if the URL fails is_safe_url."""
    ok, reason = is_safe_url(url)
    if not ok:
        raise UnsafeURLError(f"unsafe URL ({reason}): {url}")


# ---------------------------------------------------------------------------
# Safe streaming download with manual redirect-chain validation
# ---------------------------------------------------------------------------
#
# httpx/requests with follow_redirects=True will happily chase a 302 to
# http://127.0.0.1/admin after an initial safe URL passed require_safe_url.
# We validate every hop instead of trusting the library.


def safe_stream_bytes(
    url: str,
    max_bytes: int,
    timeout: float = 15.0,
    max_redirects: int = 5,
) -> bytes | None:
    """Stream-download a URL with SSRF-safe redirect handling.

    - Validates every URL (including each redirect target) with require_safe_url.
    - Streams chunks and stops at max_bytes (does not trust Content-Length).
    - Returns bytes on success, None on any failure.

    Used by PDF and office extractors. Replaces the common pattern of
    httpx.stream(..., follow_redirects=True) which bypasses SSRF checks.
    """
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
                    # Resolve relative redirects against current
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
                        # Don't return truncated bytes — downstream parsers
                        # crash on partial PDFs/docx and the caller has no
                        # way to distinguish "small file" from "cut off".
                        return None
                    chunks.append(chunk)
                return b"".join(chunks)
        except Exception:
            return None

    return None  # redirect cap exceeded

