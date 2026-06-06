"""Conventional Commits parser (pure): type/scope/breaking, #issue refs, co-authors."""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

_HEADER = re.compile(
    r"^(?P<type>[a-zA-Z]+)(?:\((?P<scope>[^)]*)\))?(?P<bang>!)?:\s*(?P<desc>.*)$"
)
_ISSUE = re.compile(r"#(\d+)")
_COAUTHOR = re.compile(r"(?im)^Co-authored-by:\s*(.+?)\s*$")
_BREAKING_FOOTER = re.compile(r"(?m)^BREAKING[ -]CHANGE:")

# callers use this set to distinguish "code work" from chore/docs (any word is valid as type)
KNOWN_TYPES = (
    "feat", "fix", "docs", "style", "refactor", "perf",
    "test", "build", "ci", "chore", "revert",
)


@dataclass
class ParsedCommit:
    type: str | None
    scope: str | None
    breaking: bool
    description: str
    issues: list[str] = field(default_factory=list)      # ["#12", ...]
    co_authors: list[str] = field(default_factory=list)   # ["Name <email>", ...]

    @property
    def is_conventional(self) -> bool:
        return self.type is not None


def _dedup(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out


def parse_commit(message: str) -> ParsedCommit:
    """Parse a commit message into structured fields. Never raises."""
    message = message or ""
    lines = message.splitlines()
    header = lines[0].strip() if lines else ""

    breaking = bool(_BREAKING_FOOTER.search(message))
    issues = [f"#{n}" for n in _dedup(_ISSUE.findall(message))]
    co_authors = _dedup(c.strip() for c in _COAUTHOR.findall(message))

    m = _HEADER.match(header)
    if not m:
        return ParsedCommit(None, None, breaking, header, issues, co_authors)
    return ParsedCommit(
        type=m.group("type").lower(),
        scope=(m.group("scope") or None),
        breaking=breaking or bool(m.group("bang")),
        description=m.group("desc").strip(),
        issues=issues,
        co_authors=co_authors,
    )
