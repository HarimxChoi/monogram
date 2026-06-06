"""Pipeline observability — append-only JSONL trace. All errors swallowed; observability must not crash the pipeline."""
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
    # Cumulative per-stage ms: if a stage runs twice (escalation), both runs are summed.
    stage_latency_ms: dict[str, int] = field(default_factory=dict)

    def to_jsonl(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), ensure_ascii=False)


class StageTimer:
    """Records per-stage wall-clock ms; never raises."""

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
                self.latencies_ms.setdefault(name, 0)


def drop_id_for(payload: str) -> str:
    """Deterministic 12-char id to correlate log entries without storing the payload."""
    h = hashlib.sha256((payload or "").encode("utf-8")).hexdigest()
    return h[:12]


def _safe_get(obj: Any, attr: str, default: Any = None) -> Any:
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
