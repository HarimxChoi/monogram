"""Stage 3 — Extractor. See docs/agents.md §3.

Real implementation (Phase D). Calls Flash-Lite with per-drop-type schema
selection, returns the appropriate ExtractedPayload variant.

v0.7 (D1-A): passes agent_tag="extractor" so eval cassette routes calls
to evals/cassettes/extractor.json.
"""
from __future__ import annotations

from typing import Literal, Type, Union

from pydantic import BaseModel, Field

import json

from ..llm import complete
from ..models import get_model
from .classifier import Classification

EXTRACTOR_SYSTEM_PROMPT = """\
You are the extractor stage of Monogram's pipeline.

Given an inbound payload and its classification, extract the structured
fields matching the target schema for that drop_type.

Rules:
- Do not invent content not present in the input
- If a field is not mentioned, leave it null (do not guess)
- Copy user's phrasing for progress_note and content fields;
  summarize only when the raw text is too long (>500 chars)
- For URLs, copy exactly; do not shorten or canonicalize
- For deadlines, parse into ISO date only if unambiguous; else leave null

Output valid JSON matching the appropriate schema variant.
"""


class ProjectUpdate(BaseModel):
    kind: Literal["project_update"] = "project_update"
    project_name: str
    status_change: str | None = None
    progress_note: str
    deadline_mentioned: str | None = None
    blocker_mentioned: str | None = None


class ConceptDrop(BaseModel):
    kind: Literal["concept_drop"] = "concept_drop"
    title: str
    summary: str
    source_url: str | None = None
    key_claims: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class PersonalLog(BaseModel):
    kind: Literal["personal_log"] = "personal_log"
    content: str
    context: str | None = None


class QueryIntent(BaseModel):
    kind: Literal["query_intent"] = "query_intent"
    question: str
    scope: Literal["scheduler", "wiki", "both"]
    time_range: Literal["today", "week", "month", "all"] = "all"


class LifeEntry(BaseModel):
    """A single life-area entry — shopping item, meeting note, place, etc."""
    kind: Literal["life_entry"] = "life_entry"
    title: str = Field(description="Short title, becomes the H3 in life/<area>.md")
    content: str = Field(description="The full content of the entry")
    context: str | None = Field(default=None, description="Optional context")


class CredentialEntry(BaseModel):
    """A credential capture — stored but never echoed back in logs/briefs."""
    kind: Literal["credential_entry"] = "credential_entry"
    label: str = Field(description="Human label — NOT the secret value itself")
    body: str = Field(description="The credential content, as-is")


ExtractedPayload = Union[
    ProjectUpdate,
    ConceptDrop,
    PersonalLog,
    QueryIntent,
    LifeEntry,
    CredentialEntry,
]

_DROP_TYPE_TO_SCHEMA: dict[str, Type[BaseModel]] = {
    "task": ProjectUpdate,
    "deadline": ProjectUpdate,
    "technical_link": ConceptDrop,
    "paper": ConceptDrop,
    "personal_thought": PersonalLog,
    "life_item": LifeEntry,
    "credential": CredentialEntry,
    "query": QueryIntent,
    "ambiguous": PersonalLog,
}

# target_kind overrides drop_type for schema routing. Classifier can emit
# (drop_type="task", target_kind="life") if the user's language is ambiguous;
# without this map the extractor would ask the LLM for ProjectUpdate fields
# on a shopping item. Keying on target_kind guarantees the schema matches
# the writer's dispatch in writer.run.
_TARGET_KIND_TO_SCHEMA: dict[str, Type[BaseModel]] = {
    "life": LifeEntry,
    "credential": CredentialEntry,
}


async def run(
    payload: str,
    classification: Classification | None = None,
    *,
    model_override: str | None = None,
) -> ExtractedPayload:
    """Extract structured fields based on classification.drop_type."""
    if classification is None:
        return PersonalLog(content=payload)

    schema = (
        _TARGET_KIND_TO_SCHEMA.get(classification.target_kind)
        or _DROP_TYPE_TO_SCHEMA.get(classification.drop_type, PersonalLog)
    )
    prompt = (
        f"Payload:\n{payload}\n\n"
        f"Classification: {classification.model_dump_json()}"
    )

    raw = await complete(
        prompt=prompt,
        system=EXTRACTOR_SYSTEM_PROMPT,
        response_format=schema,
        model=model_override or get_model("low"),
        agent_tag="extractor",
    )
    data = json.loads(raw)
    # LLM may return wrong kind value (e.g. "task" instead of "project_update");
    # override with the schema's default since kind is a discriminator, not LLM-generated.
    kind_field = schema.model_fields.get("kind")
    if kind_field is not None and kind_field.default is not None:
        data["kind"] = kind_field.default
    return schema.model_validate(data)
