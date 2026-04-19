# Monogram — Ingestion Spec

> How each source type becomes markdown.
> Storage layout, memory architecture, and model routing are in `docs/architecture.md`.
> Domain schema lives in your vault's `identity/` folder (user-defined).

---

## 0. Critical Model Note

**Gemini 2.0 Flash shuts down June 1, 2026.**
All references in this codebase use `gemini-2.5-flash-lite` as the default
baseline model. See `docs/architecture.md` section 3 for routing.

---

## 1. Core Principle

Every source type — image, video, PDF, link — normalizes to markdown
before entering the agent pipeline.

```
ANY SOURCE → [extraction layer] → markdown text → agent pipeline → scheduler/wiki/
```

Why markdown:
- LLM-native, zero token overhead from XML/HTML noise
- Human-readable, editable directly in Obsidian
- Git-diffable — every change is auditable
- No vendor lock-in — files outlast any app

The extraction layer is dumb: it converts source to text. Classification,
routing, and storage decisions happen in the agent pipeline (see
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
youtube URL → yt-dlp (metadata) + youtube-transcript-api (transcript)
           → agent pipeline for summarization
```

Transcript is preferred. Vision pass on keyframes only if transcript
unavailable.

```python
# ingestion/youtube.py
from youtube_transcript_api import YouTubeTranscriptApi
import yt_dlp

def extract(video_url: str) -> dict:
    video_id = extract_video_id(video_url)
    
    # metadata always
    with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True}) as ydl:
        info = ydl.extract_info(video_url, download=False)
    
    # transcript preferred
    try:
        transcript = YouTubeTranscriptApi.get_transcript(video_id)
        text = " ".join(seg["text"] for seg in transcript)
    except Exception:
        # fallback: yt-dlp audio → Whisper
        text = whisper_transcribe_from_url(video_url)
    
    return {
        "title": info.get("title"),
        "channel": info.get("channel"),
        "duration": info.get("duration"),
        "upload_date": info.get("upload_date"),
        "description": info.get("description"),
        "transcript": text,
    }
```

---

### 2.3 Instagram & TikTok

```
ig/tiktok URL
  ├── yt-dlp download (temp file)
  │     ├── photo → image pipeline (2.1) → delete temp
  │     └── video → keyframes + Whisper audio → delete temp
  └── metadata: caption, hashtags, author, url, timestamp
```

Media is ephemeral. Download → process → delete. Only markdown is stored.

- Public content: `yt-dlp` handles without auth
- Private/stories: `instagrapi` with user-provided session cookie
- Same pipeline for both platforms

Normalized output:

```markdown
## [first 60 chars of caption]
**Source:** https://instagram.com/p/xxx
**Author:** @username
**Type:** video | photo
**Posted:** 2026-04-16
**Duration:** 0:43 (video only)

### Summary
[visual content, speech transcription, key points]

### Concepts
[extracted topics relevant to wiki schema]

### Hashtags
#tag1 #tag2 #tag3
```

URL = permanent reference. Summary = searchable artifact.

---

### 2.4 Web Link

```
URL
  ├── arxiv.org   → arxiv API (structured metadata + abstract + citations)
  ├── github.com  → GitHub API (README + repo metadata)
  ├── static page → trafilatura (fast body extraction)
  └── JS-heavy    → jina.ai reader (r.jina.ai/{url}, free)
```

Tiered extraction with fallback:

```python
# ingestion/web.py
def extract(url: str) -> str:
    if "arxiv.org" in url:
        return extract_arxiv(url)          # Semantic Scholar enrichment
    if "github.com" in url:
        return extract_github(url)
    
    # try trafilatura first (faster)
    raw = trafilatura.fetch_url(url)
    text = trafilatura.extract(raw) if raw else None
    
    if not text or len(text) < 200:
        # fallback: jina reader
        text = requests.get(f"https://r.jina.ai/{url}", timeout=30).text
    
    return text
```

arXiv enrichment adds:
- Authors, year, abstract
- Semantic Scholar citation count + related papers
- Link to PDF

---

### 2.5 Text (plain, personal notes)

Direct passthrough for the agent pipeline. Flash-Lite's 1M context means
no chunking at personal scale.

```python
# ingestion/text.py
def extract(text: str) -> str:
    if token_count(text) < 900_000:
        return text
    return hierarchical_summarize(text)   # map-reduce for book-length input
