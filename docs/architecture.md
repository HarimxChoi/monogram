# Monogram — Architecture

> One document. One design. One mental model.
> This is the unified spec for Monogram's agent pipeline, memory layout,
> and model routing. Paired with your vault's `identity/` folder
> (user-defined behavior rules and domain schema) and
> `docs/agents.md` (per-stage Pydantic schemas).

---

## 0. Design Principle

**Quality through architecture, not through larger models.**

A weak model making 5 verified calls produces more reliable output than a
strong model making 1 unverified call. Monogram exploits this by using the
cheapest capable model (the low tier) as the baseline and spending
the quota savings on verification passes instead of heavier inference.

The agent's reliability comes from the pipeline structure. The model is
interchangeable.

---

## 1. The Always-Loaded Standing Context

Every LLM call in Monogram loads a small standing context — your vault's
`identity/` folder plus `MEMORY.md`. Nothing more, nothing less.

```
Your vault's identity/      user-maintained: behavior rules + domain schema
MEMORY.md                   ~5KB  pointer index — where everything lives
────────────────────────────────────────────────────────────────────────
Standing context floor  ~10-12KB  (~2500-3000 tokens)
```

**Why these and nothing else:**

- Your vault's `identity/` folder is where you, the user, keep the agent's
  behavior rules and your domain schema (what kinds of things can exist and
  how they transform: drop → wiki entry, project update → scheduler commit).
  These files are NOT shipped in this repo — they live in your private vault.
- `MEMORY.md` is the index — every file has a pointer here, always loaded

Everything else loads on demand, referenced by `MEMORY.md`.

On Flash-Lite (250k TPM, 1M context window), a 3000-token floor is 0.3% of
per-call capacity. Plenty of room for task content and tool responses.

