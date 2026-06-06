"""Stage 3 — Extractor."""
from __future__ import annotations

import json
import logging
from typing import Literal, Type, Union

from pydantic import BaseModel, Field, ValidationError

log = logging.getLogger("monogram.extractor")

from ..llm import complete
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
    kind: Literal["life_entry"] = "life_entry"
    title: str = Field(description="Short title, becomes the H3 in life/<area>.md")
    content: str = Field(description="The full content of the entry")
    context: str | None = Field(default=None, description="Optional context")


class CredentialEntry(BaseModel):
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


async def run(
    payload: str,
    classification: Classification | None = None,
    *,
    model_override: str | None = None,
) -> ExtractedPayload:
    if classification is None:
        return PersonalLog(content=payload)

    schema = _DROP_TYPE_TO_SCHEMA.get(classification.drop_type, PersonalLog)
    prompt = (
        f"Payload:\n{payload}\n\n"
        f"Classification: {classification.model_dump_json()}"
    )

    kwargs: dict = dict(
        prompt=prompt,
        system=EXTRACTOR_SYSTEM_PROMPT,
        response_format=schema,
        agent_tag="extractor",
    )
    if model_override:
        kwargs["model"] = model_override

    for attempt in range(2):
        if attempt == 1:
            kwargs["temperature"] = 0.5  # vary a bad parse that may be deterministic
        raw = await complete(**kwargs)
        try:
            data = json.loads(raw)
            # kind is a discriminator, not LLM-generated — force the schema default so the union validates.
            kind_field = schema.model_fields.get("kind")
            if kind_field is not None and kind_field.default is not None:
                data["kind"] = kind_field.default
            return schema.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as e:
            log.warning("extractor: invalid output (attempt %d/2): %s", attempt + 1, e)
    log.error("extractor: parse failed twice; falling back to PersonalLog")
    return PersonalLog(content=payload)
