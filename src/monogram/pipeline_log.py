"""Pipeline observability — append-only JSONL trace of every run.

Logs to `log/pipeline.jsonl` in the vault repo. One line per pipeline
invocation, including blocked / errored runs. Shape:

    {"ts": "2026-04-25T…", "drop_id": "abc123…", "duration_ms": 1234,
     "stages": ["orchestrator","classifier",…], "escalated": false,
     "blocked_reason": null, "target_kind": "wiki", "slug": "…",
     "drop_type": "url", "target_path": "wiki/….md",
     "target_confidence": "high", "verifier_ok": true,
     "provider": "gemini", "model_tier_usage": {"low":3,"mid":1,"high":0}}

This is feedstock for the v0.7 eval harness and the morning brief's
pipeline-health metrics. All failures in this module are silently
swallowed — observability MUST NOT crash the pipeline.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from . import github_store
from .vault_config import load_vault_config

log = logging.getLogger("monogram.pipeline_log")

_LOG_PATH = "log/pipeline.jsonl"


@dataclass
class PipelineRecord:
    ts: str
    drop_id: str
    duration_ms: int
    stages: list[str]
    escalated: bool
    blocked_reason: str | None
    target_kind: str | None
    slug: str | None
    drop_type: str | None
    target_path: str | None
    target_confidence: str | None
    verifier_ok: bool | None
    provider: str = ""
    model_tier_usage: dict[str, int] = field(default_factory=dict)
    # v0.8 Tier 4: per-stage wall-clock latency in milliseconds.
    # Keys are stage names ("orchestrator", "classifier", "extractor",
    # "verifier", "writer"). Values are cumulative — if a stage runs
    # twice (escalation path), both runs are summed.
    stage_latency_ms: dict[str, int] = field(default_factory=dict)

    def to_jsonl(self) -> str:
        """Single-line JSON suitable for append to a .jsonl file."""
        return json.dumps(asdict(self), separators=(",", ":"), ensure_ascii=False)


class StageTimer:
    """Context-manager that records per-stage wall-clock latency.

    Usage:
        timer = StageTimer()
        with timer.stage("orchestrator"):
            plan = await orchestrator.run(payload)
        ...
        log_pipeline_run(..., stage_latency_ms=timer.latencies_ms)

    Cumulative — if a stage (e.g., extractor on escalation) runs twice,
    both runs are summed. That's what dogfood wants: total time spent in
    each stage, including retries.

    Never raises. If `time.monotonic` somehow fails, emits zero and
    continues.
    """

    def __init__(self) -> None:
        self.latencies_ms: dict[str, int] = {}

    @contextmanager
    def stage(self, name: str):
        start = time.monotonic()
        try:
            yield
        finally:
            try:
                elapsed_ms = int((time.monotonic() - start) * 1000)
                self.latencies_ms[name] = (
                    self.latencies_ms.get(name, 0) + elapsed_ms
                )
            except Exception:
                # Observability must not break the pipeline
                self.latencies_ms.setdefault(name, 0)


def drop_id_for(payload: str) -> str:
    """Deterministic 12-char hex id for the payload.

    Used to correlate multiple log entries for the same drop (e.g. across
    a retry) without storing the payload itself in the log stream.
    """
    h = hashlib.sha256((payload or "").encode("utf-8")).hexdigest()
    return h[:12]


def _safe_get(obj: Any, attr: str, default: Any = None) -> Any:
    """getattr that tolerates None and weird objects."""
    if obj is None:
        return default
    try:
        return getattr(obj, attr, default)
    except Exception:
        return default


def log_pipeline_run(
    *,
    payload: str,
    classification: Any,
    verification: Any,
    stages: list[str],
    escalated: bool,
    duration_ms: int,
    blocked_reason: str | None = None,
    stage_latency_ms: dict[str, int] | None = None,
) -> None:
    """Append a single pipeline-run trace to `log/pipeline.jsonl`.

    Swallows all errors — including malformed classification/verification
    objects, vault-read failures, and github_store.append failures.
    The pipeline must never fail because of observability.
    """
    try:
        try:
            provider = _safe_get(load_vault_config(), "llm_provider", "") or ""
        except Exception:
            provider = ""

        rec = PipelineRecord(
            ts=datetime.now(timezone.utc).isoformat(),
            drop_id=drop_id_for(payload),
            duration_ms=duration_ms,
            stages=list(stages or []),
            escalated=bool(escalated),
            blocked_reason=blocked_reason,
            target_kind=_safe_get(classification, "target_kind"),
            slug=_safe_get(classification, "slug"),
            drop_type=_safe_get(classification, "drop_type"),
            target_path=_safe_get(classification, "target_path"),
            target_confidence=_safe_get(verification, "target_confidence"),
            verifier_ok=_safe_get(verification, "ok"),
            provider=provider,
            stage_latency_ms=dict(stage_latency_ms or {}),
        )
        line = rec.to_jsonl()
        github_store.append(_LOG_PATH, line, "monogram: pipeline trace")
    except Exception as e:
        log.debug("pipeline_log swallowed error: %s", e)
