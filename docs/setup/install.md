# Installation

Fast path for first-time setup. The **interactive wizard does the
work** — this doc exists for background and recovery.

## TL;DR

```bash
pip install mono-gram
monogram init          # interactive wizard (≈10 min)
monogram auth          # one-time Telegram code
monogram run           # listener + bot (leave running)
```

The wizard collects every credential inline, writes `.env` +
`mono/config.md`, and — if you pick the GCS web UI — provisions the
bucket, service account, and IAM policy for you via `gcloud`.

## What `monogram init` actually does

Six steps, each with inline validation. You can re-run at any time —
it's idempotent.

| Step | What it asks | What it does |
|---|---|---|
| 1/6 GitHub vault | PAT + `<user>/mono` repo name | probes repo with `get_contents('')` — fails loud if PAT lacks `Contents:R/W` |
| 2/6 Language | ISO 639-1 code for vault prose | one LLM call at the end to localize skeleton templates |
| 3/6 Life categories | comma list or defaults | always keeps `credentials/` (LLM-blocked) |
| 4/6 Telegram | api_id/hash + bot token + user_id | `getMe` probe on the bot token |
| 5/6 LLM provider | provider + key(s) + tier models | "Say OK" round-trip probe per tier; offers continue-on-fail |
| 6/6 Web UI | `gcs` / `self-host` / `mcp-only` + password | if `gcs`: runs `gcloud` to create bucket + SA + IAM and writes the key path into `.env`. Skips gracefully if `gcloud` is missing. |

Failures in Step 6 are non-fatal: the wizard writes your config with
`webui_mode=gcs` and you can re-run or provision by hand later —
neither the vault nor the listener depend on the bucket.

## What you need before starting

All of this should be ready-to-paste when you run `init`:

- **GitHub account** — create `<user>/mono` (empty private repo). Fine-grained PAT with `Contents: R/W` on that repo. See [README.md §Quickstart](../../README.md).
- **Telegram account** — follow [telegram.md](telegram.md) to get the four env values.
- **LLM API key** — one of:
  - Gemini (free tier is enough): <https://aistudio.google.com/app/apikey>
  - Anthropic: <https://console.anthropic.com/settings/keys>
  - OpenAI: <https://platform.openai.com/api-keys>
  - Local Ollama: just have it running; no key needed.
  - Full matrix: [llm-providers.md](llm-providers.md).
- **(optional) GCP account + `gcloud` CLI** — only if you want the
  web dashboard on `gs://`. Billing enabled (stays $0 on free tier).
  `gcloud auth login` before running `init` and the wizard provisions
  the bucket, service account, and IAM bindings for you — see
  [Install the gcloud CLI](https://cloud.google.com/sdk/docs/install).
- **(optional) backup GitHub repo** — `<user>/mono-backup`, separate
  fine-grained PAT.

## Deploying to an always-on VM

`monogram run` can run anywhere, but to get 24/7 processing without
leaving your laptop on you want a cheap VM. The canonical deploy is a
GCP `e2-micro` always-free instance.

End-to-end walkthrough (VM provision, systemd, cron jobs, monthly
backup verify) lives in **[deploying.md](../../deploying.md)**.
Everything there is additive to what `monogram init` already did.

## Related docs

- [telegram.md](telegram.md) — the four Telegram env values
- [llm-providers.md](llm-providers.md) — provider-specific model
  strings and credential routing
- [mcp-clients.md](mcp-clients.md) — Claude Desktop / Cursor /
  OpenClaw integration
- [docs/webui.md](../webui.md) — encrypted dashboard deployment
  (GCS, self-host, or MCP-only)
- [deploying.md](../../deploying.md) — end-to-end VM deployment
- [docs/architecture.md](../architecture.md) — full topology

## Recovering from a broken setup

Re-run `monogram init`. It detects an existing `.env` and asks before
overwriting. You can selectively re-enter only the phases you need.
Re-running is cheap — validation is fast and the vault skeleton write
is idempotent (`write_atomic` merges with existing content).

If a GCP bucket already exists from a previous attempt, the provision
step will detect it and reuse it rather than recreate.
