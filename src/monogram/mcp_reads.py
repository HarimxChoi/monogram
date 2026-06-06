"""MCP read-tool implementations; all use safe_read to respect life/credentials/ blocking."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from . import github_store
from .safe_read import safe_read


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _yesterday_str() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")


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


_LIFE_ENTRY_RE = re.compile(
    r"^## (\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}) — (.+)$", re.MULTILINE
)


async def query_life(area: str, days: int = 7, limit: int = 20) -> str:
    """Credentials area is unconditionally blocked."""
    area = (area or "").strip().lower()
    if not area:
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
    entries.reverse()
    return json.dumps({"entries": entries[:limit], "area": area}, indent=2)


async def get_morning_brief(date: str = "") -> str:
    date = (date or "").strip() or _yesterday_str()
    content = safe_read(f"daily/{date}/report.md")
    if not content:
        return f"No morning brief for {date}."
    return content


async def current_project_state(slug: str) -> str:
    slug = (slug or "").strip().lower()
    if not slug:
        return "Usage: current_project_state(slug=<project-slug>)"
    content = safe_read(f"projects/{slug}.md")
    if not content:
        archived = safe_read(f"projects/archive/{slug}.md")
        if archived:
            return f"[archived]\n\n{archived}"
        return f"Project '{slug}' not found."
    return content


async def get_board() -> str:
    content = safe_read("board.md")
    return content or "board.md not found or empty."
