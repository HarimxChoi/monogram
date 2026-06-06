# Knowledge Graph — Design (v2.0)

> **Status:** design, not yet implemented. Working note.
> **Scope:** the typed knowledge graph over the markdown vault, plus the
> semantic + graph-aware retrieval it powers (`monogram search --semantic`,
> Telegram `/search`).
> **Authoritative model:** `docs/architecture.md` wins on conflicts. This doc
> extends it (§13 non-goal + §15 v2.0 roadmap).

---

## 0. Goal & non-negotiable constraints

Turn the vault from "tag co-occurrence + manual wikilinks" into a **typed,
temporal knowledge graph** so retrieval returns *connected, meaningful
knowledge* — not flat one-off facts.

Three axes the graph must capture:
1. **Extraction quality** — reliably pull entities/relations from short drops.
2. **Real-world grounding** — where a fact sits in world knowledge, how it connects.
3. **Personal-world placement** — where it sits in *the user's* knowledge, how
   important it is to them, what it connects to.

**Hard constraints:**
- **Cheap model only:** all extraction runs on **Gemini 2.5 Flash** (mid tier),
  sometimes Flash-Lite. No frontier model in the loop.
- **git is the store:** index committed as files in the vault. **No vector-DB,
  no graph-DB service, no new credential.**
- **Language-agnostic by default.** No single-language tooling in the core path.
- **$0 / free-tier budget**, lightweight deps (optional extras only).

**Guiding principle (architecture.md §0):** *quality through architecture, not
larger models.* The cheap model **proposes**; quality is guaranteed by
**schema constraint + deterministic resolution + PPR + the human (approve)** —
not by self-evaluation.

---

## 1. Why now — this is the planned v2.0

`architecture.md` deliberately deferred this and named the trigger:
- **§13 Non-Goals:** "Typed knowledge graph with entity extraction… **graph
  becomes useful past ~2000 entries**"; "Vector database… long context + grep +
  MEMORY.md covers personal scale."
- **§15 v2.0:** "**Bi-temporal metadata (`valid_from`/`valid_until`), YAML-level
  supersession linking.**"
- **§14** already cites **Zep/Graphiti** (arXiv:2501.13956) and **A-MEM**
  (arXiv:2502.12110, "−6 F1 when supersession removed").

At ~30k chunks (the projected scale) we cross that threshold. This design
**evolves existing primitives** into the v2.0 graph — it does **not** bolt on a
foreign GraphRAG stack.

---

## 2. Locked decisions

| # | Decision | Rationale |
|---|---|---|
| 1 | **Ontology:** 5 node types (`concept`, `log`, `project`, `person`, `source`) + 4 LLM-extracted relations (`supports`, `contradicts`, `elaborates`, `part_of`). `map`/MOC = derived/human overlay, not extracted. | Small closed schema ⇒ less cheap-model hallucination ("Are LLMs Effective KG Constructors?", arXiv:2510.11297); memorizable; matches existing kinds. |
| 2 | **Gate by edge *type*, not numeric confidence.** Additive edges auto-accept; semantic-destructive (`contradicts`/supersession) gate. | Zep/Mem0/Penfield auto-accept all + resolve by temporal invalidation; verbalized confidence is poorly calibrated on cheap models. |
| 3 | **Auto-accept + `git revert` undo** for additive edges; **approve-first only** for semantic-destructive. | Git = audit log, not gate; no external side effects ⇒ commit never irreversible. Approval fatigue is real (SOC ignore-rate 67%); sustainable human load = 10–15% / 3–8 decisions per day. |
| 4 | **Write-time provisional extraction + daily batch consolidation**, gate items collected into **one daily Telegram digest**. | ATOM/Zep/Mem0 de-facto standard; matches monogram morning(daily)+weekly rhythm; budget ≈ 77/500 Flash RPD. |
| 5 | **Supersession = bi-temporal edge invalidation, never delete.** | Graphiti's exact model; architecture.md §7 "never delete, git history"; A-MEM. |
| 6 | **Trust via a cheap KG-eval loop on the existing cassette harness.** | Must trust Flash output ⇒ continuous offline F1/adherence + monthly cross-family hallucination judge (KGGen/MINE). |
| 7 | **Extraction = 2-pass schema-locked, evidence-quote required, abstention field.** Entity resolution = rule + embedding cosine ≥ 0.85 (reuse vectors), LLM only for ambiguous. Run on **Flash (mid)**. | KGGen 2-pass beats GraphRAG on MINE (66% vs 48%); Flash-Lite too weak for relations. |
| 8 | **Retrieval = HippoRAG-style:** vector seed → Personalized PageRank → blended score → MMR → connected neighborhood with labeled edges. | GraphRAG *global* is cost-prohibitive at personal scale; HippoRAG PPR is in-process, no per-query LLM, multi-hop. |
| 9 | **Storage:** sharded vector JSONL + bi-temporal `edges.jsonl`/`entities.jsonl`, incremental (blob-sha), 8 MB/shard append-mostly. Never re-summarize note bodies on reindex. | Git-history hygiene; model-collapse avoidance. |

