# Monogram

English | [한국어](./README.ko.md)

> Share it once. Monogram turns news, social posts, papers, videos, and documents into source-linked knowledge you can search and reuse.

[![tests](https://github.com/HarimxChoi/monogram/actions/workflows/tests.yml/badge.svg)](https://github.com/HarimxChoi/monogram/actions/workflows/tests.yml)
[![eval](https://github.com/HarimxChoi/monogram/actions/workflows/eval.yml/badge.svg)](https://github.com/HarimxChoi/monogram/actions/workflows/eval.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

## Why

Useful context arrives through news, social feeds, arXiv, YouTube, reports, messages, and local documents. Bookmarks preserve the URL but rarely preserve why it mattered, and information scattered across services is hard to find when a project needs it again.

## How

Monogram accepts links, messages, documents, and code through Telegram Saved Messages, an Obsidian quick-capture plugin, or MCP. A five-stage pipeline — **Orchestrator → Classifier → Extractor → Verifier → Writer** — extracts the source and metadata, structures the content as Markdown, and lands every file produced by one capture in a single Git Tree commit. Git keeps the history inspectable; the dashboard and MCP expose the same vault to people and agents.

## Result

One share becomes searchable, versioned knowledge without manually copying it into a separate note system. The vault remains ordinary Markdown, the encrypted dashboard provides a visual view, and **13 MCP tools** expose project state, briefs, retrieval, and approval-gated writes to other agents.

![Monogram dashboard — projects, wiki, life recent, commits](docs/images/dashboard.png)

![Monogram walkthrough — capture, vault, dashboard, MCP](docs/images/short-demo.gif)

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

- **Atomic ingest commits** — every path produced by one captured item lands in a single commit through the GitHub Git Tree API.
- **Hybrid and graph retrieval** — local embeddings, BM25/RRF, and an event graph search the same Markdown vault without coupling retrieval to the chat-model provider.
- **13 MCP tools** — read project state, retrieve knowledge, build briefs, and request writes. Sensitive write tools require Telegram approval.
- **SSRF-hardened URL ingestion** — every hop validated, including CGNAT + cloud metadata ranges.
- **Credential safety by construction** — classifier routing plus a writer-level secret-shape redaction backstop.
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
- Not a general web search engine — retrieval is scoped to the user's own vault.
- Not multi-user — one Telegram account, one vault, one person.
- Not a replacement for Obsidian/Notion/Logseq — it's the ingest path. Your vault renders natively in any markdown editor.

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
