"""v0.6 — Dashboard data collection + Jinja rendering.

Single entry point: `render_bundle()` returns UTF-8 HTML bytes. That
plaintext is then passed through `encryption_layer.wrap()` before upload.

Data sources (all via safe_read — credentials never reach the LLM or the UI):
  - board.md              : project board, optional critpath frontmatter
  - projects/*.md         : active + inactive
  - projects/archive/*.md : recent done entries
  - life/<area>.md        : last 7 days of H3 entries per area (NOT credentials)
  - wiki/index.md         : recent + tag cloud
  - daily/<today>/drops.md + commits.md

Rate-limit safety: `asyncio.Semaphore(10)` caps concurrent GitHub API calls.
"""
from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import github_store
from .safe_read import safe_read

_SEMA = asyncio.Semaphore(10)
_TEMPLATE_DIR = Path(__file__).parent / "webui" / "templates"
_MONOGRAM_VERSION = "v0.6"


# ── Jinja env ──


def _jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "html.j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _read_static(name: str) -> str:
    return (_TEMPLATE_DIR / name).read_text(encoding="utf-8")


# ── concurrency-capped reads ──


async def _read(path: str) -> str:
    async with _SEMA:
        return await asyncio.get_event_loop().run_in_executor(
            None, safe_read, path
        )


# ── time helpers ──


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _today_iso() -> str:
    return _now().strftime("%Y-%m-%d")


def _relative_time(iso_like: str | datetime | None) -> str:
    """Render '2h ago', '3d ago', 'yesterday', etc. UTC-naive tolerant."""
    if iso_like is None:
        return "—"
    if isinstance(iso_like, str):
        try:
            dt = datetime.fromisoformat(iso_like.replace("Z", "+00:00"))
        except Exception:
            return iso_like
    else:
        dt = iso_like
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = _now() - dt
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    days = seconds // 86400
    if days == 1:
        return "yesterday"
    if days < 30:
        return f"{days}d ago"
    return dt.strftime("%Y-%m-%d")


# ── board / projects ──


_LIFE_ENTRY_RE = re.compile(
    r"^## (\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}) — (.+)$", re.MULTILINE
)

_WIKI_INDEX_RE = re.compile(
    r"^- \[\[([a-z0-9-]+)\]\] — (.+?) \[(.*?)\](?: \((\d{4}-\d{2}-\d{2})\))?",
    re.MULTILINE,
)


def _deadline_label(fm: dict) -> tuple[str, str]:
    """Return (label, css_modifier) for a project's deadline frontmatter."""
    deadline = fm.get("deadline")
    if not deadline:
        return "—", "ok"
    try:
        dt = datetime.fromisoformat(str(deadline).replace("Z", "+00:00"))
    except Exception:
        return str(deadline), "ok"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta_days = (dt - _now()).days
    if delta_days < 0:
        return f"overdue {-delta_days}d", "urgent"
    if delta_days <= 7:
        return f"+{delta_days}d", "urgent"
    if delta_days <= 30:
        return f"+{delta_days}d", "warn"
    return f"+{delta_days}d", "ok"


def _extract_note(body: str, max_chars: int = 140) -> str:
    """First non-heading paragraph, trimmed."""
    for para in body.split("\n\n"):
        text = para.strip()
        if not text or text.startswith("#"):
            continue
        text = text.replace("\n", " ").strip("- *")
        return text[:max_chars]
    return ""


def _extract_blocker(fm: dict, body: str) -> str | None:
    if fm.get("blocker"):
        return str(fm["blocker"])[:100]
    m = re.search(r"(?i)\bblocker[:\s]+(.+)", body)
    return m.group(1).strip()[:100] if m else None


def _make_sparkline(points: list[int], width: int = 70, height: int = 14) -> str:
    """Generate a tiny inline SVG polyline. Zero-data case returns a dim line."""
    if not points:
        return f'<svg class="spark spark--dim" viewBox="0 0 {width} {height}"></svg>'
    lo, hi = min(points), max(points)
    span = max(hi - lo, 1)
    step = width / max(len(points) - 1, 1) if len(points) > 1 else 0
    coords = []
    for i, v in enumerate(points):
        x = i * step
        y = height - 1 - ((v - lo) / span) * (height - 2)
        coords.append(f"{x:.1f},{y:.1f}")
    poly = " ".join(coords)
    cls = "spark spark--dim" if sum(points) == 0 else "spark"
    return (
        f'<svg class="{cls}" viewBox="0 0 {width} {height}" preserveAspectRatio="none">'
        f'<polyline points="{poly}" stroke="currentColor" stroke-width="1.2" fill="none"/>'
        f'</svg>'
    )


