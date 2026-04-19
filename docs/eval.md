# Monogram Eval Harness

## What it does

The eval harness is a cassette-replay test suite for Monogram's 5-stage pipeline
(orchestrator → classifier → extractor → verifier → writer). It records real LLM
responses once, commits them to the repo, and replays them on every CI run — so
the test suite costs zero LLM tokens and produces deterministic results.

On top of replay, the harness runs two optional tracks:

- **Track A (harvest)** — scans production pipeline logs, anonymizes them, and
  grows the fixture set so evals stay representative of real usage.
- **Track B (few-shot injection)** — feeds approved, harvested examples back into
  the classifier's system prompt, gated behind a flag.

Both tracks are opt-in. The default install runs replay only.

## Quickstart

```bash
pip install -e '.[eval]'
pytest evals/
```

That runs the full replay suite against the committed cassettes. Zero network
calls, zero API cost.

## Architecture

The harness is structured around three principles:

**Cassette replay.** `evals/cassette.py` patches `litellm.acompletion` and
routes each call to a per-agent JSON file (`evals/cassettes/{agent}.json`)
keyed by a SHA-256 of the canonical request. A `current_agent_tag` ContextVar
in `src/monogram/llm.py` lets the shim know which agent made the call without
inspecting prompt text. Each agent passes its tag explicitly
(`agent_tag="classifier"` etc.) — refactor-safe, git-visible.

**Non-collision.** `src/` never imports from `evals/` on the hot path. This is
enforced in CI by `scripts/check_no_evals_import.py`, which greps for
`from evals` or `import evals` inside `src/` and fails the build on any match.
The only shared surface is data: JSONL files in the vault.

**Kill switches everywhere.** Three independent layers (env var, vault config,
CLI flag) can disable the harness or the few-shot injection. See
[Kill switches](#kill-switches).

```
evals/
├── cassette.py       # record/replay shim + per-agent routing
├── capture.py        # memory-backed substitute for github_store
├── harvest.py        # Track A: pipeline.jsonl → anonymized fixtures
├── anonymizer.py     # 4-layer PII scrubber
├── fixtures.py       # JSONL loader
├── conftest.py       # pytest session fixtures + CLI flags
├── kill_switch.py    # env/config precedence checker
├── cli.py            # `monogram eval *` subcommands
├── test_*.py         # classification, extraction, safety, escalation suites
├── fixtures/         # JSONL, git-committed
└── cassettes/        # per-agent JSON, git-committed
```

## Running tests

Default is replay mode, which is what CI runs:

```bash
pytest evals/                        # replay only, offline
pytest evals/ --record               # force re-record; burns API quota
pytest evals/ --auto-record          # replay hits, record misses
pytest evals/ -k classification      # narrow to one suite
```

A cassette miss in replay mode is a hard failure. During fixture development
you can soften that to a skip:

```bash
MONOGRAM_EVAL_MISS_SKIP=1 pytest evals/
```

`--record` is serialized automatically (xdist `numprocesses=0`) so concurrent
workers don't race to write the same cassette file.

## Fixtures

Fixtures are JSONL, one drop per line, organized by category:

```
evals/fixtures/
├── projects.jsonl        # project-kind drops
├── wiki.jsonl            # wiki-kind drops
├── life.jsonl            # personal-slug drops
├── credentials.jsonl     # API keys / secrets — must redact
├── daily_only.jsonl      # daily-log-only cases
├── escalation.jsonl      # cases that should escalate
└── edge_cases.jsonl      # injection attempts, malformed input
```

Every fixture passes through a 4-layer anonymizer before being committed:
known-slug replacement, regex scrubbing (name, email, phone, API-key shapes),
optional spaCy NER for Korean + English entities, and a residual-PII guard
that fails the harvest cycle if anything recognizable slips through. The
anonymizer has its own adversarial test suite in `evals/test_anonymizer.py`.

## Kill switches

Three independent layers, checked in precedence order:

1. **Don't install the extra.** `pip install -e .` without `[eval]` leaves the
   eval directory uninstalled. Zero overhead; the `monogram eval` subcommand
   refuses to load.
2. **Env var.** `MONOGRAM_EVAL_DISABLED=1` hard-disables the harness regardless
   of config. Use when the vault is unreachable.
3. **Vault config.** Set `eval_enabled: false` in `mono/config.md`. Persists
   across restarts; scheduled cron and bot `/eval_*` commands will refuse.

A fourth, independent sub-switch turns off just the Track B classifier
few-shot without disabling anything else:

```bash
monogram eval disable-few-shot
```

`monogram eval status` prints the effective state of all four layers.

## Contributing a fixture

1. Add one JSONL line to the appropriate file under `evals/fixtures/`. See
   existing rows for the schema (`id`, `input`, expected `target_kind`, `slug`,
   etc.). Double-check it contains no real names, URLs, or identifiers.
2. Record its cassette: `pytest evals/ --record -k <your-fixture-id>`. This
   calls the real LLM once and writes to `evals/cassettes/`.
3. Run the replay suite to confirm: `pytest evals/`.
4. Commit the fixture and the cassette diff together.

Credentials fixtures have an extra bar: they must round-trip through the
verifier with the secret fully redacted. A regression on any credential
fixture blocks merge.

## CI

`.github/workflows/eval.yml` defines four jobs:

- **non-collision-check** (every push): greps `src/` for `evals` imports;
  fails the build on any match.
- **replay-tests** (every push): `pytest evals/` in replay mode. No secrets,
  no network.
- **kill-switch-smoke-test** (every push): three tests confirming each
  disable layer works independently.
- **harvest** (scheduled, Sun + Wed 03:00 KST): pulls recent pipeline logs,
  runs the anonymizer, and posts proposed new fixtures to Telegram for
  approval. Respects the kill-switch chain.

The harvest job uses a PAT scoped only to reading `log/pipeline.jsonl` and
`config.md` and writing `.monogram/harvest-pending/*`, separate from the
production PAT.
