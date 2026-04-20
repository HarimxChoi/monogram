# Monogram — Ingestion Spec

> How each source type becomes markdown.
> Storage layout, memory architecture, and model routing are in `docs/architecture.md`.
> Domain schema lives in your vault's `identity/` folder (user-defined).

---

## 1. Core Principle

Every source type — image, video, PDF, link — normalizes to markdown
before entering the agent pipeline.

```
ANY SOURCE → [extraction layer] → markdown text → agent pipeline → scheduler/wiki/
```

Markdown is LLM-native, git-diffable, and editable in any plain-text editor.

The extraction layer is dumb: source → text. Classification, routing,
and storage decisions happen in the agent pipeline (see
`docs/architecture.md` section 1), not in the extractor.

---

## 2. Source Extraction by Type

### 2.1 Image

```
telegram drop (photo) → bytes → Gemini 2.5 Flash-Lite vision
```

Flash-Lite handles images natively (1M context, multimodal).
Direct bytes → base64 → API call. No preprocessing needed.

```python
# ingestion/image.py
async def extract(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
    from monogram.llm import complete_vision
    prompt = (
        "Describe this image in detail. Extract any visible text verbatim. "
        "Identify key concepts, objects, and context relevant to knowledge capture."
    )
    return await complete_vision(image_bytes, prompt, mime_type=mime_type, task="vision")
```

Output: structured text description with extracted text and concepts.

---

### 2.2 YouTube

```
youtube URL → yt-dlp (metadata) + youtube-transcript-api v1.x (transcript)
           → agent pipeline for summarization
```

Transcript is preferred. Whisper fallback is opt-in via
`youtube_whisper_fallback` in vault config — CPU/GPU-heavy, transcript
API covers >90% of cases. On transcript unavailable + Whisper disabled,
extractor returns metadata-only with a warning.

The `youtube-transcript-api` v1.x API is instance-based
(`YouTubeTranscriptApi().fetch(video_id)`); the old static
`get_transcript()` is deprecated. `ingestion/youtube.py` handles both
for compatibility.

---

### 2.3 Instagram & TikTok

```
ig/tiktok URL → yt-dlp (metadata + caption, skip_download)
             → caption + hashtags + author → markdown
```

v0.8 scope: public content only, metadata + caption. No media download.
Whisper transcription of video audio is deferred (opt-in, heavy).
Private content (stories, private accounts) requires session cookies
and is not supported in v0.8.

URL is the permanent reference; caption is the searchable artifact.
SSRF-checked via `require_safe_url` before yt-dlp sees the URL — yt-dlp
otherwise follows redirects without hostname validation.

---

### 2.4 Web Link

```
URL
  ├── arxiv.org   → arxiv library (rate-limited client) + optional Semantic Scholar
  ├── github.com  → GitHub API (README + repo metadata)
  ├── static page → trafilatura
  └── JS-heavy    → r.jina.ai reader (free, no auth)
```

Fallback chain in `ingestion/web.py`:

1. `require_safe_url` — rejects private IPs, CGNAT, cloud metadata ranges, non-HTTP schemes.
2. `trafilatura` with `output_format="markdown"`, `include_tables=True`.
3. If result <200 chars: `r.jina.ai/{url}` via httpx (server-side JS render).
4. If both empty: return trafilatura's output with `both_extractors_returned_empty` warning.

arXiv (`ingestion/arxiv_source.py`) uses the `arxiv` library's shared
process-wide `Client()` so concurrent drops respect the 1-request-per-3-seconds
ToU. Semantic Scholar enrichment (citation counts) is opt-in via
`vault_config.arxiv_enrichment`, degrades on 429.

---

### 2.5 Text (plain, personal notes)

Direct passthrough. Flash-Lite's 1M context means no chunking at
personal scale.

```python
# ingestion/text.py
def extract(text: str) -> str:
    if token_count(text) < 900_000:
        return text
    return hierarchical_summarize(text)   # map-reduce for book-length input
```

For large texts (books, long reports): map-reduce. Each chunk → summary
→ summaries → final synthesis.

---

### 2.6 PDF

Two-tier by quality gate:

```
PDF
  ├── fast path: PyMuPDF4LLM (native text, ~100× faster than Docling)
  └── fallback:  Marker (Surya OCR, multi-column, tables, scanned)
```

Quality gate (`_quality_ok` in `ingestion/pdf.py`): if PyMuPDF4LLM
returns <100 chars or printable-ratio <0.85, escalate to Marker.

```python
# ingestion/pdf.py (sketch)
fast = await _pymupdf4llm_extract(pdf_bytes)
if fast and _quality_ok(fast):
    return fast
marker = await _marker_extract(pdf_bytes)    # if marker-pdf installed
return marker or fast or "[PDF extraction failed]"
```

Why not MarkItDown + Docling (the old plan, per the module docstring):

