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
    life_area: str | None = None        # set only when target_kind == "life"
    slug: str                           # validated to [a-z0-9-]+
    confidence: Literal["high", "medium", "low"]
    tags: list[str] = Field(default_factory=list)
    reasoning: str = Field(
        description="One-line rationale (logged, not shown to user)"
    )

    @property
    def target_path(self) -> str:
        # Derived, never emitted by the LLM:
        #   project    → projects/<slug>.md
        #   life       → life/<life_area>.md
        #   wiki       → wiki/<slug>.md
        #   credential → life/credentials/<slug>.md
        #   daily_only → ""  (drops.md only)
        return derive_path(self.target_kind, self.slug, self.life_area)
```

### System Prompt

```
You are the classifier stage of Monogram's pipeline.

Given an inbound payload, determine:
1. What kind of content this is (drop_type)
2. Where it should live (target_kind + slug, or life_area when life)
3. Confidence in the classification (high/medium/low)

Emit target_kind + (slug | life_area) only — never a raw path; the path is
derived. Routing rules (the five target_kind values) are embedded in the
prompt, with the user's life_area list injected at runtime. Do not invent
new categories.

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
**Input:** ExtractedPayload + VerifyResult + Classification + preloaded file state
**Output:** `FileChange` (a `writes` dict + commit message). The commit itself is performed by the caller (`listener` / `bot`), not the Writer.

### Behavior

Per the 2x3 grid (see `docs/architecture.md` and `docs/vault-layout.md`), one
drop stages writes across up to ~5 paths. The Writer only builds the
`FileChange` — it has no git side effect.

```python
# pseudocode — mirrors writer.run()

async def run(extraction, verification, classification,
              existing_target="", existing_memory="", existing_drops="",
              existing_decisions="", existing_wiki_index=""):
    today = utcnow().strftime("%Y-%m-%d")
    writes: dict[str, str] = {}                 # path -> content
    target_path = classification.target_path    # derived from target_kind
    target_kind = classification.target_kind

    # 1. Stable-state write — dispatched on target_kind
    if target_kind == "project":
        writes[target_path] = serialize(meta, render_project(extraction))   # OVERWRITE
    elif target_kind == "life":
        writes[target_path] = append_timestamped(existing_target, render_life(extraction))
    elif target_kind == "wiki":
        writes[target_path] = serialize(meta, render_wiki(extraction))      # flat wiki/<slug>.md
        writes["wiki/index.md"] = upsert_index_line(existing_wiki_index, ...)
        writes.update(compute_backlink_writes(...))      # tag-overlap peers, cap 5
    elif target_kind == "credential":
        writes[target_path] = render_credential(extraction)   # life/credentials/<slug>.md, minimal
    # daily_only -> no stable-state write

    # 2. daily/<today>/drops.md — ALWAYS (credential is redacted inside the entry)
    drops_path = f"daily/{today}/drops.md"
    writes[drops_path] = append(existing_drops, build_drop_entry(...))

    # 3. MEMORY.md pointer — only project & wiki
    if target_kind in ("project", "wiki"):
        writes["MEMORY.md"] = update_memory_pointer(existing_memory, target_path, ...)

    # 4. log/decisions.md — ALWAYS (credential slug/path redacted in the entry)
    writes["log/decisions.md"] = append(existing_decisions, build_decision_entry(...))

    # 5. Secret-shape backstop — redact every staged write except the
    #    credential file itself, so a classifier misroute can't leak a key.
    for path in writes:
        if not path.startswith("life/credentials/"):
            writes[path] = secret_filter.redact(writes[path])

    return FileChange(writes=writes,
                      commit_message=build_commit_message(classification),  # slug redacted inside
                      primary_path=target_path or drops_path,
                      confidence=verification.target_confidence)
```

### Committing the FileChange

The Writer returns; the caller commits. By default that is
`github_store.write_multi()` — one commit per file, simple and **not** atomic.
`github_store.write_atomic()` (GitHub Git Tree API, single all-or-nothing
commit) is implemented and available opt-in for callers that need it.

### Credential backstop

`life/credentials/<slug>.md` is the only path where a secret legitimately
lives. Every other staged write — drops, MEMORY, decisions, wiki/project
bodies, and the commit message — passes through `secret_filter.redact()`, so
a misrouted credential cannot leak a key shape into a benign path.

### What Writer does NOT do

- No LLM calls (Writer is deterministic)
- No supersession decisions (Verifier decides, Writer executes)
- No git commit (the caller performs it)

### Drops carry no confidence

Drops are **events**, not claims. The entry in `daily/*/drops.md` records
*that something happened* and *how it was classified*, but carries no
`confidence:` field of its own. Only the stable-state write carries
confidence metadata.

Example drops.md entry (from `_build_drop_entry`):

```markdown
## 14:32
**concept_drop** -> `wiki/rtmpose.md`
RTMPose hits 500 FPS on a single GPU
```

For a credential the same slot is redacted:

```markdown
## 14:32
**credential** -> (redacted)
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