def _count_mentions_per_day(slug: str, days: int = 30) -> list[int]:
    """Count occurrences of `slug` across daily/<D>/drops.md + commits.md."""
    counts: list[int] = []
    now = _now()
    for i in range(days - 1, -1, -1):
        d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        drops = safe_read(f"daily/{d}/drops.md") or ""
        commits = safe_read(f"daily/{d}/commits.md") or ""
        hay = (drops + "\n" + commits).lower()
        counts.append(hay.count(slug.lower()))
    return counts


def _list_dir(folder: str) -> list[str]:
    """List repo files under `folder` via github_store. Returns [] on any error."""
    try:
        repo = github_store._repo()
        contents = repo.get_contents(folder)
    except Exception:
        return []
    return [
        f.path
        for f in contents
        if f.type == "file" and f.path.endswith(".md")
    ]


def _project_card(path: str, body_limit: int = 160) -> dict:
    content = safe_read(path) or ""
    fm, body = github_store.parse_metadata(content)
    fm = fm or {}
    slug = Path(path).stem
    deadline_label, deadline_class = _deadline_label(fm)
    tags = fm.get("tags") or []
    tag_summary = ", ".join(tags[:3]) if tags else ""
    note = _extract_note(body or "", body_limit)
    blocker = _extract_blocker(fm, body or "")

    points = _count_mentions_per_day(slug, days=30)
    drops_count = sum(points)
    # commits count: rough estimate from commits.md only
    commit_count = 0
    now = _now()
    for i in range(30):
        d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        commits = safe_read(f"daily/{d}/commits.md") or ""
        commit_count += commits.lower().count(slug.lower())

    metric_html = (
        f"<strong>{drops_count}</strong> drops · <strong>{commit_count}</strong> commits · 30d"
    )
    return {
        "slug": slug,
        "status": fm.get("status", "active") or "active",
        "deadline_label": deadline_label,
        "deadline_class": deadline_class,
        "note": note,
        "blocker": blocker,
        "tags": tags,
        "spark_svg": _make_sparkline(points),
        "metric_html": metric_html,
    }


def _group_projects() -> dict:
    """Return {active:[cards], inactive:[cards], done:[cards], total:int}."""
    active, inactive = [], []
    for p in _list_dir("projects"):
        if p.endswith("/archive") or "/archive/" in p:
            continue
        card = _project_card(p)
        (active if card["status"] == "active" else inactive).append(card)

    done = []
    for p in _list_dir("projects/archive")[:5]:
        card = _project_card(p, body_limit=80)
        card["deadline_label"] = "archived"
        card["deadline_class"] = "ok"
        done.append(card)

    total = len(active) + len(inactive) + len(done)
    return {"active": active, "inactive": inactive, "done": done, "total": total}


# ── critpath ──


def _critpath(board_fm: dict | None, project_cards: list[dict]) -> list[dict]:
    """Items with blockers, urgent deadlines, or explicit priority=critical."""
    items: list[dict] = []
    for card in project_cards:
        if card.get("blocker") or card["deadline_class"] == "urgent":
            items.append({
                "slug": card["slug"],
                "description": card.get("blocker") or card.get("note", ""),
                "deadline_label": (
                    card["deadline_label"]
                    if card["deadline_class"] != "ok" else ""
                ),
            })
            if len(items) >= 3:
                break
    return items


# ── life ──


_LIFE_TAG_CSS = {
    "career": "career",
    "health": "health",
    "finance": "finance",
    "read-watch": "readwatch",
    "readwatch": "readwatch",
    "shopping": "shopping",
    "places": "places",
    "meeting-notes": "meeting-notes",
}


def _life_items(days: int = 7) -> list[dict]:
    from .vault_config import load_vault_config
    cfg = load_vault_config()
    since = _now() - timedelta(days=days)
    since_iso = since.strftime("%Y-%m-%dT%H:%M:00")

    items: list[dict] = []
    for area in cfg.life_categories:
        if area == "credentials":
            continue  # NEVER surface
        content = safe_read(f"life/{area}.md") or ""
        for m in _LIFE_ENTRY_RE.finditer(content):
            ts_iso = f"{m.group(1)}T{m.group(2)}:00"
            if ts_iso < since_iso:
                continue
            title = m.group(3).strip()
            # Extract snippet from the paragraph right after the header
            start = m.end()
            next_header = content.find("\n## ", start)
            snippet_block = (
                content[start:next_header] if next_header > 0 else content[start:]
            )
            snippet = ""
            for line in snippet_block.strip().split("\n"):
                line = line.strip()
                if line and not line.startswith("_"):
                    snippet = line[:160]
                    break
            try:
                dt = datetime.fromisoformat(ts_iso).replace(tzinfo=timezone.utc)
            except Exception:
                dt = _now()
            items.append({
                "ts": dt,
                "time_relative": _relative_time(dt),
                "title": title[:140],
                "snippet": snippet,
                "area_label": area.upper(),
                "area_css": _LIFE_TAG_CSS.get(area, "places"),
            })
    items.sort(key=lambda x: x["ts"], reverse=True)
    return items[:20]