- MarkItDown's PDF success rate is ~25% (pdfminer.six backend, no layout analysis).
- Docling is ~100× slower and ships ~1GB of HuggingFace models.
- Marker handles scanned/multi-column PDFs with Surya OCR; ~1GB install but much higher accuracy.

Download path is SSRF-hardened: `require_safe_url` pre-fetch, then
`safe_stream_bytes` validates every redirect hop with a 20MB size cap.
urllib fallback does not follow redirects (safe default).

---

### 2.7 Office Documents

```
docx / pptx / xlsx → MarkItDown → markdown
hwp (Korean)       → LibreOffice CLI → PDF → §2.6 pipeline
```

MarkItDown is appropriate here (not for PDFs): it wraps `python-docx`,
`python-pptx`, `openpyxl` — all high-quality. 80-95% accuracy on common
documents, ~10MB install, no ML models. See `ingestion/office.py`
docstring for the rationale split.

HWP (`ingestion/hwp.py`) goes through LibreOffice headless. Real threat
surface — CVE-2024-12425, CVE-2024-12426, CVE-2025-1080, CVE-2018-16858
all trigger on document load. Mitigations:

| Control | Purpose |
|---|---|
| Version gate: refuse LibreOffice < 25.2.1 | CVEs patched upstream |
| Minimal env (PATH, LANG, HOME=temp only) | Blocks CVE-2024-12426 env exfil |
| Fresh `UserInstallation` profile dir per run | Contains blast radius |
| `--safe-mode --headless --norestore --nofirststartwizard` | Minimum feature surface |
| 60s subprocess timeout | Bounds hang/CPU exhaustion |
| 20MB input size cap | Bounds parser attack surface |

The resulting PDF is handed to the §2.6 pipeline. See `SECURITY.md`.

---

## 3. Pipeline Summary (Extraction Layer Only)

| Source | Library | Output |
|---|---|---|
| image | Gemini Flash-Lite vision | text description |
| youtube | yt-dlp + youtube-transcript-api v1.x | text + metadata |
| instagram | yt-dlp (metadata + caption) | text + url |
| tiktok | yt-dlp (metadata + caption) | text + url |
| web link | trafilatura / r.jina.ai | markdown |
| arxiv | arxiv lib + Semantic Scholar (opt-in) | markdown + meta |
| github | gh API + README | markdown + meta |
| text | passthrough | text |
| pdf (fast) | PyMuPDF4LLM | markdown |
| pdf (complex) | Marker (Surya OCR) | markdown |
| docx/pptx/xlsx | MarkItDown | markdown |
| hwp | LibreOffice → PDF → §2.6 | markdown |

Once normalized to markdown, the agent pipeline (see
`docs/architecture.md` section 1) takes over: orchestrator → classifier
→ extractor → verifier → writer.

---

## 4. Dependencies

Ingestion is split across opt-in extras. Install what you need.

| Extra | Packages | Covers |
|---|---|---|
| `ingestion-video` | `yt-dlp>=2026.1`, `youtube-transcript-api>=1.0` | YouTube, Instagram, TikTok |
| `ingestion-research` | `arxiv>=2.0`, `httpx>=0.25` | arXiv + Semantic Scholar |
| `ingestion-pdf` | `pymupdf4llm>=0.0.17` | PDF fast path |
| `ingestion-pdf-complex` | `marker-pdf>=1.0` | PDF fallback (Surya OCR, ~1GB) |
| `ingestion-office` | `markitdown>=0.0.1` | docx/pptx/xlsx |
| `ingestion-whisper` | `openai-whisper>=20231117` | YouTube/social audio fallback |
| `ingestion-all` | video + research + pdf + office | everything except complex-pdf + whisper |

```bash
pip install 'mono-gram[ingestion-all]'           # common case
pip install 'mono-gram[ingestion-pdf-complex]'   # add Marker for scanned PDFs
pip install 'mono-gram[ingestion-whisper]'       # add Whisper for video audio
```

System dependency: LibreOffice >=25.2.1 (for HWP; installed via OS package manager).

LLM access is not an ingestion extra — it ships as a core dep via
`litellm`, which abstracts Gemini / Anthropic / OpenAI / Ollama.

---

## 5. GitHub Activity Digest

Implemented in v0.8 as the `monogram digest` subcommand (see
`src/monogram/cli.py` and `src/monogram/digest.py`). Not Telegram drops
— a separate ingestion channel that polls recent commits from watched
repos into the daily log.

```bash
monogram digest --hours 24
```

Watched repos are configured via `MONOGRAM_WATCH_REPOS`. Each commit
batch is summarized and appended to `daily/<date>/commits.md`.

Stall detection and the cross-repo morning brief format are planned
for a later release; the current subcommand covers the ingestion half.

---

## 6. Where This Fits

Extraction is the dumb layer — source to text. Intelligence
(classification, verification, supersession) lives in the agent
pipeline; see `docs/architecture.md`.