**Anti-pattern (what we're avoiding):** dumping the whole wiki or all project
files into every call. That wastes tokens on irrelevant content, degrades
attention to the actual task, and exhausts rate limits faster.

---

## 2. Memory Layout — The 2×3 Grid

Monogram's storage is organized along two orthogonal axes. The grid
replaces all prior tier / folder / component taxonomies in this codebase.

```
                 SOURCES              STATE              DERIVED
                 (what comes in)      (what we maintain) (what we generate)
  ──────────────────────────────────────────────────────────────────────────
  TEMPORAL       daily/*/drops.md     —                  daily/*/report.md
  (time-indexed) daily/*/convos.md                       reports/weekly/
                 daily/*/commits.md
  ──────────────────────────────────────────────────────────────────────────
  STABLE         —                    wiki/              MEMORY.md
  (path-indexed)                      projects/          wiki/index.md
                                      life/              board.md
                                      identity/
```

**Reading the grid:**

- **Rows** = how it's indexed. Temporal means the path is a date. Stable
  means the path is a concept/project name.
- **Columns** = data role. Sources are events that enter the system.
  State is what the agent maintains as truth over time. Derived is
  computed from sources + state.
- **Empty cells are load-bearing.** "Temporal state" is a contradiction
  (state changes over time, but it's indexed by identity not date).
  "Stable sources" is a contradiction (sources are always dated events).

### Why this replaces the earlier folder-count models

Earlier drafts listed 5 folders (scheduler/ wiki/ log/ raw/ identity/) and
a later draft listed 6 "components" (drops, conversations, commits, wiki,
scheduler, reports). Neither count mapped cleanly onto both folders AND
conceptual roles. The grid counts neither — it explains the two axes and
lets the folder inventory follow from the cells.

The grid explains both inventory and rationale in one diagram:

- Need time-indexed sources? → `daily/`
- Need path-indexed state? → `wiki/`, `projects/`, `life/`, `identity/`
- Need time-range summaries? → `reports/`
- Need navigation? → `MEMORY.md`

### Full folder layout (v0.3)

```
<your-github-user>/mono (= Obsidian vault)
│
├── config.md                    ← USER-EDITABLE: language, life_categories, never_read_paths
├── README.md
├── MEMORY.md                    ← derived: pointer index for project + wiki
├── board.md                     ← project board (update-not-regenerate)
│
├── identity/                    ← always-loaded context
│   ├── CORE.md
│   ├── SCHEMA.md
│   └── CONSTRAINTS.md
│
├── projects/                    ← active deadlined projects
│   ├── <slug>.md
│   └── archive/                 ← done projects (via /done)
│
├── life/                        ← ongoing life areas (APPEND, not overwrite)
│   ├── shopping.md
│   ├── places.md
│   ├── career.md
│   ├── read-watch.md
│   ├── meeting-notes.md
│   ├── health.md
│   ├── finance.md
│   └── credentials/             ← HARD-CODED LLM-SKIP, never read
│       └── <slug>.md
│
├── wiki/                        ← flat Karpathy-style knowledge
│   ├── index.md                 ← auto-appended on every wiki write
│   └── <slug>.md                ← tags in frontmatter
│
├── daily/                       ← temporal sources + daily derived
│   └── YYYY-MM-DD/
│       ├── drops.md
│       ├── commits.md
│       └── report.md
│
├── reports/
│   └── weekly/YYYY-Www.md
│
├── log/
│   ├── decisions.md
│   ├── runs/YYYY-MM-DD-<job>.md
│   └── unattributed.md
│
└── raw/                         ← archival destination (weekly sweep past 67d)
```

| Folder | Axis | Role |
|---|---|---|
| `projects/` | stable | state — deadlined work |
| `life/` | stable | state — ongoing areas (config-driven) |
| `wiki/` | stable | state — reusable knowledge (flat) |
| `daily/` | temporal | sources + daily derived |
| `identity/` | stable | state (and standing context) |
| `reports/` | temporal | multi-day derived |
| `log/` | temporal | system telemetry (not user data) |
| `raw/` | temporal | immutable source archive |

### 2.1 User-configurable taxonomy

Life categories are NOT hardcoded. They live in `config.md`'s YAML
frontmatter, are loaded at process start by `VaultConfig`, and the
classifier's system prompt is rebuilt per-call with the current list.
Adding a new category is a single edit to config.md + restart.
| `MEMORY.md` | stable | navigation (derived from wiki + scheduler) |

### Atomicity rule

Every agent write produces **one git commit** that spans all affected grid
cells. A drop updating a project writes to four paths in one commit:

```
1. daily/2026-04-17/drops.md           (temporal source — always)
2. projects/paper-a.md       (stable state — if classified as such)
3. MEMORY.md                           (derived — if state changed)
4. log/decisions.md                    (system — always)
```

One commit, atomic. If any write fails, nothing commits. See `docs/agents.md`
§5 (Writer stage) for implementation.

### Events vs claims

Critical distinction the grid enforces:

- **Drops are events.** They record that something happened. They carry
  no confidence — events are certain by definition. They are never
  superseded (history doesn't revise).
- **Wiki entries are claims.** They assert something is true. They carry
  confidence (high/medium/low). They CAN be superseded when better
  information arrives.

If the user drops "RTMPose does 500 FPS" on 2026-04-15 and "RTMPose does
300 FPS" on 2026-04-17, both drops remain in their respective daily
folders (events happened). The wiki entry evolves via supersession
(claim changes). Git history links both.

---

## 3. Agent Pipeline

Every inbound operation — a Telegram drop, a bot chat, a GitHub event —
runs through the same five-stage pipeline. Each stage (except Writer) is
one LLM call.

```
INBOUND → 1. Orchestrator  →  2. Classifier  →  3. Extractor
       →  4. Verifier (reliability gate; escalates on contradictions / low confidence)
       →  5. Writer (pure Python, no LLM, atomic commit)  →  OUTBOUND
```

Per-stage prompts, Pydantic output schemas, thinking-mode rules,
escalation triggers, and token budgets are specified in `docs/agents.md`
(§1-5 = stages, §6 = escalation flow, §7 = thinking, §8 = token budget).
**`docs/agents.md` is the authoritative source** — if anything here
disagrees with it, `agents.md` wins.

Call budget: 4 Flash-Lite calls per drop (stages 1-4), occasionally 5 if
the operation includes a formatted reply. Writer is deterministic.

---

## 4. Model Routing — Three Tiers, Escalation Upward (provider-agnostic in v0.4)

```
TIER   ROLE
────────────────────────────────────────────────────────────────
LOW    classifier, extractor, verifier default, today_brief,
       Obsidian queue-poller classification — all high-frequency
       pipeline stages

MID    verifier escalation target (contradiction / low confidence),
       wiki synthesis

HIGH   morning brief, weekly report, monthly cross-repo analysis,
       paper synthesis (ad-hoc)
```

Tiers are mapped to model strings via `mono/config.md` — no hardcoding
in Python. See `docs/setup/llm-providers.md` for the full guide.

**Gemini default mapping** (the init wizard's "Default" path):

```
low  → gemini/gemini-2.5-flash-lite    (free tier, 1000 RPD)
mid  → gemini/gemini-2.5-flash         (500 RPD)
high → gemini/gemini-2.5-pro           (100 RPD)
```

Edit `mono/config.md` to point at Anthropic / OpenAI / Ollama / any
OpenAI-compatible endpoint instead. Monogram discovers credentials from
`.env` based on the `provider/` prefix in each model string.

**Gemini free-tier rate limits (April 2026):**

| Model       | RPM | RPD   | TPM     | Context |
|-------------|-----|-------|---------|---------|
| Flash-Lite  | 15  | 1,000 | 250,000 | 1M      |
| Flash       | 10  | 500   | 250,000 | 1M      |
| Pro         | 5   | 100   | 250,000 | 1M      |

**Realistic daily budget:**

```
30 Telegram drops × 4 stages   = 120 Flash-Lite
20 bot queries × 2 stages      =  40 Flash-Lite
15 GitHub events × 1 stage     =  15 Flash-Lite
Flash-Lite total               = 175 / 1000 RPD (17.5% util)

~10% of stages escalate        =  15 Flash
2 wiki compilations            =   2 Flash
Flash total                    =  17 / 500 RPD (3.4% util)

1 morning brief                =   1 Pro
weekly lint / 7                = 0.3 Pro avg
Pro total                      = 1.3 / 100 RPD (1.3% util)
```

All three tiers stay under 20% utilization at heavy personal use. 5x safety
margin. Quota is not the bottleneck.

---

## 5. Escalation Rules

The verifier is the gatekeeper. It decides whether a pipeline result is
trustworthy or needs escalation.

```python
# pseudocode

def escalation_policy(verify_result, upstream_confidences):
    if verify_result.contradictions:
        return "flash"  # semantic merging needed
    
    if "low" in upstream_confidences:
        return "flash"  # at least one stage was uncertain
    
    if verify_result.ok:
        return None     # proceed to writer
    
    # verifier says not ok, but no specific reason → try thinking mode
    return "flash-lite-thinking-on"
```

Escalation does **not** re-run the full pipeline. It re-runs only the stage
that produced the weak signal, with a stronger model or thinking enabled.
This keeps escalation cost bounded to 1-2 extra calls.

---

## 6. Confidence — Enum, Not Float

All confidence values in Monogram are one of three symbols:

```
high     verified by 2+ sources OR manually confirmed OR just-written by user
medium   default state for new single-source entries
low      inbox items, automatic extractions without verification,
         entries that have aged past reconfirmation threshold
```

**Why enum:** Tian et al. 2023 (arXiv:2305.14975) established that LLMs cannot
reliably self-assess confidence on a continuous scale. A model claiming "0.73"
vs "0.68" is pattern-matching, not calibrating. Three-level enum is within
actual calibration capacity.

**Why not float:** decay curves, threshold comparisons, and arithmetic on
confidence values all encode false precision. They look principled but run
on noise.

**Decay rules (no math):**

```
if last_confirmed age > 30 days AND confidence == high → demote to medium
if last_confirmed age > 90 days AND confidence == medium → demote to low  
if confidence == low AND not reinforced in 30 days → flag for weekly lint
```

User access or new source matching = reinforcement = reset age counter.

---

## 7. Supersession — Git History Is the Audit Trail

When new information contradicts or updates an existing wiki claim:

1. The Writer **overwrites the file in place** with the new content
2. `MEMORY.md` pointer is updated to reflect the change
3. `git log <path>` preserves every prior version — full rollback is free

Never delete. Git history alone provides the audit trail.

**YAML-level supersession linking** (`supersedes: []`, `superseded_by:
<path>`) is deferred to v2.0. A-MEM research (Xu et al. 2025,
arXiv:2502.12110) shows that supersession with explicit link updates
prevents F1 degradation in long-horizon memory tasks, but the
implementation complexity isn't justified at personal scale in v0.1–v1.0
where `git log` covers the same need.

---

## 8. Metadata Schema (YAML Frontmatter)

Every file in `wiki/` and `projects/` carries this header:

```yaml
---
confidence: high            # enum: high | medium | low
sources: 2                  # integer count
created: 2026-04-17T09:21
last_accessed: 2026-04-17T14:32
last_confirmed: 2026-04-10
tags: [ml-cv, pose, rtmpose]
---

# Page content here.
```

Written by the Writer stage. Never hand-edited by the agent — only bumped
via known operations (read = update `last_accessed`, new source = update
`last_confirmed`, contradiction = overwrite file; git history preserves
the prior version).

Scheduler project files add: `status: active | inactive | done`,
`github_repos: [list]`, optional `deadline: YYYY-MM-DD`. Define the full
field list in your vault's `identity/` folder (user-defined).

---

## 9. MEMORY.md — Pointer Index Spec

One line per entry. Under 150 characters. Format:

```
<name>   <path>   <one-line-status>   [<confidence>]
```

Grouped by category. Always loaded into context. Never contains facts —
only pointers to where facts live.

Example:

```markdown
# MEMORY.md — scheduler repo pointer index
# Updated: 2026-04-17

## Active projects
paper-a        projects/paper-a.md     Phase 0 blocked on GPU, D-8   [high]
project-b      projects/project-b.md   60 cold mails, 200 prepping   [high]
monogram       projects/monogram.md    Phase B closing, B1 merged    [high]
side-project   projects/side-c.md      paused, resource blocker      [medium]

## Wiki — high-confidence knowledge
rtmpose        wiki/ML-CV/rtmpose.md             pose estimation, mobile INT8  [high]
calibration    wiki/ML-Uncertainty/calibration   temp scaling, focal loss      [high]
onnx           wiki/Infra/onnx.md                deployment, quantization      [medium]

## Recent drops (last 7 days)
2026-04-15     wiki/_inbox/2026-04-15-drop1.md   arxiv paper on pose v2        [low]
2026-04-16     wiki/_inbox/2026-04-16-drop2.md   instagram sports technique video [low]
```

Updated by the Writer stage after every commit. Never grows unboundedly —
entries older than 30 days in `## Recent drops` auto-compact into their
target category sections via the weekly lint pass.

---

## 10. Standing Context Discipline

The agent must not infer facts from standing context (SCHEMA, MEMORY, CORE).
These are hints and pointers, not ground truth.

Before asserting any fact in output, the agent either:

- Reads the specific file that MEMORY.md points to, OR
- Verifies via GitHub API (for scheduler state), OR
- Asks the user to confirm

This is the "memory as hint" discipline. Standing context exists to guide
the agent toward the right files and the right tone, not to substitute for
reading the source of truth.

Encoded as a rule in your vault's `identity/` folder (user-defined).

---

## 11. Weekly Lint (Self-Healing)

Runs Sunday night via GitHub Actions. One Pro call, one commit.

```
1. AGE PASS       demote confidence per decay rules (§6)
2. ORPHAN CHECK   MEMORY.md pointers to non-existent files → flag
3. DEAD LINK      wiki links to superseded pages → rewrite
4. LOW CONF       low-confidence entries → surface to user via Telegram
5. INBOX DRAIN    _inbox/ items older than 7 days → propose compilation
6. MEMORY COMPACT Recent drops > 30 days → move to canonical sections
```

Output is a Markdown summary pushed to Telegram with inline approve/reject
buttons. Nothing auto-merges without user approval.

---

## 12. Observability — Every Call Logged

Every LLM call writes one line to `log/llm_usage.jsonl`:

```json
{"ts":"2026-04-17T14:32:01Z","stage":"classifier","model":"gemini-2.5-flash-lite","input":412,"output":58,"thinking":false,"escalated":false,"drop_id":"abc123"}
```

Every agent decision writes one block to `log/decisions.md`:

```markdown
## 2026-04-17T14:32:03Z
Pipeline: drop_abc123
Path: orchestrator → classifier(0.89) → extractor(0.82) → verifier(ok) → writer
Writes: projects/paper-a.md (confidence: medium)
Cost: 4 Flash-Lite calls, ~1.8k tokens
```

This replaces "how do I know what the agent did?" with grep.

Horthy's 12-Factor Agents Factor 6 (own your context window) and Factor 9
(compact errors into context) are encoded here — every interaction produces
a compact, durable log entry that future agent runs can reference.

---

## 13. Non-Goals — What Monogram Explicitly Does Not Build

For v1, Monogram scopes out these patterns even though they appear in
recent agent architectures:

- **Autonomous tick-loop daemon modes.** Personal agent does not need
  always-on background execution. Cron-based proactive layer is sufficient.
- **Sub-agent swarms with fork/teammate/worktree delegation.** Single
  pipeline with verifier escalation covers the same reliability surface.
- **Long-running cloud offload for planning.** Gemini Pro 5 RPD handles
  the heavy tasks. No separate infrastructure needed.
- **Parallel branch exploration (Tree of Thoughts).** Adds cost, adds
  complexity, adds no clear reliability win for structured domain.
- **Typed knowledge graph with entity extraction.** Markdown pointer
  index handles personal scale. Graph becomes useful past ~2000 entries.
- **Vector database.** Long context + grep + MEMORY.md covers retrieval
  needs at personal scale. Vectors add cost and indexing complexity.
- **Multi-agent mesh sync.** Single user, single agent instance.
- **Automatic contradiction resolution.** Verifier flags contradictions;
  user decides. No silent knowledge overwrites.

These remain on the table for v2+ if scale or use case demands them.

---

## 14. Architectural Foundations

Monogram's pipeline design synthesizes several established patterns:

- **ReAct** (Yao et al. 2022) — reason-act-observe loop. The pipeline's
  stages 1-4 are ReAct with explicit stage boundaries.
- **Chain-of-Verification / CoVe** (Dhuliawala et al. 2023) — drafted
  output plus independent verification pass. Stage 4 is CoVe.
- **12-Factor Agents** (Horthy 2024) — own your prompts, externalize
  state, stateless execution. GitHub commits = externalized state.
  Pipeline stages = explicit control flow.
- **A-MEM** (Xu et al. 2025, arXiv:2502.12110) — atomic memory with linked
  supersession; ablation shows −6 F1 when supersession is removed.
- **Zep / Graphiti** (Rasmussen et al. 2025, arXiv:2501.13956) —
  bi-temporal knowledge graph with episodic + semantic + community
  hierarchy; LongMemEval 63.8%. Informs the 2×3 grid split.
- **MemMachine** (arXiv:2604.04853) — non-lossy episodic storage; LoCoMo
  0.92. Informs daily/ being append-only sources.
- **Supermemory** (supermemory.ai/research) — temporal reasoning 76.69% on
  LongMemEval_s via temporal metadata coupling. Informs bi-temporal
  frontmatter.
- **Multi-Agent Report Generation** (LlamaIndex 2024) — researcher → writer
  → editor pipeline beats single-LLM report generation. Informs the
  reports/ derivation layer.
- **Karpathy LLM Wiki** (gist 442a6bf) — markdown + git, no RAG at
  personal scale.
- **Pointer index pattern** (described in public analyses of Anthropic's
  Claude Code, April 2026) — always-loaded MEMORY.md with pointers only.
- **Calibrated confidence** (Tian et al. 2023, arXiv:2305.14975) — enum,
  not float; continuous self-assessment is pattern-matching, not
  calibration.

Monogram is not a reimplementation of any single one of these. It's a
synthesis chosen for a specific target: one user, personal scale,
free-tier model budget, mobile-first capture.

Citations are intentionally centralized here. Peer docs (storage-layout,
agents, ingestion) reference this section rather than duplicate.

---

## 15. What Gets Built When

**v0.1 (Phases A–E — complete):** Full 5-stage pipeline with real
Extractor + Verifier, Telethon listener, aiogram bot, `monogram run`
end-to-end loop, morning 08:00 job (per-project commits + board update
+ brief), Sunday 21:00 weekly report + 67-day archival sweep, calendar
event detection → URL push inside morning brief. 68 tests pass.

**v0.6 — Web UI:** Password-protected, client-side decrypted dashboard
rendering the PARA-mapped vault (critical path, project board, life
timeline, wiki, today). Three delivery modes sharing one backend ABC:

- `gcs` — GCP Cloud Storage, stable bookmarkable URL, ~$0/month
- `self-host` — aiohttp behind cloudflared quick tunnel (rotating URL)
- `mcp-only` — no web UI; use Claude Desktop / Cursor MCP client

Pipeline: `webgen.render_bundle()` → `encryption_layer.wrap(plaintext,
password)` → backend.publish(). AES-256-GCM with 600k PBKDF2 iterations
means the public bucket can host ciphertext safely. Regenerated on
`/webui` bot command and at end of morning brief.

New files: `src/monogram/webgen.py`, `src/monogram/encryption_layer.py`,
`src/monogram/webui/__init__.py` + 3 backends, 10 template files,
`src/monogram/bot_webui_cmds.py`, `src/monogram/cli_webui.py`.

**v1.0:** `monogram search` (covers `daily/` + `raw/` transparently).
PyPI release.

**v2.0:** Bi-temporal metadata (`valid_from`/`valid_until`), YAML-level
supersession linking, Notion one-way sync, natural-language project
management via bot.

**Permanently cut:** Google Calendar 2-way OAuth, monthly/yearly reports.
