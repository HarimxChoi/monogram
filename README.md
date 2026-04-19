# Monogram

> Drop into Telegram. Auto-save as wiki. Wake up to a project dashboard.

[![tests](https://github.com/HarimxChoi/monogram/actions/workflows/tests.yml/badge.svg)](https://github.com/HarimxChoi/monogram/actions/workflows/tests.yml)
[![eval](https://github.com/HarimxChoi/monogram/actions/workflows/eval.yml/badge.svg)](https://github.com/HarimxChoi/monogram/actions/workflows/eval.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

Send anything to Telegram Saved Messages. Monogram classifies it,
verifies no credentials leak, and commits it to a private GitHub repo
as a markdown file you'd have written yourself. One repo. One mental
model. Same vault, three views — Obsidian, MCP, and this:

![Monogram dashboard — projects, wiki, life recent, commits](docs/images/dashboard.png)

Dark, information-dense, password-protected, client-side decrypted.
Runs from a static bucket ($0 / month on GCS free tier), a self-hosted
server, or not at all (MCP-only mode). Design reference:
[docs/design/webui-mockup.html](docs/design/webui-mockup.html).

> 🎬 **30-second walkthrough** — capture → vault → dashboard → MCP query. *Coming soon.*

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
│  VAULT  (git)                  BACKUP  (separate PAT)        │
│    <user>/mono          ⟶      <user>/mono-backup            │
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

Six horizontal planes. Inputs → pipeline → vault/backup → consumer
surfaces. Observability and eval sit below, cross-cutting. Full
writeup: [docs/architecture.md](docs/architecture.md).

## Quickstart

Python 3.10+, a GitHub account, a Telegram account, one LLM API key
(Gemini free tier is sufficient).

```bash
pip install mono-gram
monogram init            # interactive wizard
monogram auth            # one-time Telegram auth
monogram run             # listener + bot (leave running)
```

> The pip package is `mono-gram`; the CLI command remains `monogram`.
> The Python import path is also `monogram` — `from monogram import ...`.

Drop something into Saved Messages. Within seconds a commit appears on
your vault repo. End-to-end walkthrough (GCP free tier → PyPI): **[deploying.md](deploying.md)**.

Optional extras:

```bash
pip install 'mono-gram[ingestion-all]'   # YouTube, arXiv, PDF, Office, HWP
pip install 'mono-gram[eval]'            # cassette-replay eval harness
```

## Web UI

One vault, three ways to deploy the dashboard:

| Mode | Where it runs | When to pick it |
|---|---|---|
| **GCS** | Static bucket, client-side decrypt | Default. Bookmarkable URL, $0 at personal scale. |
| **Self-host** | Local Flask or any static host | Air-gapped / private network. |
| **MCP-only** | No web face — access via Claude Desktop / Cursor | Terminal-centric workflow. |

Password-protected. Content is encrypted at rest; the host only ever
holds ciphertext. Regenerated on morning / weekly runs. Setup:
[docs/setup/gcp-webui.md](docs/setup/gcp-webui.md) (~5 min).

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

```
run · morning · weekly · digest · search · stats
backup · mcp-serve · eval · migrate
```

Details: `monogram --help` or [docs/agents.md](docs/agents.md).

## Ingestion

Drop URLs, PDFs, Office docs — they're extracted before the pipeline
sees them. Full table + fallback chain in
[docs/ingestion.md](docs/ingestion.md). HWP is hardened against
CVE-2024-12425/12426 and CVE-2025-1080; see [SECURITY.md](SECURITY.md).

## What this is *not*

- Not a chat bot — no conversational turn-taking.
- Not a search engine — `monogram search` is grep + scope filters. Semantic search in v1.1.
- Not multi-user — one Telegram account, one vault, one person.
- Not a replacement for Obsidian/Notion/Logseq — it's the ingest path. Your vault renders natively in any markdown editor.

## Roadmap

- **v0.8 (current)** — core pipeline, ingestion, hardening, observability
- **v1.0** — PyPI release after dogfood + RC soak
- **v1.1** — news digest, MCP client mode, BM25 + embeddings search

Roadmap: see CHANGELOG.md for shipped features.

## Links

- [deploying.md](deploying.md) — GCP + GitHub + LLM provider setup, end-to-end
- [docs/architecture.md](docs/architecture.md) — full topology
- [docs/agents.md](docs/agents.md) — per-stage schemas and prompts
- [docs/setup/gcp-webui.md](docs/setup/gcp-webui.md) — dashboard deployment
- [docs/setup/llm-providers.md](docs/setup/llm-providers.md) — provider preset configs
- [docs/setup/mcp-clients.md](docs/setup/mcp-clients.md) — Claude Desktop / Cursor integration
- [docs/eval.md](docs/eval.md) — eval harness + kill-switch design
- [SECURITY.md](SECURITY.md) — threat model + disclosure
- [CONTRIBUTING.md](CONTRIBUTING.md) — how to help

## License

MIT. See [LICENSE](LICENSE).

---

Existing PKM tools treat capture as easy and organization as hard.
In practice it's the opposite — I always capture, rarely organize.
Monogram is the LLM pipeline that organizes as a side-effect of
capture.
