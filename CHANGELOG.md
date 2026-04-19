# Changelog

All notable changes to Monogram are documented here.

This project follows [Semantic Versioning](https://semver.org/). Dates
are in KST (UTC+9).

## [Unreleased] — v0.8 (full buildout)

This release consolidates the v0.8+v0.9+v1.0 scope from the original
roadmap into a single version, reflecting a ship-all-now decision after
the v0.7 eval harness landed.

### Packaging

- **Pip distribution name is now `mono-gram`.** The name `monogram` is on
  PyPI's stopwords/denylist, so the package ships as `mono-gram`. CLI
  command (`monogram ...`), Python import path (`from monogram import ...`),
  and GitHub repo name all remain `monogram`. Install: `pip install mono-gram`.

### Added

- **Ingestion pipeline** (`src/monogram/ingestion/`):
  - Dispatcher: routes URLs to appropriate extractor
  - YouTube extractor (transcript via `youtube-transcript-api` v1.x API;
    Whisper fallback opt-in)
  - arXiv extractor (`arxiv` library + Semantic Scholar enrichment)
  - PDF extractor (PyMuPDF4LLM fast path; Marker fallback for complex
    layouts; `ingestion-pdf-complex` extra)
  - Web page extractor (trafilatura-based, refactored from listener)
  - Raw tier: immutable audit copy at `raw/YYYY-MM-DD-<source>-<slug>.md`
- **Atomic writes** (`github_store.write_atomic`): single-commit
  multi-file writes via Git Tree API with retry loop on 422 ref-update
  conflicts (concurrent write safety)
- **Backup flow** (`src/monogram/backup.py`):
  - `monogram backup mirror` — manual nightly mirror to
    `<your-github-user>/mono-backup`
  - `monogram backup verify` — restore-drill against backup repo
  - Monthly CI job invokes verify
- **Search** (`src/monogram/cli_search.py`): ripgrep subprocess with
  Python regex fallback. Scopes: `--kind wiki`, `--since 30d`, `--raw`.
- **Observability helpers**: `pipeline_log.py` enhanced with per-stage
  latency buckets; `monogram eval baseline --save` now auto-computes
  p50/p95/p99 from existing log data.
- **`monogram migrate`**: one-shot migration from v0.6 vault schema
  (writes `eval_enabled: false` by default for existing users)
- **Kill-switch startup log**: `monogram run` emits effective eval state
  + which layer set it, at service start
- **Dependabot**: weekly Python dep updates, monthly GitHub Actions
- **PyPI publish workflow**: Trusted Publishing (OIDC), auto-attestations,
  environment gate (`environment: pypi`), pending-publisher-ready
- **Docs**:
  - Full `README.md` (was 1-line stub)
  - `LICENSE` (MIT)
  - `SECURITY.md` (vulnerability disclosure)
  - `CONTRIBUTING.md` (solo-maintainer SLAs)
  - `CODE_OF_CONDUCT.md` (Contributor Covenant)
  - This file (`CHANGELOG.md`)

### Changed

- `listener.py::handle_drop`: extracts URLs before pipeline, enriches
  drop text with extracted content + raw-tier reference
- Python 3.10 added to CI matrix (was: 3.11 + 3.13 only)
- `docs/eval.md` corrections:
  - arXiv rate limit corrected to "1 request per 3 seconds per arXiv ToU"
    (was: "3 requests/second default" — inverted)
  - Git Tree API arithmetic: N+3 API calls per atomic commit (was: 3N)
  - `runlog.py` path: `log/runs/YYYY-MM-DD-<job>.md` (was: `log/runs.jsonl`)
  - `attestations: true` marked as default/implicit (was: explicit config)

### Security

- `.gitignore` audited: `*.session` (Telethon), `*.session-journal`,
  `.env`, `gcp-sa.json` all confirmed present
- `.env.example` audited: placeholder-only, no real PATs or keys
- GitHub Actions workflows use `id-token: write` per-job (principle of
  least privilege)

## [0.7.0] — 2026-04-18

Open-source launch readiness. Eval harness landed; kill-switch
architecture operational.

### Added

- **Cassette-replay eval harness** (`evals/`):
  - Per-agent cassette routing via `monogram.llm.current_agent_tag`
    ContextVar (D1-A)
  - 50 seed fixtures across 7 categories (projects, wiki, life,
    credentials, daily_only, escalation, edge_cases)
  - 155 eval tests
  - CI workflow: non-collision-check, replay-tests,
    kill-switch-smoke, scheduled harvest (Sun+Wed 03:00 KST)
- **Track A harvest**: production `log/pipeline.jsonl` → anonymized
  fixtures via 4-layer scrubber. Telegram approval gate with 24h TTL.
- **Track B classifier few-shot** (gated, flag off by default): approved
  harvest entries feed into classifier prompt when
  `classifier_few_shot_enabled: true`
- **Three-layer kill-switch**:
  1. Don't install `.[eval]` → no `monogram eval` subcommand
  2. `MONOGRAM_EVAL_DISABLED=1` env var → hard override
  3. `eval_enabled: false` in `mono/config.md` → normal user control
- **`bot_eval_cmds.py`**: `/eval_status`, `/eval_enable`, `/eval_disable`,
  `/eval_enable_few_shot`, `/eval_disable_few_shot`
- **Docs**: `docs/eval.md` eval harness reference

### Infrastructure

- `pyproject.toml`: added `[eval]` and `[eval-nlp]` optional-dependencies
- Production edits minimal and reversible: `llm.py` (+ContextVar kwarg),
  4 agents (+1 line each for `agent_tag=`), `vault_config.py` (+flags),
  `cli.py`/`bot.py` wiring
- Zero changes to `pipeline.py`, `github_store.py`, `writer.py`,
  `morning_job.py`, `weekly_job.py`, `mcp_*`, `queue_poller.py`

## [0.6.0] — 2026-04-17

Web UI — password-protected client-decrypted dashboard. Three backends
(GCS, self-host, mcp-only).

## [0.5.1] — 2026-04-16

Queue poller hardening (regex relaxation, delete semantics), telegram
approval token format unification (`secrets.token_urlsafe`).

## [0.5.0] — 2026-04-15

Obsidian quick-capture plugin + `queue_poller.py` for async drop
ingestion from Obsidian → Monogram.

## [0.4b] — 2026-04-14

MCP tool expansion: 5 read tools (`search_wiki`, `query_life`,
`get_morning_brief`, `current_project_state`, `get_board`) + 1
gated write tool (`add_wiki_entry`).

## [0.4a] — 2026-04-13

LLM pluggability via `vault_config.py`. Runtime-editable
`llm_provider` + `llm_models` in `mono/config.md`; no hardcoded
model strings in production code.

## [0.3b] — 2026-04-12

Language directive injection — LLM output respects
`vault_config.primary_language` (en / ko / etc) for narrative fields
while slugs/paths/enum values stay English.

## [0.3a] — 2026-04-10

Pipeline refactor: 5-stage agent architecture (orchestrator → classifier
→ extractor → verifier → writer) with Pydantic structured output at each
stage.

## [0.2.0] — 2026-04-08

Escalation mechanism: verifier can request classifier re-run on a more
capable model (`cheap` → `mid` → `pro` tier progression).

## [0.1.0] — 2026-04-06

Initial release. Telegram listener + pipeline + GitHub writer.
Phases A-E: listener → orchestrator → pipeline → writer → bot commands.

---

## Version conventions

- Dates in KST (UTC+9)
- [Unreleased] sections hold in-progress work on the current branch
- Pre-v1.0 MINOR bumps may contain breaking changes (documented in that
  entry's "Changed" section)
- Post-v1.0: strict SemVer; breaking changes only in MAJOR bumps
