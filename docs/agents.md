# Monogram — Agents

> The five pipeline stages: their prompts, schemas, and escalation rules.
> Complements `docs/architecture.md` (pipeline overview) with the
> concrete Pydantic schemas and system prompts each stage uses.

---

## 0. Convention

Each stage is a Python module under `src/monogram/agents/`.
Each module exports:

- A Pydantic schema for output (typed, validated)
- A system prompt (versioned, stored as module constant)
- An async function `run(input, context) -> OutputSchema`

All stages call into `monogram.llm` which handles model selection, thinking
mode, usage logging, and escalation routing.

---

## 1. Orchestrator

**File:** `src/monogram/agents/orchestrator.py`
**Model:** low tier (Gemini default config: Flash-Lite, thinking off)
**Input:** raw inbound payload (drop text or bot message)
**Output:** `PipelinePlan`

### Schema

```python
from pydantic import BaseModel, Field
from typing import Literal

class PipelinePlan(BaseModel):
    operation: Literal["ingest_drop", "answer_query", "update_project", "log_event"]
    preload_files: list[str] = Field(
        default_factory=list,
        description="Paths from MEMORY.md to load into downstream stages"
    )
    skip_stages: list[str] = Field(
        default_factory=list,
        description="Stages to skip, e.g. 'extractor' for pure queries"
    )
    notes: str = Field(
        default="",
        description="One-line rationale for the plan"
    )
```

### System Prompt

```
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
```

### Escalation

None. Orchestrator is deterministic routing — if it fails, the whole
pipeline falls back to default ingest_drop operation.

---

## 2. Classifier

**File:** `src/monogram/agents/classifier.py`
**Model:** low tier (Gemini default: Flash-Lite, thinking AUTO — on if last stage confidence == low)
**Input:** inbound payload + PipelinePlan from orchestrator
**Output:** `Classification`

### Schema

```python
from pydantic import BaseModel, Field
from typing import Literal

class Classification(BaseModel):
    drop_type: Literal[
        "task", "deadline", "technical_link", "paper",
        "personal_thought", "life_item", "credential",
        "query", "ambiguous",
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
```

`target_path` is derived, not emitted. The classifier emits
`target_kind` + `slug` (+ `life_area` for life kinds); `derive_path`
in `monogram.taxonomy` maps them to the canonical path, e.g.
`projects/paper-a.md`, `wiki/<slug>.md`, `life/<area>.md`,
`life/credentials/<slug>.md`, or `""` for `daily_only`.

### System Prompt

```
You are the classifier stage of Monogram's pipeline.

Given an inbound payload, route it to exactly one of FIVE destinations.
Choose based on ACTIONABILITY — how the user will use this later — not topic.

1. project — user talks about THEIR OWN deadlined project
   path: projects/{slug}.md
2. life — ongoing life area item (shopping, meetings, places, etc.)
   path: life/{life_area}.md (appends timestamped entry)
3. wiki — reusable knowledge, NOT tied to one project
   path: wiki/{slug}.md (flat, no subfolders)
4. credential — password, API key, token
   path: life/credentials/{slug}.md (slug MUST be generic)
5. daily_only — reflections, queries, random thoughts
   NO stable target — lands only in daily/drops.md

HARD CONSTRAINTS:
- slug MUST match [a-z0-9-]+. No spaces, dates, uppercase, or underscores.
- life_area MUST be from VaultConfig.life_categories (or omit for non-life).
- Do NOT emit raw paths. Emit target_kind + (life_area | slug) only.

Output valid JSON matching the Classification schema.
```

The full prompt (with examples per kind) lives in
`classifier._build_system_prompt()`. `life_categories` is injected from
`VaultConfig` at call time — users edit categories in `mono/config.md`
without a code change.

### Escalation triggers

- `confidence == "low"` → downstream stages run with thinking ON
- `drop_type == "ambiguous"` → verifier is likely to set `escalate=true`

---

## 3. Extractor

**File:** `src/monogram/agents/extractor.py`
**Model:** low tier (Gemini default: Flash-Lite, thinking AUTO)
**Input:** inbound payload + Classification + preloaded target file (if exists)
**Output:** `ExtractedPayload` (schema depends on drop_type)

### Schema

```python
from pydantic import BaseModel, Field
from typing import Literal, Union

class ProjectUpdate(BaseModel):
    kind: Literal["project_update"] = "project_update"
    project_name: str
    status_change: str | None = None
    progress_note: str
    deadline_mentioned: str | None = None  # ISO date
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
    title: str
    content: str
    context: str | None = None

class CredentialEntry(BaseModel):
    kind: Literal["credential_entry"] = "credential_entry"
    label: str   # human label — NOT the secret value itself
    body: str    # credential content, as-is

ExtractedPayload = Union[
    ProjectUpdate, ConceptDrop, PersonalLog, QueryIntent,
    LifeEntry, CredentialEntry,
]
```

Schema variant is chosen from `classification.drop_type` via
`_DROP_TYPE_TO_SCHEMA` in `extractor.py`:

