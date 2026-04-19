"""v0.4b: MCP write-tool implementations.

Write-type MCP tools enqueue via mcp_pending and are executed by the
bot's /approve_<token> handler. This module holds the kind-specific
execution logic the handler dispatches to.
"""
from __future__ import annotations

import json
import re

from . import github_store
from .agents.writer import (
    _build_metadata,
    _wiki_index_header,
    _wiki_index_line,
)
from .safe_read import safe_read


_SLUG_RE = re.compile(r"^[a-z0-9-]+$")


async def add_wiki_entry_pending(
    slug: str, title: str, body: str, tags: list[str] | None = None
) -> str:
    """Enqueue an add-wiki-entry write + push Telegram approval prompt.

    Called directly by the MCP tool handler. Validates input; returns
    a token message for the MCP caller.
    """
    from .bot_notify import push_to_telegram
    from .mcp_pending import new_pending

    slug = (slug or "").strip().lower()
    title = (title or "").strip()
    body = body or ""
    tags = [t.strip().lower() for t in (tags or []) if t.strip()]

    if not slug or not _SLUG_RE.match(slug):
        return "Error: slug must match [a-z0-9-]+ and be non-empty."
    if not title:
        return "Error: title is required."
    if not body.strip():
        return "Error: body is required (non-empty)."

    payload = {"slug": slug, "title": title, "body": body, "tags": tags}
    preview = (
        f"slug: {slug}\n"
        f"title: {title}\n"
        f"tags: {', '.join(tags) if tags else '(none)'}\n"
        f"body: {body[:200]}{'…' if len(body) > 200 else ''}"
    )
    entry = new_pending("add_wiki_entry", payload, preview)
    await push_to_telegram(
        f"📝 MCP client wants to add a wiki entry:\n\n"
        f"{preview}\n\n"
        f"/approve_{entry.token} or /deny_{entry.token} (expires 5 min)"
    )
    return f"Pending approval — check Telegram. Token: {entry.token}"


async def commit_wiki_entry(payload: dict) -> tuple[bool, str]:
    """Actually write the wiki entry to the repo. Called by the bot's
    /approve_<token> handler. Returns (ok, summary).
    """
    slug = payload["slug"]
    title = payload["title"]
    body = payload["body"]
    tags = payload.get("tags", [])

    path = f"wiki/{slug}.md"
    # Don't silently overwrite if the entry already exists — surface and abort
    existing = safe_read(path)
    if existing:
        return False, f"wiki/{slug}.md already exists — not overwriting"

    metadata = _build_metadata(confidence="medium", tags=tags)
    rendered_body = f"# {title}\n\n{body.rstrip()}\n"
    full = github_store.serialize_with_metadata(metadata, rendered_body)
    ok = github_store.write(path, full, f"monogram: wiki/{slug}.md via MCP")
    if not ok:
        return False, f"write failed: {path}"

    # Update wiki/index.md incrementally
    existing_index = safe_read("wiki/index.md")
    # Use the same line format as Writer for consistency with backlinks/lint
    # We don't have a ConceptDrop here — inline the expected format:
    tag_str = " ".join(f"#{t}" for t in tags[:5]) if tags else ""
    from .agents.writer import _today
    idx_line = f"- [[{slug}]] — {title[:60]} [{tag_str}] ({_today()})"
    if existing_index:
        index_content = existing_index.rstrip() + "\n" + idx_line + "\n"
    else:
        index_content = _wiki_index_header() + idx_line + "\n"
    github_store.write(
        "wiki/index.md", index_content,
        f"monogram: wiki/index.md — +[[{slug}]]"
    )

    return True, f"wrote {path} + updated wiki/index.md"
