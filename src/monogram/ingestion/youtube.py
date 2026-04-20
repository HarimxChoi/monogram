"""YouTube extractor — transcript via `youtube-transcript-api`,
metadata via `yt-dlp` (no download).

IMPORTANT — youtube-transcript-api v1.0 BREAKING CHANGE:
The old `YouTubeTranscriptApi.get_transcript(video_id)` is DEPRECATED
since v1.0.0 and REMOVED in recent versions. New API:

    ytt_api = YouTubeTranscriptApi()
    fetched = ytt_api.fetch(video_id)                   # primary
    # or for language selection:
    fetched = ytt_api.list(video_id).find_transcript(["en", "ko"]).fetch()

Reference: https://pypi.org/project/youtube-transcript-api/

Whisper fallback is opt-in (`youtube_whisper_fallback: true` in config)
because:
  1. Whisper is CPU/GPU-heavy (5-30s per minute of video)
  2. It downloads audio (bandwidth cost)
  3. Transcript API covers >90% of cases

YouTube-side changes happen ~monthly (SABR playback, signature
extraction, PoTokenRequired errors). Keep yt-dlp on the stable channel
and be prepared for 1-2 day gaps between breakage and fix.
"""
from __future__ import annotations

import asyncio
import logging
import re

from .base import ExtractionResult

log = logging.getLogger("monogram.ingestion.youtube")


_VIDEO_ID_RE = re.compile(
    r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)([A-Za-z0-9_-]{11})"
)


def parse_video_id(url: str) -> str | None:
    match = _VIDEO_ID_RE.search(url)
    return match.group(1) if match else None


async def extract(url: str) -> ExtractionResult:
    """Extract a YouTube video's transcript + metadata."""
    video_id = parse_video_id(url)
    if not video_id:
        return ExtractionResult(
            source_type="youtube",
            url=url,
            text=f"[Could not parse YouTube video ID from {url}]",
            success=False,
            extraction_method="parse_failed",
            warning="invalid_video_id",
        )

    # Fetch metadata (lightweight, no transcript yet)
    metadata = await _fetch_metadata(url)

    # Fetch transcript (primary path)
    transcript_text = await _fetch_transcript(video_id)
    if transcript_text:
        return ExtractionResult(
            source_type="youtube",
            url=url,
            text=transcript_text,
            metadata=metadata,
            extraction_method="transcript",
        )

    # Transcript unavailable — check if Whisper fallback is opted-in
    if await _is_whisper_enabled():
        whisper_text = await _whisper_fallback(url)
        if whisper_text:
            return ExtractionResult(
                source_type="youtube",
                url=url,
                text=whisper_text,
                metadata=metadata,
                extraction_method="whisper_fallback",
                warning="transcript_unavailable_used_whisper",
            )

    # Degraded: metadata only
    title = metadata.get("title", "")
    description = metadata.get("description", "")
    text = (
        f"{title}\n\n{description}"
        if title or description
        else f"[No transcript or description available for {url}]"
    )
    return ExtractionResult(
        source_type="youtube",
        url=url,
        text=text,
        metadata=metadata,
        extraction_method="metadata_only",
        success=False,
        warning="no_transcript_available",
    )


async def _fetch_metadata(url: str) -> dict:
    """yt-dlp metadata-only extraction. Synchronous API wrapped in a
    thread to avoid blocking the event loop."""
    def _sync() -> dict:
        try:
            import yt_dlp
        except ImportError:
            return {"error": "yt-dlp not installed (install monogram[ingestion-video])"}

        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": False,
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False) or {}
        except Exception as e:
            log.debug("yt-dlp metadata failed for %s: %s", url, e)
            return {"error": str(e)[:200]}

        return {
            "title": info.get("title"),
            "channel": info.get("uploader") or info.get("channel"),
            "duration": info.get("duration"),
            "upload_date": info.get("upload_date"),
            "description": (info.get("description") or "")[:500],
            "view_count": info.get("view_count"),
        }

    return await asyncio.to_thread(_sync)


async def _fetch_transcript(video_id: str) -> str | None:
    """Fetch transcript via youtube-transcript-api v1.x API.

    Returns concatenated text, or None if unavailable.
    """
    def _sync() -> str | None:
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
        except ImportError:
            log.debug("youtube-transcript-api not installed")
            return None

        # v1.0+ API: YouTubeTranscriptApi() instance with .fetch()
        try:
            ytt = YouTubeTranscriptApi()
        except TypeError:
            # Older pre-1.0 versions had the old static API — try that
            try:
                raw = YouTubeTranscriptApi.get_transcript(video_id, languages=["en", "ko"])  # type: ignore
                return " ".join(entry.get("text", "") for entry in raw)
            except Exception as e:
                log.debug("old-API transcript fetch failed: %s", e)
                return None

        try:
            # Prefer explicit language selection; fall back to default
            try:
                fetched = ytt.list(video_id).find_transcript(["en", "ko"]).fetch()
            except Exception:
                fetched = ytt.fetch(video_id)
        except Exception as e:
            log.debug("transcript fetch failed for %s: %s", video_id, e)
            return None

        # FetchedTranscript has .snippets attr OR iterable of snippets
        snippets = getattr(fetched, "snippets", None) or list(fetched)
        parts = []
        for s in snippets:
            t = getattr(s, "text", None) or (s.get("text") if isinstance(s, dict) else None)
            if t:
                parts.append(t)
        return " ".join(parts) if parts else None

    return await asyncio.to_thread(_sync)


async def _is_whisper_enabled() -> bool:
    """Check vault config for opt-in Whisper fallback."""
    try:
        from ..vault_config import load_vault_config
        cfg = load_vault_config()
        return bool(getattr(cfg, "youtube_whisper_fallback", False))
    except Exception:
        return False


async def _whisper_fallback(url: str) -> str | None:
    """Whisper fallback — STUB in v0.8. Real implementation tracked for v0.8.1.

    A user who has set ``youtube_whisper_fallback: true`` gets an explicit
    warning (not a silent metadata-only fallback) so the mismatch between
    expectation and reality is visible in logs.
    """
    try:
        import whisper  # type: ignore  # noqa: F401
    except ImportError:
        log.warning(
            "whisper fallback requested for %s but openai-whisper is not "
            "installed (install monogram[ingestion-whisper])",
            url,
        )
        return None

    log.warning(
        "whisper fallback is a stub in v0.8 — video %s will fall through to "
        "metadata-only. Track v0.8.1 for real implementation.",
        url,
    )
    return None
