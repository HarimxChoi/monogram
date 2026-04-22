

# Monogram

**Language:** [English](README.md) · [Korean](README.ko.md)

> A personal knowledge pipeline: Telegram shares and GitHub commits auto-organize into wiki, kanban, calendar, morning briefings, MCP, and an encrypted dashboard.

[![tests](https://github.com/HarimxChoi/monogram/actions/workflows/tests.yml/badge.svg)](https://github.com/HarimxChoi/monogram/actions/workflows/tests.yml)
[![eval](https://github.com/HarimxChoi/monogram/actions/workflows/eval.yml/badge.svg)](https://github.com/HarimxChoi/monogram/actions/workflows/eval.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

Anything you share to Telegram Saved Messages and anything you commit
on GitHub flows through a 5-stage LLM pipeline and lands as structured
markdown in a private GitHub repo — one atomic commit per drop. The
markdown then renders into an auto-generated, encrypted dashboard on GCP.

Your commits auto-organize into a Kanban. Your links become a wiki.
Your mornings arrive as a briefing, calendar events included. Same
markdown, three views — Obsidian, the dashboard, and MCP.

![Monogram dashboard — projects, wiki, life recent, commits](docs/images/dashboard.png)

Dark, information-dense, password-protected, client-side decrypted.
Hosted automatically on GCP free tier at $0 / month. Design reference:
[docs/design/webui-mockup.html](docs/design/webui-mockup.html).

<video src="https://github.com/user-attachments/assets/9f144500-535f-4c51-9cc0-e87aaa33498f" controls muted playsinline width="320"></video>

<!--
  ┌─────────────────────────────────────────────────────────────┐
  │  SHORT SLOT — replace the blockquote above with:            │
  │                                                             │
  │  Option A (inline GIF, autoplays on GitHub, ≤5 MB):         │
  │    ![30-second walkthrough](docs/images/short-demo.gif)     │
  │                                                             │
  │  Option B (clickable poster → YouTube Short):               │
  │    <a href="https://www.youtube.com/shorts/YOUR_ID">        │
  │      <img src="docs/images/short-poster.jpg"                │
  │           alt="30-second walkthrough" width="400"/>         │
  │    </a>                                                     │
  │                                                             │
  │  Option C (both — GIF inline + link to full Short):         │
  │    ![30-second walkthrough](docs/images/short-demo.gif)     │
  │                                                             │
  │    *Full walkthrough:                                       │
  │    [youtube.com/shorts/YOUR_ID](https://…)*                 │
  │                                                             │
  │  Target arc (15-30s):                                       │
  │    0-3s   phone: drop URL in Telegram Saved Messages        │
  │    3-10s  desktop: commit appears on GitHub                 │
  │   10-20s  browser: dashboard auto-updates with the drop     │
  │   20-30s  Claude Desktop: MCP query finds the same drop     │
  └─────────────────────────────────────────────────────────────┘
-->

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  INPUTS                                                      │
│    Telegram Saved Messages  ·  Obsidian plugin  ·  MCP       │
└────────────────────────┬─────────────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────────────┐
│  PIPELINE     (5 stages · per-stage latency logged)          │
│    Orchestrator → Classifier → Extractor                     │
│                           → Verifier → Writer                │
└────────────────────────┬─────────────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────────────┐
│  MARKDOWN  (git)                  BACKUP  (separate PAT)     │
│    <user>/mono          ⟶      <user>/mono-backup           │
└────────────────────────┬─────────────────────────────────────┘
                         │
       ┌─────────┬───────┴───────┬────────────┐
       ▼         ▼               ▼            ▼
   Morning    Weekly         Web UI       MCP server
    brief     rollup       (dashboard)  (Claude / Cursor)

┌──────────────────────────────────────────────────────────────┐
│  OBSERVABILITY         │  EVAL HARNESS       (optional)      │
│  log/pipeline.jsonl    │  cassette replay · harvest loop     │
│  /stats · CLI          │  3-layer kill-switch                │
└──────────────────────────────────────────────────────────────┘
```

Six horizontal planes. Inputs → pipeline → markdown/backup → consumer
surfaces. Observability and eval sit below, cross-cutting. Full
writeup: [docs/architecture.md](docs/architecture.md).

## Quickstart

Python 3.10+, a GitHub account, a Telegram account, one LLM API key
(Gemini free tier is sufficient). If you want the encrypted web
dashboard on GCS, have the `gcloud` CLI installed and `gcloud auth
login` done — the wizard takes it from there.

```bash
pip install mono-gram
monogram init            # interactive wizard — env, config, GCP bucket, all inline
monogram auth            # one-time Telegram auth
monogram run             # listener + bot (leave running)
```

> ⚠️ **PyPI approval pending.** `pip install mono-gram` won't resolve
> yet — install from source per
> [docs/setup/install-from-source.md](docs/setup/install-from-source.md):
>
> ```bash
> git clone https://github.com/HarimxChoi/monogram.git
> cd monogram
> python -m venv .venv && source .venv/bin/activate
> pip install -e .
> ```
>
> Everything after — `monogram init`, `monogram run`, all subcommands —
> works identically.

> The pip package is `mono-gram`; the CLI command remains `monogram`.
> The Python import path is also `monogram` — `from monogram import ...`.

Drop something into Saved Messages. Within seconds a commit appears on
your markdown repo. End-to-end walkthrough (GCP free tier → PyPI): **[deploying.md](deploying.md)**.

Optional extras:

```bash
pip install 'mono-gram[ingestion-all]'   # YouTube, arXiv, PDF, Office, HWP
pip install 'mono-gram[eval]'            # cassette-replay eval harness
```

## Web UI

One markdown, three ways to deploy the dashboard:

| Mode | Where it runs | When to pick it |
|---|---|---|
| **GCS** | Static bucket, client-side decrypt | Default. Bookmarkable URL, $0 at personal scale. Bucket + service account + IAM provisioned by `monogram init` via `gcloud`. |
| **Self-host** | Local Flask or any static host | Air-gapped / private network. |
| **MCP-only** | No web face — access via Claude Desktop / Cursor | Terminal-centric workflow. |

Password-protected. Content is encrypted at rest; the host only ever
holds ciphertext. Regenerated on morning / weekly runs. Setup:
[docs/webui.md](docs/webui.md) (~5 min).

## Runs on $0

Designed to run end-to-end on free tiers:

- GCP `e2-micro` always-free VM for the listener + cron jobs.
- GCS free tier for the encrypted dashboard — bucket, service account,
  and IAM policy are provisioned automatically by `monogram init` via
  the `gcloud` CLI.
- Gemini free tier for the LLM pipeline.

**No GPU required.** Use the free LLM API tier, or plug in a local
Ollama model if you'd rather keep inference on-device — Monogram has
no hardware floor.

**No PC required after setup.** First-time configuration runs on your
desktop (install, `monogram init`, one-time Telegram auth), then the
VM takes over. Drops flow phone → Telegram → markdown → dashboard with
nothing local running.

## What you get

- **Single-commit atomic writes** via GitHub Git Tree API. No partial state.
- **SSRF-hardened URL ingestion** — every hop validated, including CGNAT + cloud metadata ranges.
- **Credential safety by construction** — classifier-level discriminator + verifier gate.
- **Observability** — one JSONL line per run, p50/p95/p99 on demand, `/stats` on Telegram.
- **Backup isolation** — separate PAT + monthly restore drill in CI.
- **LLM pluggability** — Gemini / Anthropic / OpenAI / Ollama / custom, per-tier.
- **Eval harness** — cassette replay at zero LLM cost; harvest loop (off by default) grows fixtures from your real drops.
- **Kill-switch** — three independent layers, first match wins.

Each is a short section in [docs/](docs/).

## Commands

Three surfaces over the same markdown — use whichever's closest to hand.

**CLI** — `monogram --help` lists these; full per-stage behavior in
[docs/agents.md](docs/agents.md):

```
run · morning · weekly · digest · search · stats
backup · mcp-serve · eval · migrate
```

**Telegram bot** — on-demand reports and markdown queries from your phone.
Every command gates on `TELEGRAM_USER_ID`. Full reference:
[docs/setup/telegram.md §6 Bot commands](docs/setup/telegram.md#6-bot-commands).

```
/report  [YYYY-MM-DD]   morning brief (default: yesterday)
/weekly  [YYYY-Www]     weekly report (default: last Mon–Sun)
/digest  [Nh|Nd|Nw]     commit digest since N (default: 24h)
/search  <query>        fixed-string grep, credentials-path blocked
/last    [N]            N most-recent drops (default 10, max 50)
/stats                  pipeline health — p50/p95/p99 from log/pipeline.jsonl
```

**MCP server** — Claude Desktop / Cursor / OpenClaw. 13 tools across
reads + a gated write + LLM config. Setup:
[docs/setup/mcp-clients.md](docs/setup/mcp-clients.md); authoritative
tool list in [docs/mcp.md](docs/mcp.md).

## Ingestion

Drop URLs, PDFs, Office docs — they're extracted before the pipeline
sees them. Full table + fallback chain in
[docs/ingestion.md](docs/ingestion.md). HWP is hardened against
CVE-2024-12425/12426 and CVE-2025-1080; see [SECURITY.md](SECURITY.md).

## Credentials

Storing credentials in chat isn't a recommended way to manage secrets.
Still, if you end up dropping a password, an API key, or a personal ID
into Saved Messages, Monogram handles it as safely as it can. The
classifier tags it as a credential and isolates it under
`life/credentials/` — a path the LLM is code-level blocked from
reading. The content lives only in your private GitHub repo, and you
retrieve it by syncing that repo to Obsidian on a device you trust.

## What this is *not*

- Not a chat bot — no conversational turn-taking.
- Not a search engine — `monogram search` is grep + scope filters. Semantic search in v1.1.
- Not multi-user — one Telegram account, one markdown, one person.
- Not a replacement for Obsidian/Notion/Logseq — it's the ingest path. Your markdown renders natively in any markdown editor.

## Roadmap

- **v0.8 (current)** — core pipeline, ingestion, hardening, observability. Package live on PyPI as `mono-gram`; dogfood underway.
- **v1.0** — tag cut after dogfood wraps. KakaoTalk, LINE, WhatsApp support.
- **v1.1** — news digest, MCP client mode, BM25 + embeddings / Graphify search.

Roadmap: see CHANGELOG.md for shipped features.

## Links

- [deploying.md](deploying.md) — GCP + GitHub + LLM provider setup, end-to-end
- [docs/architecture.md](docs/architecture.md) — full topology
- [docs/agents.md](docs/agents.md) — per-stage schemas and prompts
- [docs/setup/telegram.md](docs/setup/telegram.md) — Telegram API + bot setup
- [docs/webui.md](docs/webui.md) — dashboard deployment
- [docs/setup/llm-providers.md](docs/setup/llm-providers.md) — provider preset configs
- [docs/setup/mcp-clients.md](docs/setup/mcp-clients.md) — Claude Desktop / Cursor integration
- [docs/eval.md](docs/eval.md) — eval harness + kill-switch design
- [SECURITY.md](SECURITY.md) — threat model + disclosure

## License

MIT. See [LICENSE](LICENSE).
