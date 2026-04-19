"""Social-media extractor — Instagram + TikTok via yt-dlp.

Scope for v0.8: public content only. Captions + metadata always; for
videos, transcript via Whisper fallback if the user opted in. Private
content (stories, private accounts) requires session cookies — not
supported in v0.8 (private-content handling is a future opt-in that
needs a careful credential-storage design).

Instagram and TikTok both go through yt-dlp. Same code path, different
URL detection. We return a unified ExtractionResult so the pipeline
doesn't need to care which platform.

Threat surface:
  - yt-dlp has had code-execution CVEs historically (around the metadata
    extractor for specific hostile sites). Stable channel with monthly
    updates is the mitigation; Dependabot pins loosely so we catch
    patches without unattended major bumps.
  - Instagram rate limits aggressively. We don't download media; just
    metadata + caption. Reduces rate-limit exposure and disk I/O.
  - TikTok may require PO tokens for some videos (like YouTube). Graceful
    degradation to metadata-only.
"""
from __future__ import annotations

import asyncio
import logging
import re

from .base import ExtractionResult

log = logging.getLogger("monogram.ingestion.social")


_INSTAGRAM_HOSTS = ("instagram.com", "www.instagram.com", "instagr.am")
_TIKTOK_HOSTS = ("tiktok.com", "www.tiktok.com", "vm.tiktok.com", "vt.tiktok.com")


def is_instagram(url: str) -> bool:
    return any(h in url.lower() for h in _INSTAGRAM_HOSTS)


def is_tiktok(url: str) -> bool:
    return any(h in url.lower() for h in _TIKTOK_HOSTS)


def platform_for(url: str) -> str | None:
    if is_instagram(url):
        return "instagram"
    if is_tiktok(url):
        return "tiktok"
    return None


async def extract(url: str) -> ExtractionResult:
    """Extract caption + metadata from an Instagram or TikTok URL."""
    platform = platform_for(url)
    if not platform:
        return ExtractionResult(
            source_type="social",
            url=url,
            text=f"[Not an Instagram/TikTok URL: {url}]",
            success=False,
            extraction_method="not_social",
        )

    # Validate before yt-dlp sees it. yt-dlp will happily follow any
    # redirect and hit loopback / cloud-metadata endpoints.
    from .base import require_safe_url, UnsafeURLError
    try:
        require_safe_url(url)
    except UnsafeURLError as e:
        return ExtractionResult(
            source_type=platform,
            url=url,
            text=f"[Social fetch blocked: {e}]",
            success=False,
            extraction_method="blocked",
            warning=str(e),
        )

    info = await _ytdlp_info(url)
    if not info:
        return ExtractionResult(
            source_type=platform,
            url=url,
            text=f"[Extraction failed for {url}]",
            success=False,
            extraction_method="ytdlp_failed",
            warning="yt_dlp_error",
        )

    if "error" in info:
        return ExtractionResult(
            source_type=platform,
            url=url,
            text=f"[yt-dlp error: {info['error'][:100]}]",
            success=False,
            extraction_method="ytdlp_error",
            warning=info["error"][:200],
        )

    caption = info.get("description") or info.get("title") or ""
    metadata = {
        "title": info.get("title"),
        "author": info.get("uploader") or info.get("channel"),
        "duration": info.get("duration"),
        "upload_date": info.get("upload_date"),
        "like_count": info.get("like_count"),
        "view_count": info.get("view_count"),
        "platform": platform,
    }

    # Hashtag extraction from caption — consistent across both platforms
    hashtags = re.findall(r"#(\w+)", caption)
    if hashtags:
        metadata["hashtags"] = hashtags[:30]

    # Body text: caption + hashtag summary. Whisper fallback deferred to
    # v0.8.1 (same rationale as youtube.py — opt-in, heavy).
    body_parts = []
    if metadata.get("title"):
        body_parts.append(str(metadata["title"]))
    if caption:
        body_parts.append(caption)
    if hashtags:
        body_parts.append("Tags: " + " ".join(f"#{t}" for t in hashtags[:10]))

    text = "\n\n".join(body_parts) if body_parts else f"[No caption for {url}]"

    return ExtractionResult(
        source_type=platform,
        url=url,
        text=text,
        metadata=metadata,
        extraction_method="ytdlp_metadata",
        success=bool(body_parts),
        warning=None if body_parts else "no_caption_or_title",
    )


async def _ytdlp_info(url: str) -> dict | None:
    """Fetch metadata via yt-dlp with skip_download. Wrapped in a thread."""
    def _sync() -> dict | None:
        try:
            import yt_dlp
        except ImportError:
            return {"error": "yt-dlp not installed (install monogram[ingestion-video])"}

        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": False,
            # Explicit — never write cookies or state files to disk
            "cookiefile": None,
            "cachedir": False,
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return dict(info) if info else None
        except Exception as e:
            log.debug("yt-dlp failed for %s: %s", url, e)
            return {"error": str(e)[:200]}

    return await asyncio.to_thread(_sync)
