"""Weekly lint pass — wiki health check + self-healing.

Runs as part of run_weekly_job(). Produces a LintReport feeding into the
weekly report and commits self-healing writes (regenerated wiki/index.md,
decayed confidence values).

Checks:
  1. Stale confidence decay: high→medium after 30d, medium→low after 90d
  2. Broken wikilinks: [[slug]] in a wiki body where wiki/slug.md doesn't exist
  3. Index regeneration: rebuild wiki/index.md from filesystem (authoritative
     over incremental appends)
  4. Orphan MEMORY.md pointers: pointers to paths that no longer exist
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import github_store
from .safe_read import safe_read

log = logging.getLogger("monogram.wiki_lint")

_STALE_HIGH_DAYS = 30
_STALE_MEDIUM_DAYS = 90
_WIKILINK_RE = re.compile(r"\[\[([a-z0-9-]+)\]\]")


@dataclass
class LintReport:
    orphan_pointers: list[tuple[str, str]] = field(default_factory=list)
    demoted_confidence: list[tuple[str, str, str]] = field(default_factory=list)
    broken_wikilinks: list[tuple[str, str]] = field(default_factory=list)
    orphan_wiki_files: list[str] = field(default_factory=list)
    index_regenerated: bool = False
    writes: dict[str, str] = field(default_factory=dict)

    def summary(self) -> str:
        return (
            f"orphan_pointers={len(self.orphan_pointers)} "
            f"demoted={len(self.demoted_confidence)} "
            f"broken_links={len(self.broken_wikilinks)} "
            f"orphan_files={len(self.orphan_wiki_files)} "
            f"index_regenerated={self.index_regenerated}"
        )


def _list_wiki_files() -> list[str]:
    """List wiki/*.md files (excluding index.md)."""
    try:
        repo = github_store._repo()
        contents = repo.get_contents("wiki")
    except Exception:
        return []
    return [
        f.path
        for f in contents
        if f.type == "file"
        and f.path.endswith(".md")
        and not f.path.endswith("index.md")
    ]


def _days_since(iso_str: str) -> int | None:
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return None


def _check_stale_confidence(wiki_paths: list[str], report: LintReport) -> None:
    """Decay high→medium after 30d, medium→low after 90d."""
    for path in wiki_paths:
        content = safe_read(path)
        if not content:
            continue
        fm, body = github_store.parse_metadata(content)
        if not fm:
            continue
        conf = fm.get("confidence")
        last = fm.get("last_confirmed")
        if not conf or not last:
            continue
        days = _days_since(str(last))
        if days is None:
            continue
        demoted = None
        if conf == "high" and days > _STALE_HIGH_DAYS:
            demoted = "medium"
        elif conf == "medium" and days > _STALE_MEDIUM_DAYS:
            demoted = "low"
        if demoted:
            fm["confidence"] = demoted
            new_content = github_store.serialize_with_metadata(fm, body)
            report.writes[path] = new_content
            report.demoted_confidence.append((path, conf, demoted))


def _check_broken_wikilinks(wiki_paths: list[str], report: LintReport) -> None:
    """Flag [[slug]] references whose target file doesn't exist."""
    existing_slugs = {
        p.rsplit("/", 1)[-1].replace(".md", "") for p in wiki_paths
    }
    for path in wiki_paths:
        content = safe_read(path)
        if not content:
            continue
        for match in _WIKILINK_RE.finditer(content):
            target = match.group(1)
            if target not in existing_slugs:
                report.broken_wikilinks.append((path, target))


def _regenerate_wiki_index(wiki_paths: list[str], report: LintReport) -> None:
    """Rebuild wiki/index.md from filesystem — authoritative."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [
        "# Wiki Index",
        "",
        f"Regenerated {today} by weekly lint.",
        "",
    ]
    for path in sorted(wiki_paths):
        content = safe_read(path)
        if not content:
            continue
        fm, body = github_store.parse_metadata(content)
        slug = path.rsplit("/", 1)[-1].replace(".md", "")
        tags = fm.get("tags") or [] if fm else []
        tag_str = " ".join(f"#{t}" for t in tags[:5]) if tags else ""
        # First non-empty non-heading line as summary
        summary = ""
        for line in (body or "").split("\n"):
            line = line.strip()
            if line and not line.startswith("#"):
                summary = line[:60]
                break
        if not summary:
            summary = slug
        created = str(fm.get("created", ""))[:10] if fm else today
        lines.append(f"- [[{slug}]] — {summary} [{tag_str}] ({created})")

    new_content = "\n".join(lines) + "\n"
    existing = safe_read("wiki/index.md")
    if existing != new_content:
        report.writes["wiki/index.md"] = new_content
        report.index_regenerated = True


def _check_orphan_pointers(wiki_paths: list[str], report: LintReport) -> None:
    """MEMORY.md entries pointing to files that don't exist."""
    memory = safe_read("MEMORY.md")
    if not memory:
        return
    existing_paths = set(wiki_paths)
    line_re = re.compile(r"^(\S+)\s+(\S+\.md)\s+", re.MULTILINE)
    for match in line_re.finditer(memory):
        name, path = match.group(1), match.group(2)
        if path.startswith("wiki/"):
            if path not in existing_paths:
                report.orphan_pointers.append((name, path))
        else:
            # For non-wiki pointers (projects/, life/), read the path to check
            if not safe_read(path):
                report.orphan_pointers.append((name, path))


def run_lint() -> LintReport:
    """Execute all lint checks. Returns report; caller commits writes."""
    report = LintReport()
    wiki_paths = _list_wiki_files()

    _check_stale_confidence(wiki_paths, report)
    _check_broken_wikilinks(wiki_paths, report)
    _regenerate_wiki_index(wiki_paths, report)
    _check_orphan_pointers(wiki_paths, report)

    log.info("wiki_lint: %s", report.summary())
    return report


def format_lint_section(report: LintReport) -> str:
    """Render findings as a markdown section for the weekly report."""
    lines = ["## Health check", ""]

    if report.demoted_confidence:
        lines.append(f"**Confidence decay** ({len(report.demoted_confidence)} demoted)")
        for path, from_c, to_c in report.demoted_confidence[:10]:
            lines.append(f"- {path}: {from_c} → {to_c}")
        lines.append("")

    if report.broken_wikilinks:
        lines.append(f"**Broken wikilinks** ({len(report.broken_wikilinks)})")
        for source, target in report.broken_wikilinks[:10]:
            lines.append(f"- {source} → missing [[{target}]]")
        lines.append("")

    if report.orphan_pointers:
        lines.append(f"**Orphan MEMORY pointers** ({len(report.orphan_pointers)})")
        for name, path in report.orphan_pointers[:10]:
            lines.append(f"- {name} → {path}")
        lines.append("")

    if report.index_regenerated:
        lines.append("**Wiki index regenerated from filesystem.**")
        lines.append("")

    if (
        not report.demoted_confidence
        and not report.broken_wikilinks
        and not report.orphan_pointers
        and not report.index_regenerated
    ):
        lines.append("Clean.")
        lines.append("")

    return "\n".join(lines)
