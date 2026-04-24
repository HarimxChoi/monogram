"""Map-reduce condenser for large plain-text drops.

Telegram plain messages cap at 4096 chars, but file attachments (.txt,
.md, .csv, .yaml, .log…) routinely arrive at 10KB-2MB. The pipeline
otherwise only sees the first 2000 chars of a drop (via
`build_drop_text` / `to_pipeline_snippet`), so a long document ends up
classified on its preface alone.

This module replaces that preface with an LLM-generated condensed
summary that preserves concrete nouns, specific findings, and
document structure. The full text is still written to the raw/ tier
via ExtractionResult.raw_text so nothing is lost.

Thresholds (char counts):
  - ≤ 8_000: passthrough — no LLM call, returns input unchanged.
  - 8_001 – 100_000: single-pass Flash-Lite summary — one LLM call.
  - > 100_000: map-reduce — parallel chunk summaries + one synthesis
    call. N+1 LLM calls total (N = chunk count).

Output target is ~1500 chars so that build_drop_text's 2000-char cap
doesn't re-cut the condensed form.
"""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("monogram.ingestion.text")


# Thresholds tuned for Gemini 2.5 Flash-Lite (1M-token context) and
# Gemini 2.5 Flash for synthesis. Chunk size is well inside Flash-Lite
# so we can fan out dozens of parallel summarize calls cheaply.
_PASSTHROUGH_MAX = 8_000
_SINGLE_PASS_MAX = 100_000
_CHUNK_SIZE = 40_000
_CHUNK_OVERLAP = 500


async def condense_for_pipeline(
    text: str,
    *,
    filename: str = "",
    language_hint: str = "",
) -> str:
    """Return a pipeline-friendly condensed form of `text`.

    Never raises — on LLM failure the function falls back to a head+tail
    preview so the drop still reaches the pipeline with SOMETHING useful.
    Passthrough returns the input string unchanged (same object).
    """
    n = len(text)
    if n <= _PASSTHROUGH_MAX:
        return text

    try:
        if n <= _SINGLE_PASS_MAX:
            return await _single_pass(
                text, filename=filename, language_hint=language_hint
            )
        return await _map_reduce(
            text, filename=filename, language_hint=language_hint
        )
    except Exception as e:
        log.warning(
            "condense: LLM path failed (%r); falling back to head+tail", e
        )
        return _fallback_head_tail(text, filename=filename)


# ── Chunk splitting ──────────────────────────────────────────────────

