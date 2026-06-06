# P1 — Semantic Search, Sharded Vector Store & Event Graph — Design (v1.0)

> **Status:** design, partially implemented (P1a local). Working note.
> **Scope:** the flat retrieval layer that `knowledge-graph.md` §12 names "P1" —
> sharded vector store + lexical hybrid + incremental reindex — **plus** the
> drop/commit **event-graph** substrate that the typed KG (P2+) builds on.
> **Relationship:** refines `knowledge-graph.md` §8 (storage/sharding) and §12
> (phases). Where the two differ, **this doc wins for P1**; `architecture.md`
> still wins on the overall model.

---

## 0. Goal & constraints (inherited, restated)

Make retrieval return *connected, meaningful knowledge* at ~30k-chunk scale, on
**git as the only store** and a **cheap model only**, language-agnostic, $0
free-tier, lightweight optional deps.

Two things P1 must get right that the v0 flat design did not:

1. **SOTA-but-cheap vectorization** that survives 30k+ chunks in git without
   blowing up history or recall.
2. **Drop & commit graphification** — the single most important core capability
   (the user's words): every drop and every watched-repo commit becomes a
   first-class **event node** with provenance and causal edges, so the graph can
   answer *"why does this note exist / what work produced it"*, not just *"what
   words co-occur"*.

**The cheap model proposes; correctness comes from architecture** — deterministic
candidate generation, schema-locked binary verification, bi-temporal
close-don't-delete, and git as the audit log.

---

## 1. Vectorization — SOTA on a free tier (locked)

**Embed on the GitHub Actions runner, not an external API.** The default
embedder is a **local CPU model run in-process inside the reindex Action** — no
API key, no provider coupling, and no GCP↔GitHub trust boundary (the embedding
runs in the same CI context that already holds the vault token). A cloud embedder
is an optional override.

| # | Decision | Why |
|---|---|---|
| V1 | **Default model: `google/embeddinggemma-300m`, ONNX int8, MRL-truncated to 256 dims, run locally via onnxruntime (no torch).** | Best open quality-per-byte under 500M (MMTEB 61.15), multilingual incl. **Korean**, 308M params fit a 2-vCPU/8GB runner; MRL→256 gives a tiny index. Provider-independent: works whether chat is gemini/anthropic/openai/ollama. |
| V1b | **Runtime = onnxruntime + tokenizers (no torch).** Model weights live in `actions/cache` (`~/.cache/huggingface`), **never committed** (int8 ≈ 300MB > GitHub's 100MB file limit). `fastembed` is the path for catalog models (`BAAI/bge-m3`, `multilingual-e5`); a cloud model routes through litellm. | EmbeddingGemma isn't in fastembed → small custom backend. Caching (not committing) avoids Git-LFS bandwidth caps + repo bloat (Simon Willison's Actions-index pattern). |
| V2 | **Quantize output to int8** (per-component `round(v·127)`, unit vectors), stored **base64**. ~256–768 B/vec. | ~**99 % recall** vs float32 at **4× smaller**; int dot-product ranks identically to cosine. Orthogonal to the model's *own* int8 weight quantization. Binary (32×, rescore) is a future tier. |
| V3 | **One note = one chunk.** Prepend a 1-sentence **context header** before embedding. Long notes and dated append-logs (`## YYYY-MM-DD …`, e.g. `life/<area>.md`) split on H2. | Personal notes are atomic; over-chunking breaks the node↔passage 1:1 mapping. Splitting dated logs lets each entry shard by its own month + re-embed independently. |
| V4 | **Hybrid lexical + semantic via BM25S, fused with RRF** (k=60). BM25S is pure numpy/scipy, in-process. | Dense misses exact tokens; RRF needs no cross-retriever score calibration. This is P1b. |
| V5 | **Asymmetric query/document encoding.** EmbeddingGemma task prompts (`task: search result \| query:` / `title: none \| text:`); for the cloud path, per-provider `input_type`/`task_type` (gated per provider, **not** via `drop_params`). | Query/doc asymmetry is worth ~+12 % nDCG (Cohere); getting prefixes wrong silently costs 5–15 pts. |
| V6 | **Determinism is moot:** re-embed is gated on the chunk's **text hash**, not vector equality, so runner/onnxruntime version drift never churns the committed index. Pin the model revision + `onnxruntime`. | CPU ONNX is reproducible-ish but not bit-exact across images; the hash-gate makes that irrelevant. |

Runner math (research-verified): EmbeddingGemma is light; a 30k-chunk cold build
finishes well inside the 6-hour job cap and < ~40 of the 2,000 free monthly
minutes (private repo; public is unlimited). Incremental reindex re-embeds only
changed chunks → steady-state ≈ seconds. The GitHub Models API alternative
(text-embedding-3-small via `GITHUB_TOKEN`) is **rejected** for cold builds: its
150-requests/day free cap turns the backfill into a multi-day throttled chore.

---

## 2. Sharding — big-tech principles applied to a git vault (locked)

Surveyed: **Instagram** (logical vs physical shards, ID-embedded shard so a row
self-routes), **Discord** ((channel, time-bucket) keying), **InfluxDB** (hot vs
cold *shard groups* by time), **NEAR** (dynamic resharding, stateless validation),
**Notion** (logical schema resharding). The transferable invariants:

| # | Decision | Source principle |
|---|---|---|
| S1 | **Shard key = `(area, YYYY-MM)`** where `area ∈ {wiki, projects, life, daily, identity}` and `YYYY-MM` is **per chunk**: a dated `## YYYY-MM-DD` entry heading sets its own month, else the chunk inherits the note's **creation month** (frontmatter `created`, else the `daily/` folder date, else `undated`). | Discord (channel,bucket) + InfluxDB time shard groups. Per-chunk month stops an append-only log from piling into one unbounded `undated` shard — its entries fan out across month shards. |
| S2 | **`chunk_id` embeds its shard** → `"{area}/{YYYY-MM}/{slug}#{n}"`. A chunk self-routes; no lookup table needed to find its file. | Instagram ID-embedded shard. |
| S3 | **Tail-hot, cold-frozen.** Only the **current-month** shard of each area is the write-hot file; past-month shards are **sealed** and change only on the rare edit of an old note. | InfluxDB hot/cold shard groups; sealed shards ⇒ ~zero git delta on the common path. |
| S4 | **Freeze, don't rewrite.** Reindex never re-summarizes or re-emits unchanged lines; sealed shards stay byte-identical so git stores no new blob. Editing an old note re-opens *only its* month shard. | Git history hygiene; model-collapse avoidance (never re-summarize bodies). |
| S5 | **`index/manifest.json` is the directory layer** — maps each shard → `{path, sha, n_chunks, bytes, sealed, updated}`. The query path reads the manifest to know which shards exist; reindex reads it to diff. | Notion logical directory / Instagram logical→physical map. |
| S6 | **Soft cap ~4k chunks / ~8 MB per shard.** If a month-area shard exceeds it, suffix `-NN` (`daily/2026-06-01.jsonl`… is already month-keyed; overflow is rare outside `daily`). | GitHub 50 MB warn / 100 MB reject; keeps any single fetch cheap. |
| S7 | **Hot shards append JSONL; sealing compacts.** Within the hot month, new chunks **append** (cheap delta). On month rollover the prior shard is rewritten **once** (sorted, deduped) then sealed forever. | Binary/quantized arrays don't git-delta-compress, so minimize how often a shard's bytes move. |

**Why creation-month (not last-modified-month):** a note's shard is its stable
*home* (Instagram ID-embedded). Last-modified keying would migrate a note between
shards on every edit, churning two shards per edit. Creation-month keeps the home
fixed; an edit rewrites one line in one (usually recent) shard. Old-note edits are
long-tail, so cold shards stay quiet in practice — the honest hot/cold model, not
a false immutability claim.

---

## 3. Incremental reindex (locked)

```
reindex:
  1. refresh local vault cache (reuse cli_search._refresh_vault_cache — now via
       the Git blob API, so shards/notes >1MB are not silently dropped)
  2. enumerate indexable files: wiki/ projects/ life/ daily/ identity/
        exclude life/credentials/**, raw/**, index/**, graph/**, mono/**, log/**,
        wiki/index.md
  3. chunk each note → per-chunk {month, hash=sha256(redact(text))}   # secret-safe (§6)
        chunk_key = <path>#<n>; if stored hash matches → reuse vec (freeze)
        else → (re)embed; place into shard (area, chunk-month); re-home if moved
  4. for each dirty shard: rewrite its JSONL (sorted by chunk_id), update manifest
  5. write changed shards + manifest back via github_store.write_atomic (one commit)
  6. mirror the written files into the local cache (consistent immediate query)
```

- **Diff unit = per-chunk content hash of the redacted text**, not git blob sha
  (the local cache is API-fetched, not a git clone — no blob sha available, and
  redaction must be inside the hash boundary so a credential never changes the
  *indexed* content silently). Per-chunk granularity means appending one entry to
  a long log re-embeds only that entry, not the whole file.
- **Deletions:** a manifest path with no corresponding file → drop its chunks
  from the shard, mark dirty.
- **Atomic:** all dirty shards + manifest land in **one** commit (`write_atomic`,
  Git Tree API) so the index is never observed half-updated.

---

## 4. Event graph — drops & commits as first-class nodes (locked, the core)

This is the substrate the typed KG (knowledge-graph.md P2+) stands on. It is
**P1d** here because it depends on `digest.py` enrichment (§5) and the vector
index (§1) for candidate linking.

### 4.1 Node & edge model

**Event nodes** (new, alongside the 5 KG node types):
- `drop` — one ingested Telegram drop (the provenance anchor for everything it
  produced). Zep calls these *episodic* nodes.
- `commit` — one watched-repo commit.

**Entity node** (new): `repo` — a code repository, **distinct from `project`**
(a repo implements work toward a project; they are not the same node).

**Edges** (event-specific, on top of the KG's `supports/contradicts/elaborates/part_of`):

| Edge | From → To | Derivation |
|---|---|---|
| `documents` | `drop` → note (`concept`/`life`/`project`) | deterministic: the pipeline already knows which note a drop wrote. |
| `motivated_by` | `commit` → `project` / `concept` / `drop` | 2-stage linking (§4.3). **The causal money edge.** |
| `in_repo` | `commit` → `repo` | deterministic from digest. |
| `implements` | `commit` → `project` | deterministic if repo↔project mapping known, else 2-stage. |
| `authored_by` | `commit` → `person` | deterministic from commit author. |
| `precedes` / `followed_by` | `commit` ↔ `commit`, `drop` ↔ `drop` | deterministic temporal order within repo / day. |
| `mentions` / `about` | `drop`/`commit` → `concept`/`person` | deterministic candidates → binary verify (§4.3). |

### 4.2 Deterministic-first parsing (no LLM where structure exists)

- **Commits → Conventional Commits parse** (`feat(scope): …`, `fix: …`, trailers
  like `Refs: #12`, `Co-authored-by:`). Type, scope, breaking-flag, issue refs,
  co-authors are extracted with **zero** model calls.
- **Drops** already carry their target path, drop_type, and extracted entities
  from the existing pipeline — those become `documents`/`mentions` edges for free.
- The model is invoked **only** for the edges structure can't supply: `commit →
  motivated_by → {project,concept,drop}` and ambiguous `mentions`.

### 4.3 2-stage, hallucination-safe linking

```
stage 1 — deterministic candidate generation (no LLM):
    for a commit, gather candidates by ≥2 independent signals from:
      • vector similarity (commit message+files ↔ note, cosine top-k)
      • repo↔project mapping (config / frontmatter)
      • shared issue/PR refs, shared file paths, temporal proximity to a drop
    keep only candidates with ≥2 signals  → small, high-precision set
stage 2 — Flash binary verification (cheap model):
    for each candidate: "Did this commit do work motivated by THIS item? yes/no
    + which sentence/file is the evidence." schema-locked, temperature=0.
    accept iff yes AND evidence present AND confidence > 0.7.
```

No free-form "find everything related" prompt (that is where cheap models
hallucinate). Candidates are deterministic; the model only **filters**.

### 4.4 Bi-temporal (same model as KG §5)

Event edges are bi-temporal — `valid_at` (commit/drop time), `invalid_at`
(null until superseded), `created_at`/`expired_at` (system belief). **Close,
never delete.** A commit that reverts another sets the reverted `motivated_by`
edge's `invalid_at`; the revert is appended.

### 4.5 MAGMA dual-path consolidation

- **Fast path (write-time):** deterministic edges (`documents`, `in_repo`,
  `authored_by`, `precedes`) are written immediately — no model, no latency.
- **Slow path (daily batch):** `motivated_by` / ambiguous `mentions` go through
  §4.3 in the existing morning/weekly batch, gated into the daily digest if any
  are destructive. Matches the morning(daily)+weekly rhythm and the Flash RPD
  budget.

---

## 5. `digest.py` enrichment — prerequisite for §4 (locked)

Today `digest.py` captures only `{sha[:7], time, author_name, first-line(≤120),
repo}`. The event graph needs more, so digest must also capture, **per commit**:

- `files_changed` (paths + add/del counts) — the strongest deterministic signal
  for `motivated_by` candidate generation (shared-path overlap with a note).
- `full_message` (subject **+ body + trailers**) — Conventional-Commits parse,
  issue refs, co-authors.
- `parents` / `is_merge` — to thread `precedes`/`followed_by` and skip merge noise.
- `commit_type` + `scope` + `breaking` — the deterministic parse result, stored
  so the graph layer never re-parses.

This is a **non-destructive** widening of the existing commit dict (old readers
keep working). It must run through `secret_filter.redact()` — commit messages and
diffs are a credential-leak vector.

---

## 6. Credential safety (invariant, restated)

Reindex and graph extraction go through `safe_read` (hard-blocks
`life/credentials/`) and `secret_filter.redact()` on **every** value that enters
an index or a node/edge: embedding input, excerpts, evidence quotes, commit
messages, file paths. The content hash (§3) is computed on the **redacted** body
so a credential can never silently ride along inside an "unchanged" note.

---

## 7. Storage layout (locked)

```
index/
  manifest.json                     # {shards: {"<area>/<YYYY-MM>": {sha,n,bytes,sealed,updated}}, model, dims, version}
  vec/<area>/<YYYY-MM>.jsonl        # {chunk_id, path, heading, excerpt, hash, vec(int8-base64)}
  bm25/<area>.npz                   # P1b: scipy CSR term-doc matrix + vocab sidecar
graph/
  nodes.jsonl                       # P1d: drop/commit/repo + the 5 KG node types
  edges.jsonl                       # P1d: bi-temporal edges (§4.1, §4.4)
  entity_index.jsonl                # P1d: canonical entity → node id, embedding
```

Everything is plain text/JSONL committed to the vault. No DB, no service, no new
credential. The `index/` and `graph/` trees are themselves excluded from indexing.

---

## 8. Implementation phases

- **P1a — vector store core (DONE, local):** `embeddings.py` (local
  EmbeddingGemma via onnxruntime → int8 → base64; fastembed / litellm overrides;
  dimension-agnostic), `embeddings_gemma.py` (custom ONNX backend —
  **validated** with the real int8 model: KO↔EN retrieval + int8 roundtrip),
  `semantic_index.py` (chunk-level shard routing, manifest, incremental reindex,
  query), `monogram reindex` CLI. Dep: `[semantic-gemma]` (no torch).
- **P1b — lexical hybrid (DONE, local):** `bm25.py` — pure-Python Okapi BM25 over
  heading+excerpt + RRF fusion (no scipy); `semantic_search(hybrid=True)` is the
  default, `monogram search --semantic` is now hybrid. (Full BM25S over complete
  chunk text + optional MiniLM rerank = future refinement.)
- **P1c — MCP `semantic_search` tool (DONE, local):** `mcp_server.py` exposes a
  `semantic_search` tool (query / k / kind) over the hybrid retriever.
- **P1d — event graph (deterministic path: local):** `digest.py` enrichment (§5,
  with a `daily/<date>/commits.jsonl` sidecar) → `commit_parse.py` (Conventional
  Commits) → `event_graph.py`: write-time deterministic edges (`documents`,
  `in_repo`, `authored_by`, `precedes`, `implements`) via `monogram graph`, plus
  the `motivated_by` 2-stage slow path (`--link`: ≥2-signal candidates → Flash
  verify, conservative recall). Bi-temporal, close-don't-delete.

P1a is independent of the graph and ships first; the graph (P1d) is the highest-
value layer and depends on P1a (candidate linking) + §5.

---

## 9. Research basis (P1-specific; see knowledge-graph.md §13 for the KG citations)

**Vectorization / quantization:**
- Matryoshka Representation Learning — arXiv:2205.13147 (truncatable dims).
- Google `gemini-embedding-001` model card + MRL guidance; `text-embedding-004`
  deprecation notice (2026-01).
- Scalar/binary quantization recall studies (Cohere int8/binary; Qdrant, Weaviate
  quantization benchmarks — int8 ≈ 99 % recall @ 4×, binary @ 32× + rescore).
- BM25S (Lù, 2024, arXiv:2407.03618) — numpy/scipy in-process BM25.
- RRF (Cormack et al., 2009) — rank fusion without score calibration.
- Anthropic Contextual Retrieval (2024) — per-chunk context header.
- `ms-marco-MiniLM-L6-v2` cross-encoder reranker.

**Sharding (big-tech):**
- Instagram sharding (logical vs physical, ID-embedded shard) — Instagram Eng.
- Discord trillion-message storage ((channel, time-bucket) keying) — Discord Eng.
- InfluxDB hot/cold shard groups by time — InfluxData docs.
- NEAR Nightshade / dynamic resharding; stateless validation — NEAR spec.
- Netflix data sharding & EVCache; Notion logical schema resharding — eng blogs.

**Event graph / provenance / causal linking:**
- Zep/Graphiti episodic nodes + bi-temporal edges — arXiv:2501.13956.
- MAGMA dual-path (fast temporal + slow causal consolidation) — memory-systems lit.
- Conventional Commits 1.0.0 spec (deterministic commit parse).
- Provenance-anchored memory (drop/commit as first-class event = audit anchor).

---

## 10. Open / deferred

- **Binary quantization tier** (96 B/vec + int8 rescore) — defer until shard
  count or fetch cost demands the 32× cut.
- **repo↔project mapping source** — start from `MONOGRAM_WATCH_REPOS` + project
  frontmatter; a `mono/config.md` `repo_projects:` map is the explicit override.
- **Cross-encoder rerank dep** — ships only as `[semantic-rerank]`; not core.
- **Overflow suffixing** (`-NN`, S6) — **not yet implemented.** The >1MB cache
  wall that motivated it is resolved by reading the cache via the Git blob API
  (§3), so suffixing is now a fetch-cost optimization, deferred until a single
  month-area shard actually gets large.
- **Embedding provider independence — RESOLVED.** The default is a local
  EmbeddingGemma model run on the Actions runner (no key, works for any chat
  provider). Cloud embedders (gemini/openai/voyage) are an optional
  `embedding_model` override with a separate `embedding_base_url`. The old
  "anthropic-only can't index" problem is gone (Anthropic has no embeddings API,
  but the local default never needs one).
- **EmbeddingGemma ONNX backend is VALIDATED** with the real int8 model
  (`onnx-community/embeddinggemma-300m-ONNX`): inputs are `input_ids` +
  `attention_mask`; the graph emits a pooled, normalized `sentence_embedding`
  (selected by name, not index — fixed a bug that took `last_hidden_state`); the
  `.onnx_data` external-weights sibling is downloaded alongside (fixed). A Korean
  query retrieved EN+KO deployment notes over an unrelated note at dim 256, and
  the int8 roundtrip preserved ranking (`tests/test_gemma_integration.py`, opt-in
  via `MONOGRAM_TEST_GEMMA=1`). Batching (currently batch-1) is a cold-start
  optimization, deferred.
- **Mid-log edits/deletes churn the tail** — chunk identity is positional
  (`<path>#<n>`); inserting/deleting a *middle* entry shifts later indices.
  Append (the dominant pattern) is fully incremental; mid-edits re-embed the
  tail. A content-addressed chunk id would remove this; deferred.
