"""Stage 4 — Verifier."""
from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, Field

from ..llm import extract as llm_extract
from .classifier import Classification
from .extractor import ExtractedPayload

log = logging.getLogger("monogram.verifier")

VERIFIER_SYSTEM_PROMPT = """\
You are the verifier stage of Monogram's pipeline — the reliability gate.

Given an extracted payload, its classification, and the EXISTING content
of the target file (if any) plus relevant MEMORY.md pointers, check for:

1. Contradictions with existing facts
   - minor: different phrasing of same fact, no action needed
   - material: partial conflict, needs user awareness
   - direct: new fact replaces old

2. Confidence appropriateness
   - Does the source support the claimed confidence level?
   - Single unverified source maxes at "medium"
   - Third-party link without cross-check maxes at "low"

3. Escalation signal
   - Set escalate=true if: contradiction is ambiguous,
     confidence is unclear, or payload is structurally strange

You do NOT write. You gate. Downstream Writer stage acts on your verdict.
If ok=false, the pipeline will either escalate or ask the user.

Output valid JSON matching VerifyResult schema.
"""


class Contradiction(BaseModel):
    existing_path: str
    existing_claim: str
    new_claim: str
    severity: Literal["minor", "material", "direct"]


class VerifyResult(BaseModel):
    ok: bool
    contradictions: list[Contradiction] = Field(default_factory=list)
    target_confidence: Literal["high", "medium", "low"]
    escalate: bool = Field(
        default=False,
        description="True if downstream should re-run with Flash",
    )
    reasoning: str


async def run(
    extraction: ExtractedPayload | None,
    classification: Classification | None,
    *,
    target_content: str = "",
    memory_content: str = "",
) -> VerifyResult:
    if extraction is None or classification is None:
        return VerifyResult(
            ok=True,
            target_confidence="medium",
            escalate=False,
            reasoning="no extraction or classification provided",
        )

    context_parts = [
        f"Extracted payload:\n{extraction.model_dump_json()}",
        f"\nClassification:\n{classification.model_dump_json()}",
    ]
    if target_content:
        context_parts.append(
            f"\nExisting target file ({classification.target_path}):\n"
            f"{target_content[:2000]}"
        )
    else:
        context_parts.append("\nTarget file does not exist yet (new entry).")

    if memory_content:
        context_parts.append(f"\nMEMORY.md (relevant pointers):\n{memory_content[:2000]}")

    try:
        return await llm_extract(
            prompt="\n".join(context_parts),
            schema=VerifyResult,
            system=VERIFIER_SYSTEM_PROMPT,
            agent_tag="verifier",
        )
    except Exception as e:
        # Fail-open: verifier parse failure must not block the drop.
        log.warning("verifier: parse failed (%s); passing through at medium", e)
        return VerifyResult(
            ok=True,
            target_confidence="medium",
            escalate=False,
            reasoning="verifier parse failed; permissive fallback",
        )
