"""YouTube extractor — transcript via youtube-transcript-api v1.x, metadata via yt-dlp.

youtube-transcript-api v1.0 BREAKING CHANGE: static get_transcript() removed; use
YouTubeTranscriptApi().fetch(video_id) or .list().find_transcript().fetch().
Whisper fallback is opt-in because it is CPU-heavy and downloads audio.
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

    metadata = await _fetch_metadata(url)

    transcript_text = await _fetch_transcript(video_id)
    if transcript_text:
        return ExtractionResult(
            source_type="youtube",
            url=url,
            text=transcript_text,
            metadata=metadata,
            extraction_method="transcript",
        )

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
    def _sync() -> str | None:
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
        except ImportError:
            log.debug("youtube-transcript-api not installed")
            return None

        try:
            ytt = YouTubeTranscriptApi()
        except TypeError:
            # pre-1.0 versions used the old static API
            try:
                raw = YouTubeTranscriptApi.get_transcript(video_id, languages=["en", "ko"])  # type: ignore
                return " ".join(entry.get("text", "") for entry in raw)
            except Exception as e:
                log.debug("old-API transcript fetch failed: %s", e)
                return None

        try:
            try:
                fetched = ytt.list(video_id).find_transcript(["en", "ko"]).fetch()
            except Exception:
                fetched = ytt.fetch(video_id)
        except Exception as e:
            log.debug("transcript fetch failed for %s: %s", video_id, e)
            return None

        # FetchedTranscript: .snippets attr OR iterable depending on version
        snippets = getattr(fetched, "snippets", None) or list(fetched)
        parts = []
        for s in snippets:
            t = getattr(s, "text", None) or (s.get("text") if isinstance(s, dict) else None)
            if t:
                parts.append(t)
        return " ".join(parts) if parts else None

    return await asyncio.to_thread(_sync)


async def _is_whisper_enabled() -> bool:
    try:
        from ..vault_config import load_vault_config
        cfg = load_vault_config()
        return bool(getattr(cfg, "youtube_whisper_fallback", False))
    except Exception:
        return False


async def _whisper_fallback(url: str) -> str | None:
    """Stub — not yet implemented; returns None until demand justifies complexity."""
    try:
        import whisper  # type: ignore  # noqa: F401
    except ImportError:
        log.info("whisper fallback requested but openai-whisper not installed")
        return None

    log.info("whisper fallback stub — not yet implemented for %s", url)
    return None