def _split_on_boundaries(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Split `text` into chunks near `chunk_size` chars at natural boundaries.

    Prefers paragraph breaks, then single newlines, then sentence
    terminators (. ! ?, plus CJK 。). Falls back to hard slice only when
    no boundary is reachable within the second half of the window.

    `overlap` chars are carried over at each boundary so a sentence
    straddling the boundary survives in at least one neighbor.
    """
    chunks: list[str] = []
    n = len(text)
    i = 0
    while i < n:
        end = min(i + chunk_size, n)
        if end < n:
            half = i + chunk_size // 2
            for sep in ("\n\n", "\n", ". ", "。", "! ", "? "):
                pos = text.rfind(sep, half, end)
                if pos != -1:
                    end = pos + len(sep)
                    break
        chunks.append(text[i:end])
        if end >= n:
            break
        i = max(end - overlap, i + 1)
    return chunks


# ── Fallback when LLM is unavailable ────────────────────────────────

def _fallback_head_tail(text: str, filename: str) -> str:
    head = text[:1000].strip()
    tail = text[-500:].strip() if len(text) > 2000 else ""
    lines = [
        f"[large {filename or 'text'} — LLM condense unavailable, "
        f"showing head + tail of {len(text):,} chars]",
        "",
        "## Head",
        head,
    ]
    if tail:
        lines += ["", "## Tail", tail]
    return "\n".join(lines)


# ── LLM prompts ──────────────────────────────────────────────────────

_SINGLE_PASS_PROMPT = (
    "Condense the document below into a structured overview that a "
    "knowledge-pipeline classifier will read. Keep it under 1500 "
    "characters.\n\n"
    "File: {filename}\n{language_line}\n\n"
    "Requirements:\n"
    "  - First line: one-sentence statement of the main topic, plain "
    "text (no '#', no 'Title:').\n"
    "  - 3-6 bulleted key points.\n"
    "  - 1-2 short prose paragraphs of overview.\n"
    "  - Preserve concrete nouns: people, places, tech terms, numbers, "
    "direct quotes worth keeping.\n"
    "  - Write in the same language as the document.\n\n"
    "Document:\n---\n{text}\n---\n"
)


_CHUNK_PROMPT = (
    "Summarize section {idx} of {total} in 100-150 words. Preserve "
    "concrete nouns, direct quotes, figures, and specific findings. "
    "Write in the input's language. No preamble.\n\n"
    "---\n{chunk}\n---\n"
)


_SYNTHESIS_PROMPT = (
    "Synthesize {chunk_count} section summaries of a single document "
    "into one condensed overview for a knowledge-pipeline classifier. "
    "Keep under 1500 characters.\n\n"
    "File: {filename}\n{language_line}\n\n"
    "Requirements:\n"
    "  - First line: main topic as a plain sentence (no '#').\n"
    "  - 3-6 bulleted key points.\n"
    "  - 1-2 short prose paragraphs describing how the sections relate.\n"
    "  - Preserve concrete nouns and document structure.\n"
    "  - Use the language of the summaries.\n\n"
    "Section summaries:\n{summaries}\n"
)


def _lang_line(hint: str) -> str:
    return f"Primary language: {hint}" if (hint or "").strip() else ""


# ── LLM paths ────────────────────────────────────────────────────────

async def _single_pass(
    text: str, *, filename: str, language_hint: str
) -> str:
    from ..llm import complete
    from ..models import get_model

    prompt = _SINGLE_PASS_PROMPT.format(
        filename=filename or "(pasted text)",
        language_line=_lang_line(language_hint),
        text=text,
    )
    out = await complete(prompt, model=get_model("low"))
    prefix = f"[condensed from {len(text):,} chars of {filename or 'text'}]\n\n"
    return prefix + out.strip()


async def _map_reduce(
    text: str, *, filename: str, language_hint: str
) -> str:
    from ..llm import complete
    from ..models import get_model

    chunks = _split_on_boundaries(text, _CHUNK_SIZE, _CHUNK_OVERLAP)
    total = len(chunks)
    low_model = get_model("low")
    tasks = [
        complete(
            _CHUNK_PROMPT.format(idx=i + 1, total=total, chunk=c),
            model=low_model,
        )
        for i, c in enumerate(chunks)
    ]
    raw_summaries = await asyncio.gather(*tasks, return_exceptions=True)

    good: list[tuple[int, str]] = []
    for i, r in enumerate(raw_summaries):
        if isinstance(r, Exception):
            log.warning(
                "condense.map: chunk %d/%d failed: %r", i + 1, total, r
            )
            continue
        s = (r or "").strip() if isinstance(r, str) else ""
        if s:
            good.append((i + 1, s))

    if not good:
        log.warning(
            "condense.map: all %d chunks failed; using head+tail", total
        )
        return _fallback_head_tail(text, filename=filename)

    joined = "\n\n".join(f"## Section {idx}\n{s}" for idx, s in good)
    prompt = _SYNTHESIS_PROMPT.format(
        chunk_count=total,
        filename=filename or "(pasted text)",
        language_line=_lang_line(language_hint),
        summaries=joined,
    )
    synthesis = await complete(prompt, model=get_model("mid"))
    prefix = (
        f"[map-reduce condensed from {len(text):,} chars / "
        f"{total} chunks of {filename or 'text'}]\n\n"
    )
    return prefix + synthesis.strip()
