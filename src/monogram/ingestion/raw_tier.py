"""Raw tier writer — immutable audit trail; raw/ files are NEVER rewritten or deleted."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import ExtractionResult

log = logging.getLogger("monogram.ingestion.raw_tier")


def write_raw(result: "ExtractionResult") -> str | None:
    from .. import github_store

    base_path = result.raw_path()
    content = result.to_raw_markdown()

    path = base_path
    existing = github_store.read(path)
    counter = 2
    while existing is not None and counter <= 9:
        path = base_path.replace(".md", f"-{counter}.md")
        existing = github_store.read(path)
        counter += 1

    if existing is not None:
        log.warning("raw/: 9 collisions for %s, skipping", base_path)
        return None

    ok = github_store.write(
        path,
        content,
        f"raw: {result.source_type} {result.url[:60]}",
    )
    if ok:
        log.info("raw/: wrote %s (%d chars)", path, len(content))
        return path
    log.warning("raw/: write failed for %s", path)
    return None
