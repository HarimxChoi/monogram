"""JSONL fixture loader.

Each fixture file under evals/fixtures/*.jsonl is one fixture per line:

    {"id": "proj-001-phase-done", "category": "projects",
     "input": {"text": "mark paper-a phase 0 done"},
     "expected": {"target_kind": "project", "slug": "paper-a",
                  "target_path": "projects/paper-a.md",
                  "should_escalate": false},
     "source_harvest_id": "seed"}

`source_harvest_id` is "seed" for hand-written fixtures, "2026-04-26" etc.
for fixtures harvested from production.
"""
from __future__ import annotations

import json
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load(category: str) -> list[dict]:
    """Load a single category's JSONL. Returns [] if absent."""
    path = FIXTURES_DIR / f"{category}.jsonl"
    if not path.exists():
        return []
    out: list[dict] = []
    for line_no, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        try:
            fixture = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Invalid JSON in {path}:{line_no}: {e}"
            ) from e
        if "id" not in fixture:
            raise ValueError(f"{path}:{line_no} missing 'id' field")
        out.append(fixture)
    return out


def load_all() -> list[dict]:
    """Load every fixture category (plus _accepted.jsonl if present)."""
    out: list[dict] = []
    for path in sorted(FIXTURES_DIR.glob("*.jsonl")):
        if path.name.startswith("harvested-"):
            # Dated audit files — don't auto-load. _accepted.jsonl is the
            # merged canonical set.
            continue
        stem = path.stem
        try:
            out.extend(load(stem))
        except ValueError as e:
            raise ValueError(f"Loading {stem}: {e}") from e
    return out


def find_by_id(fixture_id: str) -> dict | None:
    for f in load_all():
        if f.get("id") == fixture_id:
            return f
    return None
