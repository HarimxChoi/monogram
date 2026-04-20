"""v0.4b: MCP read-tool implementations.

Pure reads — no writes, no LLM calls. Extracted into this module so
mcp_server.py stays short. All helpers use safe_read to respect
life/credentials/ blocking.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from . import github_store
from .safe_read import safe_read
from .taxonomy import slugify

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _yesterday_str() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")


# ── search_wiki ──


def _list_wiki_files() -> list[str]:
    try:
        repo = github_store._repo()
        contents = repo.get_contents("wiki")
    except Exception:
        return []
    return [
        f.path for f in contents
        if f.type == "file"
        and f.path.endswith(".md")
        and not f.path.endswith("index.md")
    ]


async def search_wiki(query: str, limit: int = 10) -> str:
    """Case-insensitive substring search over wiki/index.md + tag overlap.

    Returns top-N matches as JSON with (slug, summary, tags).
    """
    query = (query or "").strip().lower()
    if not query:
        return json.dumps({"matches": [], "error": "empty query"})

    index = safe_read("wiki/index.md") or ""
    matches: list[dict] = []
    pattern = re.compile(
        r"^- \[\[([a-z0-9-]+)\]\] — (.+?) \[(.*?)\]", re.MULTILINE
    )
    for m in pattern.finditer(index):
        slug, summary, tags_str = m.groups()
        tags = [t.lstrip("#") for t in tags_str.split() if t]
        hay = f"{slug} {summary} {' '.join(tags)}".lower()
        if query in hay:
            matches.append({"slug": slug, "summary": summary.strip(), "tags": tags})
    return json.dumps({"matches": matches[:limit]}, indent=2)


# ── query_life ──


_LIFE_ENTRY_RE = re.compile(
    r"^## (\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}) — (.+)$", re.MULTILINE
)


async def query_life(area: str, days: int = 7, limit: int = 20) -> str:
    """Return recent entries from life/<area>.md within the last N days.

    Credentials area is always blocked.
    """
    area = slugify((area or "").strip())
    if not area or area == "untitled":
        return json.dumps({"entries": [], "error": "empty area"})
    if area == "credentials":
        return json.dumps({"entries": [], "error": "credentials area blocked"})

    since = datetime.now(timezone.utc) - timedelta(days=days)
    since_iso = since.strftime("%Y-%m-%dT%H:%M:00")

    content = safe_read(f"life/{area}.md")
    if not content:
        return json.dumps({"entries": [], "error": f"life/{area}.md not found"})

    entries: list[dict] = []
    for m in _LIFE_ENTRY_RE.finditer(content):
        ts_iso = f"{m.group(1)}T{m.group(2)}:00"
        if ts_iso >= since_iso:
            entries.append({"timestamp": f"{m.group(1)} {m.group(2)}", "title": m.group(3).strip()})
    entries.reverse()  # latest first
    return json.dumps({"entries": entries[:limit], "area": area}, indent=2)


# ── get_morning_brief ──


async def get_morning_brief(date: str = "") -> str:
    """Return daily/<date>/report.md. Defaults to yesterday."""
    date = (date or "").strip() or _yesterday_str()
    if not _ISO_DATE_RE.match(date):
        return f"Invalid date {date!r}; expected YYYY-MM-DD."
    content = safe_read(f"daily/{date}/report.md")
    if not content:
        return f"No morning brief for {date}."
    return content


# ── current_project_state ──


async def current_project_state(slug: str) -> str:
    """Return frontmatter + body of projects/<slug>.md."""
    slug = slugify((slug or "").strip())
    if not slug or slug == "untitled":
        return "Usage: current_project_state(slug=<project-slug>)"
    content = safe_read(f"projects/{slug}.md")
    if not content:
        # Fall back to archive
        archived = safe_read(f"projects/archive/{slug}.md")
        if archived:
            return f"[archived]\n\n{archived}"
        return f"Project '{slug}' not found."
    return content


# ── get_board ──


async def get_board() -> str:
    """Return the current board.md contents."""
    content = safe_read("board.md")
    return content or "board.md not found or empty."
