"""News / signal ingestion — pulls external economic/operational data
sources into the markdown so the morning brief can reference them as
grounded context instead of vague "recent activity" phrasing.

Source adapters:
  - fred.py       — FRED (Federal Reserve Economic Data): rates, CPI, yields,
                    unemployment, FX — the "what's moving in the macro" layer.

Each adapter writes a section into `daily/<date>/signals.md`. Morning
brief generation (phase 2) reads this file to ground its "market /
context" paragraphs in actual numbers.

Design constraints:
  - All adapters degrade gracefully: missing API key → skip, no crash.
  - No LLM calls here. Adapters produce facts; LLM interpretation happens
    later in the brief-generation pipeline.
  - Network-bounded: per-adapter asyncio.wait_for, never block the caller.
"""