New deps (optional extras only): `mono-gram[semantic] = numpy, scipy`. No service, no credential, no Node.

---

## 3. Ontology

**Node types (5)** — `type:` in frontmatter / entity record:
`concept` (atomic wiki note) · `log` (daily/life entry) · `project` ·
`person` · `source` (paper/book/link/repo). `map` (MOC hub) is authored or
community-derived, not LLM-extracted.

**Edges — two tiers:**
- **Tier 1, semantic (LLM-proposed; destructive ones gated):**
  `supports`, `contradicts`, `elaborates`, `part_of`.
- **Tier 2, structural (auto, derived — no LLM):**
  `mentions` (from `[[wikilinks]]`), `tags` (tag overlap, IDF/hierarchy-weighted),
  `semantic` (embedding kNN, cosine ≥ 0.85), `authored_by`/`related_to_project`/
  `cited_in` (from frontmatter).

**Supersession** is *not* a 5th extracted relation — it is the **temporal
mechanism** triggered when a new fact `contradicts` an existing edge (see §5).

**Tags:** reserved for **status** (`#status/seed|growing|evergreen`) and
**domain** (`#domain/...`, hierarchical) only. Topics become `[[concept]]`
nodes, **not** free tags. Ontology seeds from the user's `identity/SCHEMA.md`.

---

## 4. Extraction (write-time, Flash 2-pass)

Runs after the existing pipeline's content extraction, on **Flash (mid)**,
`temperature=0`, `response_schema` enum-constrained:

```
pass 1  entities:  [{name, type∈5, canonical(lowercased, normalized)}]
                   — only entities explicitly present; else []
pass 2  relations: given the entity list →
                   [{subject, predicate∈4, object, evidence, uncertain}]
                   — evidence = a quote from the note (no quote ⇒ drop the edge)
                   — uncertain=true ⇒ abstain (route to review, don't write)
resolution (no LLM):  lowercase/exact dedup → same-type cosine ≥ 0.85 merge
                      (reuses the vector index); LLM only for ambiguous pairs.
optimization:         skip the resolution LLM call when best candidate sim < 0.7.
```

