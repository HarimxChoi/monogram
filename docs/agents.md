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
        "personal_thought", "query", "ambiguous"
    ]
    target_path: str = Field(
        description="Relative path in repo, e.g. scheduler/projects/paper-a.md"
    )
    target_exists: bool = Field(
        description="True if target_path already exists in MEMORY.md"
    )
    confidence: Literal["high", "medium", "low"]
    tags: list[str] = Field(default_factory=list, max_length=5)
    reasoning: str = Field(
        max_length=200,
        description="One-line rationale (logged, not shown to user)"
    )
```

### System Prompt

```
You are the classifier stage of Monogram's pipeline.

Given an inbound payload, determine:
1. What kind of content this is (drop_type)
2. Where it should live (target_path)
3. Whether the target already exists (check MEMORY.md pointers)
4. Confidence in the classification (high/medium/low)

Routing rules are in SCHEMA.md "Source Types → Destination" section.
Follow them exactly — do not invent new categories.

If confidence is low, the verifier will request reclassification
with thinking enabled. Do not pad low-confidence outputs with extra
detail; state uncertainty clearly.

Output valid JSON matching the Classification schema.
```

### Escalation triggers

- `confidence == "low"` → verifier will re-run this stage with thinking ON
- `drop_type == "ambiguous"` → verifier will escalate to Flash

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
    summary: str = Field(max_length=500)
    source_url: str | None = None
    key_claims: list[str] = Field(default_factory=list, max_length=5)
    tags: list[str] = Field(default_factory=list, max_length=5)

class PersonalLog(BaseModel):
    kind: Literal["personal_log"] = "personal_log"
    content: str
    context: str | None = None  # what the user was doing, if inferrable

class QueryIntent(BaseModel):
    kind: Literal["query_intent"] = "query_intent"
    question: str
    scope: Literal["scheduler", "wiki", "both"]
    time_range: Literal["today", "week", "month", "all"] = "all"

ExtractedPayload = Union[ProjectUpdate, ConceptDrop, PersonalLog, QueryIntent]
```

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
    existing_claim: str = Field(max_length=200)
    new_claim: str = Field(max_length=200)
    severity: Literal["minor", "material", "direct"]

class VerifyResult(BaseModel):
    ok: bool
    contradictions: list[Contradiction] = Field(default_factory=list)
    target_confidence: Literal["high", "medium", "low"]
    supersession_proposed: str | None = Field(
        default=None,
        description="Path of existing page to supersede, if applicable"
    )
    escalate: bool = Field(
        default=False,
        description="True if downstream should re-run with Flash"
    )
    reasoning: str = Field(max_length=300)
```

### System Prompt

```
You are the verifier stage of Monogram's pipeline — the reliability gate.

Given an extracted payload and the relevant pointers from MEMORY.md,
check for:

1. Contradictions with existing facts
   - minor: different phrasing of same fact, no action needed
   - material: partial conflict, needs user awareness
   - direct: supersession candidate — new fact replaces old

2. Confidence appropriateness
   - Does the source support the claimed confidence level?
   - Single unverified source maxes at "medium"
   - Third-party link without cross-check maxes at "low"

3. Supersession need
   - If a direct contradiction with an existing page exists,
     propose that page's path as supersession target

4. Escalation signal
   - Set escalate=true if: contradiction is ambiguous,
     confidence is unclear, or payload is structurally strange

You do NOT write. You gate. Downstream Writer stage acts on your verdict.
If ok=false, the pipeline will either escalate or ask the user.

Output valid JSON matching VerifyResult schema.
```

### Escalation triggers

- `escalate == true` → Writer receives signal, caller re-runs extractor+verifier on Flash
- `contradictions[].severity == "material"` AND no clear supersession → ask user
- `target_confidence != extractor_confidence` → Writer uses verifier's value

---

## 5. Writer

**File:** `src/monogram/agents/writer.py`
**Model:** none — deterministic Python
**Input:** ExtractedPayload + VerifyResult + target file state
**Output:** commit SHA (single git commit covering all writes)

### Behavior

Per the 2×3 grid (see `docs/architecture.md` §2 and `docs/vault-layout.md`),
every drop produces writes in up to 5 paths, all committed atomically.

```python
# pseudocode

