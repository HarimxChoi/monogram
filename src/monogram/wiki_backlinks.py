"""Wiki backlinks — tag-overlap peer discovery.

When a new wiki entry is written, find up to 5 existing entries whose tags
overlap with the new one's tags, and add `[[new_slug]]` as a backlink to
their "## Related" section. The cap prevents fan-out explosion for common
tags — if a vault has 50 entries tagged [ml-cv] and we add another, we
only touch the top-5 by overlap count.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .safe_read import safe_read


_INDEX_LINE_RE = re.compile(
    r"^- \[\[([a-z0-9-]+)\]\] — (.+?) \[(.*?)\]",
    re.MULTILINE,
)

_MAX_BACKLINKS_PER_DROP = 5
_RELATED_MARKER = "## Related"


@dataclass
class IndexEntry:
    slug: str
    tags: list[str]


def _parse_index(index_content: str) -> list[IndexEntry]:
    """Parse wiki/index.md lines into [(slug, tags), ...]."""
    entries = []
    for match in _INDEX_LINE_RE.finditer(index_content):
        slug, _summary, tags_str = match.groups()
        tags = [t.lstrip("#") for t in tags_str.split() if t]
        entries.append(IndexEntry(slug=slug, tags=tags))
    return entries


def find_peers(
    new_slug: str,
    new_tags: list[str],
    index_content: str,
) -> list[str]:
    """Return up to 5 existing slugs with overlapping tags, sorted by
    (overlap_count desc, slug asc) for stability."""
    if not new_tags:
        return []
    new_set = set(new_tags)
    scored: list[tuple[int, str]] = []
    for entry in _parse_index(index_content):
        if entry.slug == new_slug:
            continue
        overlap = len(new_set & set(entry.tags))
        if overlap > 0:
            scored.append((overlap, entry.slug))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [slug for _, slug in scored[:_MAX_BACKLINKS_PER_DROP]]


def append_backlink(existing_content: str, new_slug: str) -> str:
    """Append `- [[new_slug]]` to the Related section. Idempotent: returns
    unchanged if [[new_slug]] already appears anywhere in the file.
    """
    link_line = f"- [[{new_slug}]]"
    if f"[[{new_slug}]]" in existing_content:
        return existing_content

    if _RELATED_MARKER in existing_content:
        lines = existing_content.split("\n")
        marker_idx = next(
            i for i, line in enumerate(lines) if line.strip() == _RELATED_MARKER
        )
        # Find end of this H2 section (next ## or EOF)
        insert_idx = len(lines)
        for i in range(marker_idx + 1, len(lines)):
            if lines[i].startswith("## ") and not lines[i].startswith("## Related"):
                insert_idx = i
                break
        lines.insert(insert_idx, link_line)
        return "\n".join(lines)

    separator = "\n\n" if not existing_content.endswith("\n\n") else ""
    return (
        existing_content.rstrip()
        + separator
        + f"\n\n{_RELATED_MARKER}\n{link_line}\n"
    )


def compute_backlink_writes(
    new_slug: str,
    new_tags: list[str],
    index_content: str,
) -> dict[str, str]:
    """Return {wiki/<peer>.md: new_content} for up to 5 peers gaining a
    backlink. Caller merges into Writer's writes dict before commit.
    """
    peer_slugs = find_peers(new_slug, new_tags, index_content)
    writes: dict[str, str] = {}
    for peer in peer_slugs:
        path = f"wiki/{peer}.md"
        existing = safe_read(path)
        if not existing:
            continue
        updated = append_backlink(existing, new_slug)
        if updated != existing:
            writes[path] = updated
    return writes
