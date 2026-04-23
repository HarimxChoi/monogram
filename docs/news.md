# News / signals ingestion

External data sources — macro indicators, refugee flows, disasters,
conflict events, curated RSS — pulled into
`daily/<YYYY-MM-DD>/signals.md` once a day. The morning brief
(phase 2) reads that file to ground its "market / context" paragraphs
in actual numbers instead of vague "recent activity" phrasing.

**Status**: Phase 1 (FRED only) landed in v0.9. Phase 2 (multi-source +
LLM synthesis) is the next milestone.

## Why

A personal-knowledge pipeline that only ever reads *your own* drops
misses half the picture. What moved in the world today matters for
tomorrow's decisions — rates, FX, energy, conflicts — and the cost
of fetching this is tiny (most sources are free, no key needed).

Design constraints:

- **No new accounts for the default setup.** Default sources (FRED,
  Open-Meteo, UNHCR, ReliefWeb, RSS) are free-tier or keyless.
- **Facts-only from adapters.** Each source writes raw numbers + dates.
  No LLM interpretation until the synthesis step.
- **One LLM call per day.** Cheap, controllable, cacheable.
- **Graceful degradation.** Any source can fail; the rest still write.

## Phase 1 (shipped) — FRED macro dashboard

`monogram news fetch --source fred` pulls the default FRED series:

| Series | Meaning |
|---|---|
| CPIAUCSL | Consumer Price Index, all urban |
| FEDFUNDS | Effective Federal Funds Rate |
| DGS10, DGS2 | 10Y / 2Y Treasury yields |
| T10Y2Y | 10Y–2Y spread (inversion watch) |
| UNRATE | Unemployment rate |
| DCOILWTICO | WTI crude |
| DEXKOUS | KRW/USD |
| PAYEMS | Nonfarm payrolls |

Output: a table + mechanical flags (yield-curve inversion, ≥5% DoD
moves) in `daily/<date>/signals.md`.

### Setup

1. Free API key: <https://fred.stlouisfed.org/docs/api/api_key.html> (5 min).
2. Add to `.env`:
   ```env
   FRED_API_KEY=your_key_here
   ```
3. Run manually or from cron:
   ```bash
   monogram news fetch              # all configured sources
   monogram news fetch --source fred
   monogram news fetch --dry-run    # stdout only
   ```

### Cron (VM deploy)

Add before the morning job so the brief can reference the signals:

```cron
30 5 * * * cd /home/user/.config/monogram && monogram news fetch >> /var/log/monogram-news.log 2>&1
0  6 * * * cd /home/user/.config/monogram && monogram morning
```

## Phase 2 (planned) — multi-source + LLM synthesis

### Sources to add

Keyless / free-tier, ranked by personal-PKM value:

| Source | What | Auth | Relevance |
|---|---|---|---|
| **Open-Meteo** | Weather forecast (user-set location) | none | Daily-relevant |
| **ReliefWeb** | Climate/disaster alerts | app name only | Travel / family safety |
| **UNHCR** | Refugee flows | none | Geopolitics |
| **RSS feeds** | User-curated news list (YAML config) | none | Core news layer |
| **ACLED** | Conflict events | free email registration | Geopolitics deep |
| **OPENAQ** | Air quality | optional key | Health / travel |
| **Finnhub (free)** | Stock quotes + news | free key | Finance layer |

Reference: `koala73/worldmonitor`'s [`.env.example`](https://github.com/koala73/worldmonitor/blob/main/.env.example)
catalogs 65+ sources with auth/free-tier annotations — the list above
cherry-picks those that fit a personal-PKM context (vs the full
situational-awareness use case worldmonitor targets).

### Architecture

Each adapter returns structured data, not rendered markdown:

```python
@dataclass
class SignalSection:
    source: str        # "fred", "open-meteo", ...
    facts: list[Fact]  # flat list of (label, value, unit, as_of)
    warnings: list[str]  # mechanical flags ("yield curve inverted", …)

async def fetch(config) -> SignalSection: ...
```

Then a single synthesizer combines all sections and makes **one LLM
call** (Gemini Flash / whatever the low tier is):

```
Input: combined facts from all sources (~1-2k tokens)
Prompt: "Summarize today's signals in {user.primary_language}. 300
  words max. Highlight anything mechanically flagged. Prefer concrete
  nouns over abstractions."
Output: one prose block + the raw facts table appended.
```

This lands at `daily/<date>/signals.md` with the same atomic-commit
guarantees as everything else in the vault.

### Why one LLM call instead of per-source

- Cost: 1 Flash call ≈ $0.0001, daily. Per-source would be 7× that
  and still only matters if you're counting pennies.
- Quality: the model can cross-reference ("Fed hiked **and** crude
  fell **and** 10Y rose — unusual combination") — no per-source
  adapter can surface that.
- Consistency: one language, one voice, one truncation budget.

### Kill-switch

Add `news_enabled: bool = false` to `vault_config.py`. Default off so
upgrades don't start burning quota; user enables via
`/config_news_enable` bot command (Phase 2 PR will add this).

### Integration with morning brief

`morning_job._collect_morning_context` will also read
`daily/<yesterday>/signals.md` and pass the synthesized paragraph
(not the raw facts) into the brief prompt as a "Market / context"
section. Keeps the brief's voice unified.

## Related

- [agents.md](agents.md) — 5-stage LLM pipeline that already runs
  per drop; news synthesis is a separate once-a-day pipeline.
- [deploying.md](../deploying.md) — cron setup for news + morning
  sequence.
- [docs/setup/llm-providers.md](setup/llm-providers.md) — which model
  tier to use for the synthesis call (recommend low tier).