```
task, deadline          → ProjectUpdate
technical_link, paper   → ConceptDrop
personal_thought        → PersonalLog
life_item               → LifeEntry
credential              → CredentialEntry
query                   → QueryIntent
ambiguous               → PersonalLog
```

`QueryIntent.scope` currently uses `Literal["scheduler", "wiki", "both"]`;
"scheduler" is retained for back-compat with pre-v0.3 cassettes.

### System Prompt

```
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
```

### Escalation triggers

- Pydantic validation fails twice → escalate to Flash
- Required field null when source clearly contains it → verifier catches this

---

## 4. Verifier

**File:** `src/monogram/agents/verifier.py`
**Model:** low tier (Gemini default: Flash-Lite, thinking ON — this stage is the reliability gate). Escalates to `get_model("mid")` on low confidence.
**Input:** ExtractedPayload + Classification + MEMORY.md pointer matches
**Output:** `VerifyResult`

### Schema

```python
from pydantic import BaseModel, Field
from typing import Literal

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
```

### System Prompt

```
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
```

### Escalation triggers

- `escalate == true` → caller re-runs extractor + verifier once on the mid tier
- `ok == false` after escalation budget is spent → ask user
- `target_confidence != extractor_confidence` → Writer uses verifier's value

---

## 5. Writer

**File:** `src/monogram/agents/writer.py`
**Model:** none — deterministic Python
**Input:** ExtractedPayload + VerifyResult + target file state
**Output:** commit SHA (single git commit covering all writes)

### Behavior

Writer dispatches on `classification.target_kind` and produces a
`FileChange` with every path for one atomic commit. No git side-effect
happens here — `github_store.write_multi()` in the caller performs the
commit.

```python
# pseudocode — real code in src/monogram/agents/writer.py

def run(extraction, verification, classification, *, existing_*):
    today = utcnow().strftime("%Y-%m-%d")
    writes: dict[str, str] = {}
    target_path = classification.target_path   # derived from target_kind + slug
    target_kind = classification.target_kind

    # ── 1. Stable-state write (kind-dispatched) ──
    if target_kind == "project" and target_path:
        # projects/<slug>.md — OVERWRITE with YAML metadata + rendered body
        writes[target_path] = serialize_with_metadata(
            metadata(verification.target_confidence, classification.tags),
            render_project(extraction),
        )

    elif target_kind == "life" and target_path:
        # life/<area>.md — APPEND timestamped H3 entry
        writes[target_path] = existing_target + render_life_entry(extraction)

    elif target_kind == "wiki" and target_path:
        # wiki/<slug>.md — OVERWRITE with metadata + body
        writes[target_path] = serialize_with_metadata(
            metadata(verification.target_confidence, classification.tags),
            render_wiki(extraction),
        )
        # wiki/index.md — maintain canonical one-line index
        writes["wiki/index.md"] = append_or_replace_index_line(...)
        # v0.3b: auto-maintained backlinks for tag-overlap peers (cap 5)
        writes.update(compute_backlink_writes(...))

    elif target_kind == "credential" and target_path:
        # life/credentials/<slug>.md — minimal body, NO frontmatter
        # (LLM never reads this path again)
        writes[target_path] = render_credential(extraction)

    # target_kind == "daily_only" has no stable-state write.

    # ── 2. daily/drops.md — ALWAYS (credential entry is REDACTED) ──
    writes[f"daily/{today}/drops.md"] = (
        existing_drops + build_drop_entry(extraction, classification)
    )

    # ── 3. MEMORY.md — ONLY for project and wiki ──
    # Not for life, not for credential (LLM-skip), not for daily_only.
    if target_kind in ("project", "wiki") and target_path:
        writes["MEMORY.md"] = update_memory_pointer(
            existing_memory, target_path,
            summary_of(extraction), verification.target_confidence,
        )

    # ── 4. log/decisions.md — ALWAYS (credential slug/path redacted) ──
    writes["log/decisions.md"] = (
        existing_decisions
        + build_decision_entry(classification, verification, list(writes))
    )

    return FileChange(
        writes=writes,
        commit_message=commit_message(classification),
        primary_path=target_path or f"daily/{today}/drops.md",
        confidence=verification.target_confidence,
    )
```

Per-kind path summary:

```
project     → projects/<slug>.md              OVERWRITE + MEMORY + drops + decisions
life        → life/<area>.md                  APPEND     +        drops + decisions
wiki        → wiki/<slug>.md + wiki/index.md  OVERWRITE + MEMORY + drops + decisions (+ backlinks)
credential  → life/credentials/<slug>.md      OVERWRITE +        drops(REDACTED) + decisions(redacted)
daily_only  → (no stable-state write)         drops + decisions only
```

### Atomic commit rule

All writes in `FileChange.writes` must land in **one git commit**, or
nothing lands. `github_store.write_multi()` is the gate. It uses the
GitHub API `POST /repos/{owner}/{repo}/git/trees` + `git/commits` to
create a tree with all changes, then fast-forwards main. Partial staging
on network failure → client-side rollback (no trees persist until the
commit is pushed).