```

For large texts (books, long reports): map-reduce summarization.
Each chunk → summary → summaries → final synthesis.

---

### 2.6 PDF

Two-tier based on complexity:

```
PDF
  ├── simple (text-based, single column) → MarkItDown (~100× faster)
  └── complex (multi-column, tables, scanned, scientific) → Docling
```

Complexity detection: if MarkItDown output has >10% garbled text or table
markers fail → retry with Docling.

```python
# ingestion/pdf.py
def extract(pdf_path: str) -> str:
    from markitdown import MarkItDown
    md = MarkItDown()
    result = md.convert(pdf_path)
    
    if quality_check(result.text_content):
        return result.text_content
    
    # fallback: Docling handles scientific papers, complex layouts
    import docling
    doc = docling.DocumentConverter().convert(pdf_path)
    return doc.document.export_to_markdown()
```

For arxiv papers: always Docling (multi-column, equations, tables).

---

### 2.7 Office Documents

```
docx / pptx / xlsx → MarkItDown → markdown
hwp (Korean)       → LibreOffice CLI → PDF → 2.6 PDF pipeline
```

HWP is the hard case. No Python library handles it cleanly. LibreOffice
headless conversion is the most reliable path:

```bash
libreoffice --headless --convert-to pdf document.hwp
```

Then process via Docling.

---

## 3. Pipeline Summary (Extraction Layer Only)

```
SOURCE          LIBRARY                  OUTPUT FORMAT
──────────────────────────────────────────────────────
image           Gemini Flash-Lite vision text description
youtube         yt-dlp + transcript-api  text + metadata
instagram       yt-dlp + vision/whisper  text + url
tiktok          yt-dlp + vision/whisper  text + url
web link        trafilatura / jina.ai    markdown
arxiv           arxiv API + S2           markdown + meta
github          gh API + README          markdown + meta
text            passthrough              text
pdf (simple)    MarkItDown               markdown
pdf (complex)   Docling                  markdown
docx/pptx/xlsx  MarkItDown               markdown
hwp             LibreOffice → Docling    markdown
```

Once normalized to markdown, the agent pipeline (see `docs/architecture.md`
section 1) takes over: orchestrator → classifier → extractor → verifier → writer.

---

## 4. Dependencies

```
# core extraction
yt-dlp                  # youtube, instagram, tiktok
youtube-transcript-api  # youtube transcripts
trafilatura             # web content extraction
markitdown              # office docs + simple PDFs
docling                 # complex PDFs, scientific papers

# audio transcription (fallback for video without transcript)
openai-whisper          # local, free — OR openai API whisper

# instagram (optional, for private content)
instagrapi              # requires session cookie

# office conversion
# libreoffice           # system install, for hwp → pdf

# existing package deps
telethon
aiogram
PyGithub
python-dotenv
google-generativeai     # direct Gemini API (litellm wraps this)
litellm                 # unified LLM abstraction
```

---

## 5. GitHub Activity Digest (v0.3+)

A separate ingestion channel — not Telegram drops, but GitHub webhooks
and polling. Each push, PR, or issue on watched repos becomes context for
the agent.

```python
# core/github_digest.py (sketch)

# Watched repos from env:
# MONOGRAM_WATCH_REPOS="<your-github-user>/mono,<your-github-user>/monogram,..."

async def on_github_event(event: dict) -> None:
    """Called from webhook handler or polling cron."""
    
    if event["type"] == "PushEvent":
        summary = await classify_push(event)   # uses Flash-Lite
        await github_store.append(
            f"log/github-{datetime.now():%Y-%m}.md",
            format_push_entry(event, summary),
            f"digest: push to {event['repo']['name']}"
        )
```

Daily cross-repo brief runs at 6am via GitHub Actions:

```
📊 GitHub activity yesterday
   monogram:   12 commits, Phase B1 merged
   scheduler:   2 commits, side-project1 status updated
   side-project2:    0 commits (paused — GPU blocker)

⚠️ Stall warning: side-project3 hasn't seen activity in 14 days
```

Stall detection: any project in MEMORY.md marked `status: active` with
no commits in 14+ days surfaces in the morning brief.

---

## 6. Where This Fits

- **This doc:** how sources become markdown (the dumb layer)
- **Your vault's `identity/` folder** (user-defined): what entity types
  exist, confidence rules, routing
- **`docs/architecture.md`:** agent pipeline, memory layout, model routing

Extraction is the simplest layer. It's dumb on purpose — any intelligence
(classification, verification, supersession) happens in the agent pipeline,
not here.
