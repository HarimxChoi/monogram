"""Path derivation and taxonomy normalization."""
from __future__ import annotations

import re
from typing import Iterable, Literal

from .vault_config import load_vault_config

TARGET_KINDS = ("project", "life", "wiki", "credential", "daily_only")
TargetKind = Literal["project", "life", "wiki", "credential", "daily_only"]

DROP_TYPES = (
    "task",
    "deadline",
    "technical_link",
    "paper",
    "personal_thought",
    "life_item",
    "credential",
    "query",
    "ambiguous",
)

CONFIDENCE_LEVELS = ("high", "medium", "low")


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s or "untitled")[:50]


def normalize_literal(value, allowed: Iterable[str], default: str) -> str:
    # Defends against Gemini's Literal drift (e.g. 'Project_Update', 'HIGH', 'tech-papers').
    allowed_tuple = tuple(allowed)
    if value in allowed_tuple:
        return value
    if value is None:
        return default
    lowered = str(value).lower().strip().replace(" ", "_").replace("-", "_")
    if lowered in allowed_tuple:
        return lowered
    kebab = str(value).lower().strip().replace(" ", "-").replace("_", "-")
    if kebab in allowed_tuple:
        return kebab
    for a in allowed_tuple:
        if lowered.startswith(a) or a.startswith(lowered):
            return a
    return default


def normalize_life_area(
    value: str | None, default: str | None = None
) -> str | None:
    if not value:
        return default
    cfg = load_vault_config()
    allowed = cfg.life_categories
    if value in allowed:
        return value
    coerced = slugify(value)
    if coerced in allowed:
        return coerced
    for a in allowed:
        if coerced.startswith(a) or a.startswith(coerced):
            return a
    return default


def derive_path(
    target_kind: str,
    slug: str,
    life_area: str | None = None,
) -> str:
    slug = slugify(slug)
    if target_kind == "project":
        return f"projects/{slug}.md"
    if target_kind == "life":
        area = normalize_life_area(life_area)
        if not area:
            return ""
        return f"life/{slugify(area)}.md"
    if target_kind == "wiki":
        return f"wiki/{slug}.md"
    if target_kind == "credential":
        return f"life/credentials/{slug}.md"
    return ""