# ── wiki ──


def _wiki_data(recent_n: int = 10) -> dict:
    """Recent entries + tag cloud from wiki/index.md."""
    index = safe_read("wiki/index.md") or ""
    rows: list[tuple[str, str, list[str], str]] = []
    for m in _WIKI_INDEX_RE.finditer(index):
        slug, summary, tags_str, date_str = m.groups()
        tags = [t.lstrip("#") for t in (tags_str or "").split() if t]
        rows.append((slug, summary.strip(), tags, date_str or ""))

    rows.sort(key=lambda r: r[3], reverse=True)
    recent = []
    for slug, summary, tags, date_str in rows[:recent_n]:
        recent.append({
            "slug": slug,
            "title": summary or slug,
            "tags": tags[:5],
            "time_relative": _relative_time(date_str) if date_str else "—",
        })

    # Tag cloud: top 12 tags by frequency
    counter: dict[str, int] = {}
    for _, _, tags, _ in rows:
        for t in tags:
            counter[t] = counter.get(t, 0) + 1
    tag_cloud = [
        {"name": name, "count": count}
        for name, count in sorted(counter.items(), key=lambda x: -x[1])[:12]
    ]
    return {"recent": recent, "tag_cloud": tag_cloud, "total": len(rows)}


# ── today ──


_DROP_LINE_RE = re.compile(
    r"^## (\d{2}:\d{2})\s*\n\*\*(\S+)\*\* → `([^`]+)`", re.MULTILINE
)

_COMMIT_LINE_RE = re.compile(
    r"-\s*`([a-f0-9]+)`\s+\S+\s+\S+\s*\[[^\]]+\]\s*(.+)$", re.MULTILINE
)


def _today_data() -> dict:
    today = _today_iso()
    drops_content = safe_read(f"daily/{today}/drops.md") or ""
    commits_content = safe_read(f"daily/{today}/commits.md") or ""

    drops = []
    for m in _DROP_LINE_RE.finditer(drops_content):
        time, kind, dest = m.groups()
        drops.append({"time": time, "source": kind, "destination": dest})
    drops = drops[-15:]  # latest 15

    commits = []
    current_repo = ""
    for line in commits_content.split("\n"):
        line = line.rstrip()
        if line.startswith("### "):
            current_repo = line[4:].strip()
        else:
            m = _COMMIT_LINE_RE.match(line)
            if m:
                sha, msg = m.groups()
                commits.append({
                    "sha": sha,
                    "message": msg.strip()[:80],
                    "repo": current_repo or "—",
                })
    commits = commits[-15:]
    return {"date": today, "drops": drops, "commits": commits}


# ── meta ──


def _meta() -> dict:
    from .config import load_config
    cfg = load_config()
    now = _now()
    try:
        repo = github_store._repo()
        commits = repo.get_commits()[:1]
        commit_short = commits[0].sha[:7] if commits else "—"
    except Exception:
        commit_short = "—"
    return {
        "version": _MONOGRAM_VERSION,
        "generated_iso": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "generated_relative": "just now",
        "vault": cfg.github_repo,
        "commit_short": commit_short,
    }


# ── render ──


def render_bundle_sync(context: dict | None = None) -> bytes:
    """Synchronous render for tests / quick usage. Uses already-populated context."""
    env = _jinja_env()
    tpl = env.get_template("dashboard.html.j2")
    ctx: dict[str, Any] = {
        "tokens_css": _read_static("tokens.css"),
        "components_css": _read_static("components.css"),
        "main_js": _read_static("main.js"),
    }
    ctx.update(context or {})
    return tpl.render(**ctx).encode("utf-8")


async def render_bundle() -> bytes:
    """Collect data + render. Returns UTF-8 HTML bytes (plaintext).

    Caller typically wraps this in encryption_layer.wrap() before publishing.
    """
    loop = asyncio.get_event_loop()

    # Parallelize the four large blocks — each contains multiple github_store calls
    # already capped by the semaphore.
    board_future = loop.run_in_executor(None, _group_projects)
    life_future = loop.run_in_executor(None, _life_items)
    wiki_future = loop.run_in_executor(None, _wiki_data)
    today_future = loop.run_in_executor(None, _today_data)
    meta_future = loop.run_in_executor(None, _meta)

    board, life, wiki, today, meta = await asyncio.gather(
        board_future, life_future, wiki_future, today_future, meta_future,
    )

    # Critical path derived from board's active+inactive
    critpath = _critpath(None, board["active"])

    context = {
        "meta": meta,
        "board": board,
        "life_items": life,
        "wiki": wiki,
        "today": today,
        "critpath": critpath,
    }
    return render_bundle_sync(context)
