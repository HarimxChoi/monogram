# LLM providers — pick any

Monogram is LLM-agnostic via [litellm](https://docs.litellm.ai/docs/providers).
You pick any provider at `monogram init` time, or switch later via bot
commands or by editing `mono/config.md` directly.

## Supported endpoints

| Endpoint | Format | Credentials | Notes |
|---|---|---|---|
| `gemini` | `gemini/<model>` | `GEMINI_API_KEY` | Free tier at [aistudio.google.com](https://aistudio.google.com) |
| `anthropic` | `anthropic/<model>` | `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) |
| `openai` | `openai/<model>` | `OPENAI_API_KEY` | [platform.openai.com](https://platform.openai.com) |
| `ollama` | `ollama/<model>[:tag]` | none | Local server, default `http://localhost:11434` |
| `openai-compat` | `openai/<model>` | `OPENAI_API_KEY=dummy` | LM Studio, vLLM, LiteLLM proxy, OpenRouter, etc. Requires `llm_base_url`. |

No model names are hardcoded in Monogram. You enter them at init time
(or via `/config_llm_*` later), and they go to `mono/config.md`. When a
provider ships new models, edit the vault and restart — no Monogram
update needed.

## Two init paths

### Path 1 — Default (Gemini free tier)

```
monogram init
...
Step 5/5: LLM setup
Choose a path:
  [1] Default — Gemini free tier (recommended for $0/month)
  [2] Bring your own LLM
Choice [1]:
```

Writes these starter values to `mono/config.md` (editable anytime):

```yaml
llm_provider: gemini
llm_mode: tiered
llm_models:
  low:  gemini/gemini-2.5-flash-lite
  mid:  gemini/gemini-2.5-flash
  high: gemini/gemini-2.5-pro
llm_base_url: ""
```

### Path 2 — Bring your own LLM

You pick the provider and enter model strings from that provider's docs.
Monogram asks nothing beyond what it needs to route credentials correctly.

Example (Anthropic tiered):

```yaml
llm_provider: anthropic
llm_mode: tiered
llm_models:
  low:  anthropic/claude-haiku-4-5
  mid:  anthropic/claude-sonnet-4-6
  high: anthropic/claude-opus-4-7
llm_base_url: ""
```

Example (Ollama single-mode):

```yaml
llm_provider: ollama
llm_mode: single
llm_models:
  single: ollama/qwen2.5:7b
llm_base_url: http://localhost:11434
```

## Tiered vs single

- **Tiered** (default): low-tier handles classifier/extractor/verifier
  (frequent, cheap calls); mid-tier for wiki synthesis and verifier
  escalation; high-tier for morning brief + weekly report.
- **Single**: one model handles everything. Simpler, but no cost
  optimization.

## Runtime editing — bot commands

All `/config_llm_*` commands edit `mono/config.md` and immediately reload
the cache. Only the whitelisted `TELEGRAM_USER_ID` can use them.

```
/config_llm                               show current config
/config_llm_provider <name>               set provider
/config_llm_mode tiered|single            set mode
/config_llm_model_low <model-string>      set low tier
/config_llm_model_mid <model-string>      set mid tier
/config_llm_model_high <model-string>     set high tier
/config_llm_model_single <model-string>   set single-mode model
/config_llm_base_url <url>                set base_url (empty = clear)
/config_llm_test                          test call per tier
/config_llm_help <endpoint>               docs URL + format
/config_reload                            re-read config.md after manual edit
```

## Mixing providers across tiers

Nothing stops you:

```yaml
llm_provider: gemini       # credential routing hint
llm_mode: tiered
llm_models:
  low:  gemini/gemini-2.5-flash-lite     # free tier
  mid:  anthropic/claude-sonnet-4-6      # paid, better reasoning
  high: openai/gpt-5                     # paid, different vendor
llm_base_url: ""
```

As long as the `.env` has the corresponding API keys, every tier just
works. `llm_provider` is a hint for default credential lookup — per-call
routing is always by the `provider/model` prefix in the model string.

## Local models

### Ollama

```yaml
llm_provider: ollama
llm_mode: single
llm_models:
  single: ollama/qwen2.5:7b
llm_base_url: http://localhost:11434
```

Verify with `ollama list` on the machine running Ollama.

### LM Studio / vLLM / LiteLLM proxy / OpenRouter

```yaml
llm_provider: openai-compat
llm_mode: single
llm_models:
  single: openai/local-model-name
llm_base_url: http://localhost:1234/v1
```

Set `OPENAI_API_KEY=dummy` in `.env` (most local servers require any
non-empty value but ignore it).

### Note on structured output

Monogram's extractor + verifier use Pydantic schemas via litellm's
`response_format` parameter. Most local servers support JSON-mode
reasonably well; some don't. If the low tier fails on a local model,
mix: use the local model for single-mode simple classification, and fall
back to a cloud model for `get_model("mid")` / `("high")`.

## Cost guidance (rough, daily use)

| Scenario | Daily cost |
|---|---|
| Gemini free tier, tiered, ~30 drops/day | **$0** (within free RPD) |
| Anthropic tiered (haiku low, sonnet mid, opus high) | ~$0.50/day |
| OpenAI gpt-5-nano + gpt-5 tiered | ~$0.30/day |
| Ollama single-mode (local) | **$0** (hardware cost only) |

Numbers assume ~30 drops/day + 1 morning brief + weekly report.
Image drops consume more tokens but stay cheap on the free tier.
