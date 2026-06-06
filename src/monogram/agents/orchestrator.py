"""Stage 1 — Orchestrator."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..llm import extract

ORCHESTRATOR_SYSTEM_PROMPT = """\
You are the orchestrator stage of Monogram's pipeline.

Given an inbound payload, decide which pipeline to run.

Operations:
- ingest_drop: new source arriving via Telegram Saved Messages
- answer_query: user asking a question in bot chat
- update_project: explicit status update to a tracked project
- log_event: passive event (GitHub push, cron tick)

Preload at most 3 files from MEMORY.md that downstream stages will need.
Do not preload speculative matches — only files clearly referenced or
clearly relevant based on MEMORY.md pointer metadata.

Output valid JSON matching the PipelinePlan schema. No prose.
"""


class PipelinePlan(BaseModel):
    operation: Literal["ingest_drop", "answer_query", "update_project", "log_event"]
    preload_files: list[str] = Field(
        default_factory=list,
        description="Paths from MEMORY.md to load into downstream stages",
    )
    skip_stages: list[str] = Field(
        default_factory=list,
        description="Stages to skip, e.g. 'extractor' for pure queries",
    )
    notes: str = Field(
        default="",
        description="One-line rationale for the plan",
    )


async def run(payload: str) -> PipelinePlan:
    return await extract(
        prompt=payload,
        schema=PipelinePlan,
        system=ORCHESTRATOR_SYSTEM_PROMPT,
        agent_tag="orchestrator",
    )
