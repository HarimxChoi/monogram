# Monogram Vault Layout

Monogram stores everything as Markdown in a git repo. This doc describes
how those files are organized on disk and why.

## The 2×3 grid

Every file in the vault belongs to exactly one cell of a two-dimensional grid.

```
                 SOURCES              STATE              DERIVED
                 (what comes in)      (what you maintain) (what gets generated)
  ──────────────────────────────────────────────────────────────────────────
  TEMPORAL       daily/*/drops.md     —                  daily/*/report.md
  (date-indexed) daily/*/convos.md                       reports/weekly/
                 daily/*/commits.md
  ──────────────────────────────────────────────────────────────────────────
  STABLE         —                    wiki/              MEMORY.md
  (path-indexed)                      projects/          wiki/_categories.json
                                      identity/
```

Rows answer *how is this indexed*: by date (temporal) or by concept/project
name (stable). Columns answer *what role does it play*: raw input (sources),
maintained truth (state), or computed output (derived).

The empty cells are deliberate. There is no "temporal state" — state is
indexed by identity and moves through time via supersession, not by sitting
in a date folder. There is no "stable source" — every source is an event
with a timestamp.

## Directory tree

```
mono/                            (= your Obsidian vault, = a git repo)
│
├── MEMORY.md                    navigation index, always loaded by the agent
├── identity/                    user-maintained behavior + schema docs
│
├── daily/                       temporal sources + per-day reports
│   └── 2026-04-17/
│       ├── drops.md
│       ├── conversations.md
│       ├── commits.md
│       └── report.md
│
├── wiki/                        stable state: compiled knowledge
│   ├── _categories.json
│   ├── _unlabeled/
│   └── <Category>/*.md
│
├── projects/                   stable state: active projects
│   └── *.md
│
├── reports/                     derived: multi-day rollups
│   └── weekly/YYYY-Www.md
│
├── mono/                        agent config
│   └── config.md
│
└── log/                         system telemetry
    └── pipeline.jsonl
```

## Daily folder (temporal, sources)

Every day has its own folder at `daily/YYYY-MM-DD/` containing four files:

- `drops.md` — things the user sends in (URLs, notes, images), one entry
  per event with timestamp, type, classification outcome, and downstream writes.
- `conversations.md` — bot chat transcripts. Private by default; excluded
  from reports unless a turn is explicitly tagged `#keep`.
- `commits.md` — GitHub activity pulled from watched repos, aggregated
  into digests (default every 6 hours).
- `report.md` — derived summary generated once the day is sealed.

All four files are append-only during the day. At 23:59 local the day is
sealed: `report.md` is generated and the whole folder becomes immutable.

Filename convention inside `daily/` is fixed — every date folder has the
same four filenames. The path itself is the only varying part.

## Wiki (stable, state)

`wiki/` holds compiled knowledge organized by category. Each entry is a
single Markdown file with YAML frontmatter.

Path shape: `wiki/<Category>/<slug>.md`. Slugs are kebab-case,
lowercased, ASCII-only. Categories are PascalCase folders like `ML-CV`,
`Infra`, `Business`.

Entries are *claims* — assertions about the world that carry a confidence
level and can be superseded when better information arrives. When an
entry is superseded, the old file stays on disk (with `superseded_by` set
in frontmatter) so the audit trail is preserved; the new file links back
via `supersedes`.

Two special locations:

- `wiki/_categories.json` — auto-maintained map of category names to
  keywords, entry counts, and last-used dates. Drives classification.
- `wiki/_unlabeled/` — low-confidence drops that couldn't be classified
  cleanly. Named `YYYY-MM-DD-<slug>.md`. Surfaced in the weekly report
  for the user to triage.

Reference material (papers, external docs) lives under `wiki/_refs/YYYY/`
indexed by year of publication.

## Projects (stable, state)

`projects/<name>.md` tracks the working state of each active project:
phase, blockers, next actions, deadlines. One file per project.

Unlike wiki entries, project files are *mutable in place* — updates
overwrite the file. History lives in git commits, not in superseded
copies on disk. Project entries are state, not claims; there's no
confidence level, no supersession chain.

A project file covers the full lifecycle: active projects stay in
`projects/`, and the same file tracks the transition to inactive
(no-op for N days) or done.

## Reports (temporal, derived)

Two reports exist:

- **Morning brief** at `daily/YYYY-MM-DD/report.md`. Generated 08:00
  local, summarizing the day just finished. Reads the previous day's
  drops, commits, and any wiki/projects changes (via `git log`).
  Covers: what got done, what was learned, decisions made, cost/usage
  metrics, and calendar events detected in drops (surfaced as
  one-click Google Calendar URLs, never auto-created).
- **Weekly rollup** at `reports/weekly/YYYY-Www.md`. Generated Sunday
  21:00 local, covering Monday–Sunday. Reads the original daily folders
  of the past seven days — not the morning briefs — to avoid compounding
  information loss.

Monthly and yearly rollups don't exist. The weekly is the coarsest
aggregation; anything longer-horizon is a git query.

**Retention.** The live `daily/` window is 67 days (nine full weeks plus
a buffer day at each end). After the weekly report writes, the archival
sweep moves any daily folder older than the window out of `daily/` into
cold storage. Calendar-aligned: a week is only archived as a complete
Monday–Sunday unit. `reports/weekly/` is kept live indefinitely.

## Write atomicity

Every agent write is a **single commit that spans all affected grid cells**,
created through the GitHub Git Tree API — Monogram builds the full tree
of updated files, posts it as one tree object, and creates one commit
pointing to it. There is no `git add` / `git commit` sequence that can
be interrupted.

A typical drop touches up to five paths in one commit:

```
always:         daily/YYYY-MM-DD/drops.md
                log/pipeline.jsonl
conditionally:  projects/<name>.md                (project update)
                wiki/<Category>/<slug>.md         (wiki entry, high/medium conf)
                wiki/_unlabeled/<date>-<slug>.md  (wiki entry, low conf)
                MEMORY.md                          (if stable state changed)
                wiki/_categories.json              (if category counts changed)
```

Why it matters: the vault is a small distributed system — the agent
writes, the user reads in Obsidian, the user edits, the agent reads back
on the next turn. A partial write would leave the wiki pointing at a
project update that doesn't exist, or a `_categories.json` count that
disagrees with the actual file tree. One tree, one commit means the grid
is always consistent at every SHA. If any part of the drop pipeline
fails, nothing commits and the vault stays at the previous SHA.

Git also serves as the temporal index for stable state. "What changed
yesterday" is `git log --since=YYYY-MM-DD --name-only`. Reports use this
to enumerate wiki and project changes. The repo must not be shallow-
cloned and history must never be rewritten.

## Config

Agent configuration lives at `mono/config.md`. It's a Markdown file with
YAML frontmatter — the frontmatter holds settings, the body is free-form
notes for the user.

What lives there: watched GitHub repos, GitHub digest window, Telegram
bot identifiers, timezone for the morning/weekly jobs, LLM model choices,
budget caps, category overrides. One file, one source of truth.

## Log

`log/pipeline.jsonl` is the system telemetry stream. One JSON object per
line, append-only. Each line captures a single pipeline run: timestamp,
drop ID, classification outcome, per-stage LLM calls with token counts
and costs, files written, and any error state.

This is the audit trail for *how the agent behaved*, separate from the
vault content itself. Reports pull metrics from it (calls per day,
cost per week). It's not user-facing content — no Obsidian styling, no
wiki links — just structured events for debugging and accounting.
