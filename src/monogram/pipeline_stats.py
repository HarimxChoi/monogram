"""Pipeline statistics — read log/pipeline.jsonl and compute latency
distributions, error rates, and stage breakdowns for dogfood and
baseline snapshots.

Used by:
  - `monogram eval baseline --save` to snapshot current-state metrics
    as reference for drift detection
  - `/stats` Telegram command (bot_stats_cmd) to show rolling metrics
    from your phone
  - morning_job health summary (when available)

Design:
  - Pure-Python statistics (no numpy dep). Personal-scale data, <10k
    entries typical.
  - Streaming parse — large logs stay memory-bounded.
  - Graceful on malformed lines (swallows errors the same way
    pipeline_log.log_pipeline_run does).
  - Quantiles via nearest-rank method (no interpolation). With ~50+
    samples this is indistinguishable from linear-interp methods but
    simpler and more robust to outliers.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

log = logging.getLogger("monogram.pipeline_stats")


@dataclass
class LatencySummary:
    """p50/p95/p99 latency snapshot across a time window."""
    samples: int
    p50_ms: int
    p95_ms: int
    p99_ms: int
    mean_ms: int
    min_ms: int
    max_ms: int


@dataclass
class StageBreakdown:
    """Per-stage latency summary. Useful to see where time is spent
    (classifier/extractor/verifier usually dominate)."""
    stage: str
    samples: int
    p50_ms: int
    p95_ms: int
    mean_ms: int


@dataclass
class PipelineStats:
    """Rolling snapshot of pipeline health across a window."""
    window_days: int
    total_runs: int
    error_rate: float          # (blocked OR exceptioned) / total
    escalation_rate: float     # escalated / total
    latency: LatencySummary
    per_stage: list[StageBreakdown]
    by_target_kind: dict[str, int] = field(default_factory=dict)
    computed_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def to_markdown(self) -> str:
        """Human-readable snapshot for baseline files + /stats replies."""
        warmup = self.latency.samples < 10
        warmup_note = (
            " *(sparse data — p95/p99 unreliable below n=10)*"
            if warmup else ""
        )

        lines = [
            f"# Pipeline stats — last {self.window_days}d",
            "",
            f"- Total runs: {self.total_runs}",
            f"- Error rate: {self.error_rate:.1%}",
            f"- Escalation rate: {self.escalation_rate:.1%}",
            f"- Computed: {self.computed_at}",
            "",
            f"## End-to-end latency (ms){warmup_note}",
            "",
            f"- p50: {self.latency.p50_ms}",
            f"- p95: {self.latency.p95_ms}",
            f"- p99: {self.latency.p99_ms}",
            f"- mean: {self.latency.mean_ms}",
            f"- min / max: {self.latency.min_ms} / {self.latency.max_ms}",
            "",
            "## Per-stage latency (ms)",
            "",
            "| Stage | Samples | p50 | p95 | mean |",
            "|---|---:|---:|---:|---:|",
        ]
        for s in self.per_stage:
            lines.append(
                f"| {s.stage} | {s.samples} | {s.p50_ms} | {s.p95_ms} | {s.mean_ms} |"
            )

        if self.by_target_kind:
            lines.extend(["", "## Drops by target kind", ""])
            for kind, count in sorted(self.by_target_kind.items(), key=lambda x: -x[1]):
                lines.append(f"- {kind}: {count}")

        return "\n".join(lines) + "\n"


def _quantile(sorted_values: list[int], q: float) -> int:
    """Nearest-rank quantile. q in [0, 1].

    Nearest-rank formula: index = ceil(q * n) - 1, clamped to [0, n-1].
    For q=0.5 on 5 sorted values → ceil(2.5)-1 = 2 → middle value ✓
    For q=0.95 on 20 values → ceil(19)-1 = 18 → 19th value ✓
    For q=0 → index 0 (smallest) by convention.
    """
    if not sorted_values:
        return 0
    import math
    n = len(sorted_values)
    if q <= 0:
        return sorted_values[0]
    idx = max(0, min(n - 1, math.ceil(q * n) - 1))
    return sorted_values[idx]


def _summarize(durations: list[int]) -> LatencySummary:
    if not durations:
        return LatencySummary(0, 0, 0, 0, 0, 0, 0)
    sorted_ms = sorted(durations)
    return LatencySummary(
        samples=len(sorted_ms),
        p50_ms=_quantile(sorted_ms, 0.50),
        p95_ms=_quantile(sorted_ms, 0.95),
        p99_ms=_quantile(sorted_ms, 0.99),
        mean_ms=int(sum(sorted_ms) / len(sorted_ms)),
        min_ms=sorted_ms[0],
        max_ms=sorted_ms[-1],
    )


def _parse_jsonl_stream(text: str):
    """Yield dict records, skipping unparseable lines silently."""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def compute_stats(
    log_content: str,
    window_days: int = 7,
    now: datetime | None = None,
) -> PipelineStats:
    """Compute pipeline stats across the last `window_days` of entries.

    `log_content` is the raw text of log/pipeline.jsonl. This separation
    from I/O keeps it testable — callers handle the github_store.read
    or file-path fetch.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=window_days)

    durations: list[int] = []
    per_stage: dict[str, list[int]] = {}
    blocked_count = 0
    escalated_count = 0
    by_kind: dict[str, int] = {}

    for rec in _parse_jsonl_stream(log_content):
        try:
            ts_str = rec.get("ts", "")
            if not ts_str:
                continue
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts < cutoff:
                continue
        except (ValueError, TypeError):
            continue

        duration_ms = rec.get("duration_ms")
        if isinstance(duration_ms, (int, float)) and duration_ms >= 0:
            durations.append(int(duration_ms))

        stage_latency = rec.get("stage_latency_ms") or {}
        if isinstance(stage_latency, dict):
            for stage, ms in stage_latency.items():
                if isinstance(ms, (int, float)) and ms >= 0:
                    per_stage.setdefault(stage, []).append(int(ms))

        if rec.get("blocked_reason"):
            blocked_count += 1
        if rec.get("escalated"):
            escalated_count += 1

        target_kind = rec.get("target_kind")
        if target_kind:
            by_kind[target_kind] = by_kind.get(target_kind, 0) + 1

    total = len(durations)

    stage_breakdowns: list[StageBreakdown] = []
    # Stable ordering: pipeline execution order
    for stage_name in ("orchestrator", "classifier", "extractor", "verifier", "writer"):
        samples = per_stage.get(stage_name, [])
        if not samples:
            continue
        sorted_samples = sorted(samples)
        stage_breakdowns.append(StageBreakdown(
            stage=stage_name,
            samples=len(samples),
            p50_ms=_quantile(sorted_samples, 0.50),
            p95_ms=_quantile(sorted_samples, 0.95),
            mean_ms=int(sum(samples) / len(samples)),
        ))

    return PipelineStats(
        window_days=window_days,
        total_runs=total,
        error_rate=(blocked_count / total) if total else 0.0,
        escalation_rate=(escalated_count / total) if total else 0.0,
        latency=_summarize(durations),
        per_stage=stage_breakdowns,
        by_target_kind=by_kind,
        computed_at=now.isoformat(),
    )


def fetch_stats(window_days: int = 7) -> PipelineStats | None:
    """Fetch log/pipeline.jsonl from the vault and compute stats.

    Returns None if the log is unreachable or empty — callers should
    render "no data yet" rather than erroring.
    """
    try:
        from . import github_store
        content = github_store.read("log/pipeline.jsonl")
    except Exception as e:
        log.warning("fetch_stats: could not read pipeline log: %s", e)
        return None

    if not content:
        return None

    return compute_stats(content, window_days=window_days)
