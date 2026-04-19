"""Raw tier writer — immutable audit trail of every URL ingestion.

Writes to `raw/YYYY-MM-DD-<source>-<slug>.md` in the vault. This file
is NEVER rewritten or deleted by the pipeline; it's the source of truth
if anonymizer logic changes and we need to re-derive anything.

Design notes:
  - raw/ is read-only after write; any code that modifies files in raw/
    is a bug
  - If a write would collide (same slug same day), we append a counter
    rather than overwriting
  - The pipeline never reads from raw/ directly; it uses the already-
    enriched drop text. raw/ is audit-only.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import ExtractionResult

log = logging.getLogger("monogram.ingestion.raw_tier")


def write_raw(result: "ExtractionResult") -> str | None:
    """Write an ExtractionResult to the raw/ tier. Returns the path
    written, or None on failure.

    Collision resolution: if `raw/<name>.md` exists, try `-2`, `-3`,
    up to `-9` before giving up.
    """
    from .. import github_store

    base_path = result.raw_path()
    content = result.to_raw_markdown()

    # Collision check — append counter if path exists
    path = base_path
    existing = github_store.read(path)
    counter = 2
    while existing is not None and counter <= 9:
        # Insert counter before .md
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
