"""Stage 5 — Writer (v0.3). See docs/agents.md §5.

Pure Python, no LLM. Produces a FileChange containing ALL paths for
one atomic commit. Dispatches on classification.target_kind:

  project     → projects/<slug>.md OVERWRITE + MEMORY pointer + drops + decisions
  life        → life/<area>.md APPEND (timestamped) + drops + decisions (no MEMORY)
  wiki        → wiki/<slug>.md OVERWRITE + wiki/index.md APPEND + MEMORY + drops + decisions
  credential  → life/credentials/<slug>.md OVERWRITE (minimal) + drops (REDACTED) + decisions (slug redacted). NO MEMORY pointer.
  daily_only  → drops.md + decisions.md only
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .. import github_store
from .classifier import Classification
from .extractor import (
    ConceptDrop,
    CredentialEntry,
    LifeEntry,
    PersonalLog,
    ProjectUpdate,
    QueryIntent,
)
from .verifier import VerifyResult


@dataclass
class FileChange:
    writes: dict[str, str] = field(default_factory=dict)
    commit_message: str = ""
    primary_path: str = ""
    confidence: str = "medium"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _now_hhmm() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _render_project(payload) -> str:
    if isinstance(payload, ProjectUpdate):
        lines = [f"# {payload.project_name}", "", f"- **Progress:** {payload.progress_note}"]
        if payload.status_change:
            lines.append(f"- **Status:** {payload.status_change}")
        if payload.deadline_mentioned:
            lines.append(f"- **Deadline:** {payload.deadline_mentioned}")
        if payload.blocker_mentioned:
            lines.append(f"- **Blocker:** {payload.blocker_mentioned}")
        return "\n".join(lines) + "\n"
    return f"# project\n\n{_render_fallback(payload)}"


def _render_wiki(payload) -> str:
    if isinstance(payload, ConceptDrop):
        lines = [f"# {payload.title}", "", payload.summary]
        if payload.source_url:
            lines.append(f"\nSource: {payload.source_url}")
        if payload.key_claims:
            lines.append("\n## Key claims")
            lines.extend(f"- {c}" for c in payload.key_claims)
        return "\n".join(lines) + "\n"
    return _render_fallback(payload)


def _render_life_entry(payload, slug: str) -> str:
    """Timestamped H3 entry for append to life/<area>.md."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    if isinstance(payload, LifeEntry):
        title = payload.title or slug
        parts = [f"## {ts} — {title}", "", payload.content.rstrip()]
        if payload.context:
            parts.append(f"\n_Context: {payload.context}_")
        return "\n".join(parts) + "\n"
    # Fallback for mis-routed payloads
    text = _render_fallback(payload)
    return f"## {ts} — {slug}\n\n{text}\n"


def _render_credential(payload) -> str:
    """Minimal body for life/credentials/<slug>.md — no frontmatter.
    Credentials are never read again by LLM; no tags needed for retrieval."""
    if isinstance(payload, CredentialEntry):
        return f"# {payload.label}\n\n{payload.body}\n"
    return f"# credential\n\n{_render_fallback(payload)}"


def _render_fallback(payload) -> str:
    if isinstance(payload, PersonalLog):
        body = payload.content.rstrip() + "\n"
        if payload.context:
            body += f"\n_Context: {payload.context}_\n"
        return body
    if isinstance(payload, QueryIntent):
        return f"Query: {payload.question}\nscope: {payload.scope}\n"
    return f"{payload!r}\n"


def _life_file_header(area: str | None) -> str:
    return f"# life/{area or 'misc'}\n\nAppend-only log. Latest at bottom.\n\n"


def _wiki_index_header() -> str:
    return "# Wiki Index\n\nOne-line entry per wiki page.\n\n"


def _wiki_index_line(slug: str, payload, tags: list[str]) -> str:
    """Canonical wiki index line. Parsed by morning_job + wiki_backlinks + lint.

    Format (LOCKED):
        - [[<slug>]] — <summary ≤60 chars> [#t1 #t2 ...] (YYYY-MM-DD)
    """
    summary = ""
    if isinstance(payload, ConceptDrop):
        summary = (payload.title or payload.summary)[:60]
    if not summary:
        summary = slug
    summary = summary.replace("\n", " ").strip()
    tag_str = " ".join(f"#{t}" for t in (tags or [])[:5])
    today = _today()
    return f"- [[{slug}]] — {summary} [{tag_str}] ({today})"


def _append_or_init_wiki_index(existing: str, new_line: str) -> str:
    """Append or replace index line. Dedup by slug (between [[ and ]] in new_line)."""
    if not existing:
        return _wiki_index_header() + new_line + "\n"
    # Extract the slug from the new line to find+replace existing entry
    import re as _re
    m = _re.match(r"- \[\[([a-z0-9-]+)\]\]", new_line)
    slug_marker = f"[[{m.group(1)}]]" if m else None
    lines = existing.split("\n")
    for i, line in enumerate(lines):
        if slug_marker and slug_marker in line:
            lines[i] = new_line
            return "\n".join(lines)
    return existing.rstrip() + "\n" + new_line + "\n"


def _summary_of(payload) -> str:
    if isinstance(payload, ProjectUpdate):
        return payload.progress_note[:60]
    if isinstance(payload, ConceptDrop):
        return payload.title[:60]
    if isinstance(payload, PersonalLog):
        return payload.content[:60]
    if isinstance(payload, QueryIntent):
        return payload.question[:60]
    if isinstance(payload, LifeEntry):
        return payload.title[:60]
    return ""