def run(drop, classification, payload, verify_result):
    if not verify_result.ok and not verify_result.escalate:
        # hard block — ask user
        return AskUser(payload, verify_result)

    today = drop.timestamp.strftime("%Y-%m-%d")
    staged_writes: dict[str, str] = {}   # path -> content
    decision_entry = start_decision_entry(drop, classification, verify_result)

    # ── 1. ALWAYS: append to today's drops.md (temporal source) ───────────────
    daily_path = f"daily/{today}/drops.md"
    staged_writes[daily_path] = append_drop_entry(
        existing=github_store.read(daily_path),
        drop=drop,
        classification=classification,
    )

    # ── 2. CONDITIONAL: stable state write ────────────────────────────────────
    target_path = None
    if classification.target_type == "off_topic":
        # no stable-state change, just the drops.md append
        pass

    elif classification.target_type == "scheduler_update":
        target_path = f"scheduler/projects/{classification.project_name}.md"
        staged_writes[target_path] = update_scheduler_project(
            existing=github_store.read(target_path),
            payload=payload,
            verify_result=verify_result,
        )

    elif classification.target_type == "wiki_entry":
        if verify_result.target_confidence == "low":
            target_path = f"wiki/_unlabeled/{today}-{classification.slug}.md"
        else:
            target_path = f"wiki/{classification.category}/{classification.slug}.md"

        # Overwrite in place; git history preserves the prior version.
        # YAML-level supersession linking deferred to v2.0.
        staged_writes[target_path] = compose_wiki_entry(
            existing=github_store.read(target_path),
            payload=payload,
            verify_result=verify_result,
            classification=classification,
        )

    # ── 3. CONDITIONAL: MEMORY.md pointer update ─────────────────────────────
    if target_path:
        staged_writes["MEMORY.md"] = update_memory_pointer(
            existing=github_store.read("MEMORY.md"),
            target_path=target_path,
            status_line=payload.short_summary(),
            confidence=verify_result.target_confidence,
        )

    # ── 4. CONDITIONAL: _categories.json update ──────────────────────────────
    if classification.target_type == "wiki_entry" and verify_result.target_confidence != "low":
        staged_writes["wiki/_categories.json"] = bump_category_counter(
            existing=github_store.read("wiki/_categories.json"),
            category=classification.category,
            keywords=classification.tags,
        )

    # ── 5. ALWAYS: decisions.md append (system telemetry) ────────────────────
    staged_writes["log/decisions.md"] = append_decision_log(
        existing=github_store.read("log/decisions.md"),
        entry=decision_entry.finalize(writes=list(staged_writes.keys())),
    )

    # ── 6. ATOMIC COMMIT: all writes in a single commit ──────────────────────
    commit_sha = github_store.atomic_commit(
        writes=staged_writes,
        message=f"monogram: {classification.drop_type} — {payload.title[:40]}",
    )

    return commit_sha
```

### Atomic commit rule

All writes in `staged_writes` must land in **one git commit**, or nothing
lands. `github_store.atomic_commit()` is the gate. Implementation uses the
GitHub API `POST /repos/{owner}/{repo}/git/trees` + `git/commits` to create
a tree with all changes, then fast-forwards main. Partial staging on network
failure → client-side rollback (no trees persist until the commit is pushed).

### What Writer does NOT do

- No LLM calls (agents.md §0 rule: Writer is deterministic)
- No supersession decisions (Verifier decides, Writer executes)
- No category decisions (Classifier decides, Writer records)
- No direct writes to `raw/`, `reports/`, or `_categories.json` contents
  beyond counter bumps (those have their own write paths in other stages)

### Drops carry no confidence

Drops are **events**, not claims. The entry in `daily/*/drops.md` records
*that something happened* and *how it was classified*, but carries no
`confidence:` field of its own. Only the stable-state write (wiki entry,
scheduler project) carries confidence metadata.

Example drops.md entry format:

```markdown
## 14:32 — url
**Source:** https://arxiv.org/abs/2501.13956
**Classified:** wiki_entry → _refs/2025/ (high)
**Written:** wiki/_refs/2025/zep-temporal-graph.md
**Commit:** abc1234
```

Event record. No confidence. Never superseded.

---

## 6. Escalation Flow

When the verifier sets `escalate=true`, the caller (pipeline runner) does:

```python
# pseudocode in pipeline.py

async def run_pipeline(payload):
    plan = await orchestrator.run(payload)
    classification = await classifier.run(payload, plan)
    extraction = await extractor.run(payload, classification)
    verification = await verifier.run(extraction, classification)
    
    if verification.escalate:
        # re-run extractor on the mid tier (reasoning)
        extraction = await extractor.run(
            payload, classification,
            model_override=get_model("mid"),
        )
        verification = await verifier.run(extraction, classification)
        
        if verification.escalate:
            # two escalations = ask user
            return AskUser(payload, verification)
    
    if not verification.ok:
        return AskUser(payload, verification)
    
    return await writer.run(extraction, verification, classification)
```

Escalation is bounded — at most one re-run. No infinite loops.

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

Each agent module has a corresponding test file:

```
tests/agents/test_orchestrator.py
tests/agents/test_classifier.py
tests/agents/test_extractor.py
tests/agents/test_verifier.py
tests/agents/test_writer.py
tests/agents/test_pipeline.py  ← end-to-end
```

Tests use the `@pytest.mark.live_llm` marker (added in Phase B) to gate
real API calls. Default pytest runs only schema/structure tests that don't
burn quota. Live tests run on-demand with `pytest -m live_llm`.

Each agent test covers:
- Happy path (expected input → expected schema output)
- Escalation trigger (low-confidence input → escalate=true or thinking=on)
- Schema validation (malformed input → clear error)

---

## 10. Versioning Prompts

System prompts for each stage are stored as module constants:

```python
# src/monogram/agents/classifier.py

CLASSIFIER_SYSTEM_PROMPT_VERSION = "v1"
CLASSIFIER_SYSTEM_PROMPT = """
You are the classifier stage of Monogram's pipeline.
...
"""
```

On any prompt change, increment the version string. `log/llm_usage.jsonl`
records the prompt version alongside the call, so regressions are traceable.

12-Factor Agents Factor 2 (own your prompts): they live in git, not in a
prompt-management SaaS. Diffs are reviewable. History is permanent.