### What Writer does NOT do

- No LLM calls. Writer is deterministic Python.
- No classification decisions. Writer reads `target_kind` / `slug`.
- No verification decisions. Writer reads `target_confidence` / `ok` / `escalate`.
- No MEMORY pointer for `life` or `credential`. Credentials are LLM-skip.

### Drops carry no confidence

Drops are events, not claims. The entry in `daily/*/drops.md` records
that something happened and how it was classified; it carries no
`confidence:` field of its own. Only the stable-state write (project,
wiki, etc.) carries confidence metadata.

Example drops.md entry format:

```markdown
## 14:32
**paper** → `wiki/zep-temporal-graph.md`
Zep: Temporal Knowledge Graph for Agent Memory
```

Credential drops are redacted:

```markdown
## 14:32
**credential** → (redacted)
```

---

## 6. Escalation Flow

When the verifier sets `escalate=true`, the caller (pipeline runner) does:

```python
# pseudocode — real code in src/monogram/pipeline.py

async def run_pipeline(payload):
    plan = await orchestrator.run(payload)
    classification = await classifier.run(payload, plan)

    # Verifier needs real context to check contradictions.
    target_content = safe_read(classification.target_path)
    memory_content = safe_read("MEMORY.md")

    extraction = await extractor.run(payload, classification)
    verification = await verifier.run(
        extraction, classification,
        target_content=target_content,
        memory_content=memory_content,
    )

    if verification.escalate:
        # Re-run extractor + verifier once on the mid tier (reasoning).
        extraction = await extractor.run(
            payload, classification, model_override=get_model("mid"),
        )
        verification = await verifier.run(
            extraction, classification,
            target_content=target_content,
            memory_content=memory_content,
        )
        if verification.escalate:
            # Two escalations = ask the user.
            return PipelineResult(blocked_reason="two escalations — ask the user")

    if not verification.ok:
        return PipelineResult(blocked_reason=verification.reasoning)

    file_change = await writer.run(
        extraction, verification, classification,
        existing_target=target_content,
        existing_memory=memory_content,
        existing_drops=safe_read(f"daily/{today}/drops.md"),
        existing_decisions=safe_read("log/decisions.md"),
        existing_wiki_index=safe_read("wiki/index.md") if classification.target_kind == "wiki" else "",
    )
    return PipelineResult(file_change=file_change)
```

Escalation is bounded — at most one re-run. No infinite loops. The
actual commit happens in the listener/bot via
`github_store.write_multi(file_change)`, not inside the pipeline.

---

## 7. Thinking Mode Resolution

The low-tier model's `thinking` parameter (on Gemini, Flash-Lite) is set per-stage based on rules:

```python
# src/monogram/llm.py

def resolve_thinking(stage: str, prev_confidence: str | None) -> bool:
    # verifier always thinks
    if stage == "verifier":
        return True
    
    # if upstream was uncertain, turn on
    if prev_confidence == "low":
        return True
    
    # orchestrator is simple routing
    if stage == "orchestrator":
        return False
    
    # default: off (speed + cost advantage)
    return False
```

Result: on a clean pipeline (all stages high confidence), thinking is ON
only for the verifier. On an uncertain pipeline, thinking cascades forward
to every stage after the first low-confidence signal.

---

## 8. Token Budget per Stage

Approximate cost (Gemini Flash-Lite as the low tier, typical drops):

```
STAGE          INPUT       OUTPUT     THINKING COST     TOTAL
────────────────────────────────────────────────────────────────
orchestrator   ~3200       ~150       0 (off)           ~3350
classifier     ~3500       ~200       0 (off, usually)  ~3700
extractor      ~4000       ~300       0 (off, usually)  ~4300
verifier       ~3500       ~250       ~400 (on always)  ~4150
writer         0           0          0                 0 (no LLM)
────────────────────────────────────────────────────────────────
total per drop ~14200 input, ~900 output, ~400 thinking tokens
```

On 250k TPM cap, this is 6% per drop. At 10 drops/minute maximum burst,
still 60% utilization. Comfortable.

---

## 9. Testing Each Agent

One test file per agent, plus an end-to-end pipeline test:

```
tests/agents/test_orchestrator.py
tests/agents/test_classifier.py
tests/agents/test_extractor.py
tests/agents/test_verifier.py
tests/agents/test_writer.py
tests/agents/test_pipeline.py
```

The `@pytest.mark.live_llm` marker gates real API calls. Default
`pytest` runs only schema/structure tests. Live tests run on-demand with
`pytest -m live_llm`.

Each agent test covers the happy path, the escalation trigger, and
schema validation against malformed input.

---

## 10. Versioning Prompts

System prompts live in the agent modules as constants (or, for the
classifier, as a function that injects runtime `VaultConfig` values).
On any prompt change, bump the version string. Prompts live in git —
diffs are reviewable, history is permanent.