def _build_drop_entry(payload, classification: Classification) -> str:
    """One drop entry for daily/YYYY-MM-DD/drops.md."""
    time = _now_hhmm()
    if classification.target_kind == "credential":
        # NEVER log the slug or content — both are sensitive
        return f"## {time}\n**credential** → (redacted)\n"
    kind = getattr(payload, "kind", classification.drop_type)
    destination = classification.target_path or "daily_only"
    summary = _summary_of(payload)
    return f"## {time}\n**{kind}** → `{destination}`\n{summary}\n"


def _update_memory_pointer(
    existing_memory: str,
    target_path: str,
    summary: str,
    confidence: str,
) -> str:
    """Update or append a MEMORY.md pointer line."""
    name = target_path.rsplit("/", 1)[-1].replace(".md", "")
    new_line = f"{name:<20s} {target_path:<45s} {summary[:60]:<60s} [{confidence}]"

    lines = existing_memory.split("\n") if existing_memory else []
    updated = False
    for i, line in enumerate(lines):
        if target_path in line:
            lines[i] = new_line
            updated = True
            break
    if not updated:
        lines.append(new_line)

    return "\n".join(lines)


def _build_decision_entry(
    classification: Classification,
    verification: VerifyResult,
    writes: list[str],
) -> str:
    """One decision log entry. Credential slug/path are redacted."""
    now = _now_iso()
    is_cred = classification.target_kind == "credential"
    slug_display = "(redacted)" if is_cred else classification.slug
    writes_display = [
        "life/credentials/(redacted)" if w.startswith("life/credentials/") else w
        for w in writes
    ]
    return (
        f"\n## {now}\n"
        f"Pipeline: {classification.drop_type}\n"
        f"Target: {classification.target_kind} / slug={slug_display}\n"
        f"Path: orchestrator → classifier → extractor → verifier → writer\n"
        f"Confidence: {verification.target_confidence}\n"
        f"Writes: {', '.join(writes_display)}\n"
        f"Reasoning: {verification.reasoning}\n"
    )


def _commit_message(classification: Classification) -> str:
    if classification.target_kind == "credential":
        return "monogram: credential (redacted)"
    if classification.target_path:
        leaf = classification.target_path.rsplit("/", 1)[-1]
        if leaf.endswith(".md"):
            leaf = leaf[:-3]
    else:
        leaf = classification.slug
    return f"monogram: {classification.drop_type} — {leaf[:40]}"


def _build_metadata(confidence: str, tags: list[str]) -> dict[str, Any]:
    now = _now_iso()
    return {
        "confidence": confidence,
        "sources": 1,
        "created": now,
        "last_accessed": now,
        "last_confirmed": now,
        "tags": list(tags),
    }


async def run(
    extraction,
    verification: VerifyResult,
    classification: Classification,
    existing_target: str = "",
    existing_memory: str = "",
    existing_drops: str = "",
    existing_decisions: str = "",
    existing_wiki_index: str = "",
) -> FileChange:
    """Build ALL writes for a single atomic commit. No git side-effect."""
    today = _today()
    writes: dict[str, str] = {}
    target_path = classification.target_path
    target_kind = classification.target_kind

    # ── 1. Stable-state write (kind-dispatched) ──
    if target_kind == "project" and target_path:
        metadata = _build_metadata(verification.target_confidence, classification.tags)
        body = _render_project(extraction)
        writes[target_path] = github_store.serialize_with_metadata(metadata, body)

    elif target_kind == "life" and target_path:
        new_entry = _render_life_entry(extraction, classification.slug)
        if existing_target:
            writes[target_path] = existing_target.rstrip() + "\n\n" + new_entry
        else:
            writes[target_path] = _life_file_header(classification.life_area) + new_entry

    elif target_kind == "wiki" and target_path:
        metadata = _build_metadata(verification.target_confidence, classification.tags)
        body = _render_wiki(extraction)
        writes[target_path] = github_store.serialize_with_metadata(metadata, body)
        # Maintain wiki/index.md
        idx_line = _wiki_index_line(classification.slug, extraction, classification.tags)
        writes["wiki/index.md"] = _append_or_init_wiki_index(existing_wiki_index, idx_line)
        # v0.3b: auto-maintained backlinks for tag-overlap peers (cap 5)
        from ..wiki_backlinks import compute_backlink_writes
        backlink_writes = compute_backlink_writes(
            new_slug=classification.slug,
            new_tags=classification.tags,
            index_content=existing_wiki_index or "",
        )
        writes.update(backlink_writes)

    elif target_kind == "credential" and target_path:
        # Minimal, no YAML frontmatter — never read again by LLM
        writes[target_path] = _render_credential(extraction)

    # ── 2. daily/drops.md — ALWAYS (credential is redacted inside _build_drop_entry) ──
    drops_path = f"daily/{today}/drops.md"
    drop_entry = _build_drop_entry(extraction, classification)
    writes[drops_path] = (
        existing_drops.rstrip() + "\n" + drop_entry if existing_drops else drop_entry
    )

    # ── 3. MEMORY.md — only for project and wiki (not life, not credential, not daily_only) ──
    if target_kind in ("project", "wiki") and target_path:
        writes["MEMORY.md"] = _update_memory_pointer(
            existing_memory,
            target_path,
            _summary_of(extraction),
            verification.target_confidence,
        )

    # ── 4. Decisions log — ALWAYS ──
    all_paths = list(writes.keys())
    decision_entry = _build_decision_entry(classification, verification, all_paths)
    writes["log/decisions.md"] = (
        existing_decisions.rstrip() + "\n" + decision_entry
        if existing_decisions
        else decision_entry
    )

    return FileChange(
        writes=writes,
        commit_message=_commit_message(classification),
        primary_path=target_path or drops_path,
        confidence=verification.target_confidence,
    )
