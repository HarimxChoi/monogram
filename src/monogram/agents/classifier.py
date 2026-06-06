"""Stage 2 — Classifier."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from ..llm import complete
from ..taxonomy import (
    CONFIDENCE_LEVELS,
    DROP_TYPES,
    TARGET_KINDS,
    derive_path,
    normalize_life_area,
    normalize_literal,
    slugify,
)
from ..vault_config import load_vault_config
from .orchestrator import PipelinePlan

log = logging.getLogger("monogram.classifier")


@dataclass
class FewShotExample:
    excerpt: str
    target_kind: str
    slug: str


def _load_few_shot_examples(path: str, max_count: int) -> list[FewShotExample]:
    # Defense-in-depth: filter expired entries even if the daily expirer hasn't run.

    from datetime import datetime, timezone
    from .. import github_store

    content = github_store.read(path)
    if not content:
        return []

    now = datetime.now(timezone.utc)
    out: list[FewShotExample] = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue

        expires = d.get("expires_at", "")
        if expires:
            try:
                if datetime.fromisoformat(expires) < now:
                    continue
            except ValueError:
                continue

        excerpt = (d.get("input_excerpt") or "").strip()
        kind = (d.get("target_kind") or "").strip()
        raw_slug = (d.get("slug") or "").strip()
        # Prompt-injection defense: exemplar file is vault-writable; re-validate before the string reaches the prompt.
        if kind not in TARGET_KINDS:
            continue
        slug = slugify(raw_slug)
        if excerpt and slug:
            out.append(FewShotExample(excerpt=excerpt, target_kind=kind, slug=slug))

    # File is written in approval order, so tail ≈ most-recent.
    return out[-max_count:] if max_count > 0 else []


def _build_system_prompt() -> str:
    cfg = load_vault_config()
    life_list = ", ".join(cfg.life_categories) or "(none configured)"
    base = f"""You are the classifier stage of Monogram's pipeline.

Given an inbound payload, route it to exactly one of FIVE destinations.
Choose based on ACTIONABILITY — how the user will use this later — not topic.

1. project — user talks about THEIR OWN deadlined project
   path: projects/{{slug}}.md
   slug = project name, kebab-case (e.g. "paper-a", "project-b")
   Examples:
     "mark paper-a phase 0 done"   → project, slug="paper-a"
     "project-b blocked on API"    → project, slug="project-b"

2. life — ongoing life area item (shopping, career moves, meetings, etc)
   path: life/{{life_area}}.md (appends timestamped entry)
   life_area must be one of: {life_list}
   Examples:
     "need wireless earbuds"       → life, life_area="shopping"
     "met with X about contract"   → life, life_area="meeting-notes"
     "new cafe on 5th ave"         → life, life_area="places"

3. wiki — reusable knowledge, NOT tied to one project
   path: wiki/{{slug}}.md (flat, no subfolders)
   tags: up to 5 topic tags in frontmatter for retrieval
   Examples:
     "RTMPose does 500 FPS"        → wiki, slug="rtmpose", tags=["pose-estimation","inference"]
     "conformal prediction..."     → wiki, slug="conformal-prediction", tags=["calibration","stats"]

4. credential — password, API key, token, sensitive secret
   path: life/credentials/{{slug}}.md
   SLUG MUST BE GENERIC — do not put secret values in slug
   Examples:
     "openai api key sk-..."       → credential, slug="openai-api-key"
     "gmail app password: ..."     → credential, slug="gmail-app-password"
   WARNING: the LLM never reads this path again. Capture correctly or not at all.

5. daily_only — reflections, queries, random thoughts
   NO stable target — lands only in daily/drops.md
   Examples:
     "feeling burned out today"    → daily_only
     "what's due this week?"       → daily_only

HARD CONSTRAINTS:
- slug MUST match [a-z0-9-]+. No spaces, no dates, no uppercase, no underscores.
- life_area MUST be from the allowed list above (or omit for non-life kinds).
- Do NOT invent new categories. If unsure which life_area matches, choose the closest one.
- Do NOT emit raw paths. Emit target_kind + (life_area | slug) only.

Output valid JSON matching the Classification schema.
"""

    if cfg.classifier_few_shot_enabled:
        try:
            examples = _load_few_shot_examples(
                cfg.classifier_few_shot_path,
                max_count=cfg.classifier_few_shot_max_examples,
            )
        except Exception as e:
            log.warning(
                "classifier few-shot load failed, zero-shot fallback: %s", e
            )
            examples = []

        if examples:
            shots = "\nHigh-confidence examples from prior classifications:\n"
            for ex in examples:
                excerpt = ex.excerpt[:120]
                shots += f'  "{excerpt}" → target_kind={ex.target_kind}, slug={ex.slug}\n'
            base += shots

    return base


class Classification(BaseModel):
    drop_type: Literal[
        "task",
        "deadline",
        "technical_link",
        "paper",
        "personal_thought",
        "life_item",
        "credential",
        "query",
        "ambiguous",
    ]
    target_kind: Literal["project", "life", "wiki", "credential", "daily_only"]
    life_area: str | None = None
    slug: str
    confidence: Literal["high", "medium", "low"]
    tags: list[str] = Field(default_factory=list)
    reasoning: str = Field(
        description="One-line rationale (logged, not shown to user)",
    )

    @property
    def target_path(self) -> str:
        return derive_path(self.target_kind, self.slug, self.life_area)


async def run(payload: str, plan: PipelinePlan) -> Classification:
    system = _build_system_prompt()
    prompt = f"Payload:\n{payload}\n\nPlan: {plan.model_dump_json()}"
    for attempt in range(2):
        raw = await complete(
            prompt=prompt,
            system=system,
            response_format=Classification,
            temperature=0.3 if attempt == 0 else 0.7,
            agent_tag="classifier",
        )
        try:
            data = json.loads(raw)
            data["drop_type"] = normalize_literal(
                data.get("drop_type"), DROP_TYPES, "ambiguous"
            )
            data["target_kind"] = normalize_literal(
                data.get("target_kind"), TARGET_KINDS, "daily_only"
            )
            if data["target_kind"] == "life":
                coerced = normalize_life_area(data.get("life_area"))
                data["target_kind"] = "daily_only" if coerced is None else "life"  # unroutable life_area → demote
                data["life_area"] = coerced
            else:
                data["life_area"] = None
            data["confidence"] = normalize_literal(
                data.get("confidence"), CONFIDENCE_LEVELS, "medium"
            )
            data["slug"] = slugify(data.get("slug") or "untitled")
            return Classification.model_validate(data)
        except (json.JSONDecodeError, ValidationError, KeyError, TypeError) as e:
            log.warning("classifier: invalid output (attempt %d/2): %s", attempt + 1, e)
    log.error("classifier: parse failed twice; routing to daily_only")
    return Classification(
        drop_type="ambiguous",
        target_kind="daily_only",
        slug="untitled",
        confidence="low",
        tags=[],
        reasoning="classifier parse failed; safe fallback",
    )