- **No open-ended self-critique** (cheap models can't self-evaluate). The
  *binary evidence-grounding* check ("is this quote in the note?") is the only
  verification and is cheap/reliable.
- **Free Tier-1 source:** the existing `Verifier.contradictions`
  (`existing_path`, `severity`) is promoted to a `contradicts`/supersession
  candidate (already computed — no extra call).
- Personal logs / `log` nodes also get entities/tags so they aren't graph
  orphans.

---

## 5. Temporal graph model (bi-temporal, close-don't-delete)

Each edge is a JSON line in `index/graph/edges.jsonl`:

```json
{
  "id": "uuid", "subject": "...", "predicate": "supports", "object": "...",
  "fact": "...", "evidence": "...", "source_path": "daily/2026-05-01/drops.md",
  "status": "confirmed",
  "valid_at":   "2026-05-01T00:00:00Z",   "invalid_at": null,
  "created_at": "2026-05-01T09:00:00Z",   "expired_at": null
}
```

Two timelines (Graphiti model):
- **valid time** `[valid_at, invalid_at)` — when the fact held in the world.
- **transaction time** `[created_at, expired_at)` — when the system believed it.
  (`created_at` can be the git commit time — free.)

**On contradiction** (new fact contradicts an existing edge):
1. extract new edge + its `valid_at`;
2. retrieve semantically-similar existing edges (embedding search over `fact`);
3. Flash binary: "does new contradict any of these?";
4. for each contradicted edge: `invalid_at = new.valid_at`, `expired_at = now`;
   **append** the new edge — **never delete** the old one.

Current facts = `WHERE invalid_at IS NULL AND expired_at IS NULL`. Historical
("what did I believe on date X") = fix either axis. Aligns with architecture.md
§7 ("never delete; git history is the audit trail") and the §15 v2.0 roadmap.

No time-decay on committed knowledge (beliefs don't lapse because unmentioned);
decay stays for the short-term / inbox tier only (architecture.md §6).

---

## 6. Governance — auto vs gate

| Edge class | Examples | Handling |
|---|---|---|
| Additive / structural | `mentions`, `tags`, `semantic`, `supports`, `elaborates`, `part_of` | **Auto-accept**, commit, `git revert` undo. No gate. |
| Semantic-destructive | `contradicts`, supersession | **Gate** via the existing weekly-lint Telegram **approve/reject**, batched into **one daily digest**. `status: proposed` until approved. |
| Hard-destructive | merge / deprecate nodes | **Manual only**; surface in the digest, never auto-propose. |

Anti-noise rules:
1. **No embedding-only edges** — every proposed edge cites a note quote.
2. **Proposed edges expire** after 30 days if unconfirmed.
3. **Degree cap** on auto `mentions` (≤ ~10/node) — generalizes the current
   `wiki_backlinks` cap-5; prevents hub explosion.
4. **Type-before-link** — classify both endpoint node types before proposing.

This is monogram's existing philosophy applied to edges (§11 "nothing
auto-merges without approval"; §13 "automatic contradiction resolution is a
non-goal — user decides").

---

## 7. Retrieval (HippoRAG-style, in-process)

```
offline (reindex):  vector shards + edges/entities + node→passage matrix P
                    + global PageRank (centrality) + per-note recency/freq
query:
  1. qv = embed(query)
  2. seeds = top-k passage hits ∪ top-k entity hits  (IDF-weighted: s_i = |P_i|^-1)
  3. Personalized PageRank over the typed graph  (α = 0.5, scipy sparse, <50 iter)
  4. score(d) = 0.5·cosine(d,qv) + 0.3·PPR(d) + 0.2·importance(d)
        importance = 0.25·centrality + 0.35·exp(-0.02·Δedit_days) + 0.25·freq + 0.15·pin
  5. MMR (λ=0.5) over top-30 → diverse top-8
  6. assemble 1-hop neighborhood: the 8 + bridge nodes + the typed edges between
     them; filter to current facts (invalid_at IS NULL)
return:  a labeled neighborhood ("X —supersedes→ Y", "X —#domain→ Z"), not a flat list
```

- α=0.5 (high restart) keeps the walk near the query — right for a small dense
  personal graph (HippoRAG).
- ~30k nodes / ~200k edges → PPR < 50 ms; cosine < 20 ms; **no per-query LLM**.
- `--semantic` = seed + 1-hop neighborhood; `--graph <slug>` = 2-hop exploration.

GraphRAG *global* search (per-community LLM map-reduce) is **rejected** — cost-
prohibitive at personal scale. Community detection (Leiden/Louvain) is used only
offline for MOC candidates + structural-gap surfacing.

---

## 8. Storage & sharding

```
index/
  vec/<area>-NNN.jsonl     # {path, chunk, heading, excerpt, sha, vec(f16-base64)}
                           #   area + 8 MB cap, append-mostly (only tail shard churns)
  graph/edges.jsonl        # bi-temporal edges (§5)
  graph/entities.jsonl     # canonical entities + merged summary + embedding
  manifest.json            # shard list, sha, size, last reindex
```

- **Chunking:** one note = one chunk (don't split short notes); prepend a
  1-sentence context header before embedding (contextual retrieval).
- **Incremental:** compare each file's current blob sha vs stored `sha`;
  re-embed/re-extract only changed files; append-mostly so weekly reindex
  rewrites only small tail shards → git history stays lean.
- **8 MB cap** ≈ 4k chunks/shard; far under GitHub's 50 MB warn / 100 MB reject.
- **Never re-summarize note bodies on reindex** (model-collapse); only re-embed
  and re-extract edges.

---

## 9. Credential safety (invariant)

Reindex and extraction go through `safe_read` (hard-blocks `life/credentials/`)
and run `secret_filter.redact()` on **excerpts, embedding input, and evidence
quotes**. No credential nodes/entities ever enter the graph. (Same multi-layer
posture as the Writer chokepoint.)

---

## 10. Trust — cheap KG-quality eval loop

Extends the existing cassette-replay harness (`evals/`):
- **Gold set:** 30 notes × ~10 verified triples (~3 h one-time). Stratified by
  drop type. Stored `evals/fixtures/kg_extraction.jsonl` + recorded cassettes.
- **Offline (CI, $0):** entity F1 (>0.75), relation F1 (>0.65), schema-adherence
  (>0.95) against cassette replays.
- **Monthly:** hallucination rate (<0.10) — judge ~50 sampled production triples
  with a **cross-family** model (e.g. GPT-4o-mini), CoT-forced, formatting
  normalized (style bias is the dominant confound). ≈ $0.01/run.
- **Abstention:** `uncertain` field; auto-accept only in-schema + evidence-
  grounded; route the rest to review. Recalibrate on each cassette re-record.

---

## 11. Reuse of existing monogram assets (minimize reinvention)

| Existing | Graph role |
|---|---|
| `MEMORY.md` pointer index (§9) | the graph-lite original → evolves into the typed graph |
| `confidence` enum + decay + `last_accessed`/`last_confirmed` (§6, §8) | importance / recency signals — already present |
| Supersession + git history (§7) | basis for temporal edge invalidation |
| weekly-lint Telegram approve/reject (§11) | the HITL gate channel for destructive edges |
| `identity/SCHEMA.md` (§1) | ontology seed (user domain schema) |
| `Verifier.contradictions` | free `contradicts`/supersession candidates |
| cassette harness (`evals/`) | KG-quality eval loop |
| `wiki_backlinks` (tag-overlap, cap-5) | generalized into the Tier-2 edge builder |
| `secret_filter` + `safe_read` | credential safety on the index |
| `_unlabeled/` + `/approve_<token>` | review queue + approval primitives |

---

## 12. Implementation phases

- **P0 — ingestion (DONE, local):** PDF = PyMuPDF4LLM native text (no OCR,
  language-agnostic, mirrors google-surf-mcp/unpdf); HWP/HWPX = rhwp-python
  direct (`is_hwp` dispatch); document **attachment wiring** in the listener
  (PDF/HWP/Office bytes → extract → drop text). Prerequisite — the graph is only
  as good as what extraction feeds it.
- **P1 — vector store + sharding + incremental reindex + plain `--semantic`.**
  Dep: `numpy`. Language-agnostic core; independent of the graph layer.
- **P2 — Flash 2-pass graph extraction + entity resolution →
  `edges.jsonl`/`entities.jsonl`** (+ Verifier-contradiction promotion).
- **P3 — governance:** type-based auto/gate, daily digest, `_unlabeled/` review,
  tag→wikilink *suggest-only* migration, anti-noise rules.
- **P4 — HippoRAG retrieval:** PPR + importance + MMR + neighborhood render;
  `/search`. Dep: `scipy`.
- **P5 — community detection (MOC candidates) + graph-health lint (weekly) +
  KG-eval gold set + α/β tuning.**

---

## 13. Research basis (citations)

**Extraction / cheap-model KG:**
- GraphRAG — arXiv:2404.16130 (gleaning, communities, exact-match entity merge)
- KGGen / MINE — arXiv:2502.09956 (2-pass entities→relations; 66% vs 48%)
- "Are LLMs Effective KG Constructors?" — arXiv:2510.11297 (smaller schema ⇒ higher reliability)
- LlamaIndex Property Graph (`SchemaLLMPathExtractor strict=True`), Neo4j LLM Graph Builder (EXTRACTED schema mode)
- Lettria text-to-graph benchmark (Gemini Flash-tier reliability); Gemini structured-output caveats (dylancastillo.co)

**Temporal / memory:**
- Zep / Graphiti — arXiv:2501.13956 (bi-temporal edges; `valid_at`/`invalid_at`/`created_at`/`expired_at`; close-don't-delete; MinHash/LSH + LLM entity resolution)
- Mem0 — arXiv:2504.19413 (ADD/UPDATE/DELETE/NOOP); ATOM — arXiv:2510.22590 (write-time + batch consolidation); MemGPT/Letta (memory tiers)
- A-MEM — arXiv:2502.12110 (supersession links; −6 F1 without)

**Retrieval / ranking:**
- HippoRAG — arXiv:2405.14831 + HippoRAG 2 — arXiv:2502.14802 (PPR α=0.5, IDF seeds, node-passage matrix)
- "When to Use Graphs in RAG" survey — arXiv:2506.05690 (HippoRAG ≫ GraphRAG cost at small scale)
- MMR (Carbonell & Goldstein); `fast-pagerank`; InfraNodus (betweenness centrality for PKM importance); Ebbinghaus forgetting curve (recency decay)

**Organization / governance / eval:**
- Penfield Labs typed wikilinks; Tana supertags; Zettelkasten/MOCs (dsebastien); folksonomy↔ontology migration risk (ResearchGate)
- MindStudio risk tiers; Galileo HITL oversight; Changkun approval-fatigue; Freestyle (version-control-for-agents → revert beats pre-approval)
- "Judging the Judges" — arXiv:2604.23178 (cross-family judge, CoT, style-bias); abstention survey (MIT TACL); CONSTRUCT — arXiv:2603.18014 (per-triple evidence grounding)

---

## 14. Open / deferred

- **Tag→wikilink migration:** approach locked (suggest-only, never rewrite note
  bodies, rollback = delete concept files); the *one-time triage* is opt-in.
- **Importance weights (w₁/w₂/w₃, β's, α):** start at the values above; tune on
  the gold set.
- **google-surf-mcp** as an *optional* high-power ingestion/web-search backend
  (Playwright + unpdf) — separate from this vault-internal graph; not a core dep.
- **HWP tables/images:** rhwp text extraction only; structured tables via its IR
  API deferred.
