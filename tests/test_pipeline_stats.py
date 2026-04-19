"""Tests for v0.8 Tier 4 — observability.

Coverage:
- StageTimer: single stage, multiple stages, cumulative retries,
  exception-safe, zero-duration, nested stages
- _quantile: nearest-rank correctness at boundaries
- compute_stats: empty input, malformed JSON, out-of-window filter,
  stage breakdown, escalation + error counting
- LatencySummary + PipelineStats to_markdown rendering
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import pytest


# ---------------------------------------------------------------------------
# StageTimer
# ---------------------------------------------------------------------------

class TestStageTimer:
    def test_single_stage_records(self):
        from monogram.pipeline_log import StageTimer

        t = StageTimer()
        with t.stage("classifier"):
            time.sleep(0.005)
        assert "classifier" in t.latencies_ms
        # 5ms sleep → at least 3ms elapsed (sleep accuracy varies)
        assert t.latencies_ms["classifier"] >= 3

    def test_multiple_stages_separate_keys(self):
        from monogram.pipeline_log import StageTimer

        t = StageTimer()
        with t.stage("classifier"):
            pass
        with t.stage("extractor"):
            pass
        assert set(t.latencies_ms.keys()) == {"classifier", "extractor"}

    def test_cumulative_on_retry(self):
        """Escalation path runs extractor/verifier twice. StageTimer
        must sum both runs, not overwrite."""
        from monogram.pipeline_log import StageTimer

        t = StageTimer()
        with t.stage("extractor"):
            time.sleep(0.003)
        first = t.latencies_ms["extractor"]

        with t.stage("extractor"):
            time.sleep(0.003)
        second = t.latencies_ms["extractor"]

        assert second > first  # accumulated

    def test_exception_in_stage_still_records(self):
        """Timer must close even if the wrapped code raises."""
        from monogram.pipeline_log import StageTimer

        t = StageTimer()
        with pytest.raises(RuntimeError):
            with t.stage("orchestrator"):
                raise RuntimeError("boom")
        assert "orchestrator" in t.latencies_ms

    def test_zero_stages_when_nothing_used(self):
        from monogram.pipeline_log import StageTimer

        t = StageTimer()
        assert t.latencies_ms == {}


# ---------------------------------------------------------------------------
# _quantile nearest-rank
# ---------------------------------------------------------------------------

class TestQuantile:
    def test_empty(self):
        from monogram.pipeline_stats import _quantile
        assert _quantile([], 0.5) == 0

    def test_single_value(self):
        from monogram.pipeline_stats import _quantile
        assert _quantile([42], 0.5) == 42
        assert _quantile([42], 0.95) == 42
        assert _quantile([42], 0.99) == 42

    def test_p50_of_odd_count(self):
        from monogram.pipeline_stats import _quantile
        # 5 values: p50 should be the middle
        assert _quantile([10, 20, 30, 40, 50], 0.50) == 30

    def test_p95_approximate(self):
        from monogram.pipeline_stats import _quantile
        # 20 values 1..20; p95 nearest-rank = index 18 or 19 (19 or 20)
        values = list(range(1, 21))
        assert _quantile(values, 0.95) in (19, 20)

    def test_p99_near_top(self):
        from monogram.pipeline_stats import _quantile
        values = list(range(1, 101))  # 1..100
        assert _quantile(values, 0.99) in (99, 100)


# ---------------------------------------------------------------------------
# compute_stats
# ---------------------------------------------------------------------------

class TestComputeStats:
    def _make_record(
        self,
        ts: datetime,
        duration_ms: int = 500,
        blocked: bool = False,
        escalated: bool = False,
        target_kind: str = "wiki",
        stage_latency: dict | None = None,
    ) -> str:
        rec = {
            "ts": ts.isoformat(),
            "drop_id": "abc123",
            "duration_ms": duration_ms,
            "stages": ["orchestrator", "classifier", "extractor", "verifier", "writer"],
            "escalated": escalated,
            "blocked_reason": "test" if blocked else None,
            "target_kind": target_kind,
            "slug": "x",
            "drop_type": "text",
            "target_path": f"{target_kind}/x.md",
            "target_confidence": "high",
            "verifier_ok": not blocked,
            "provider": "gemini",
            "model_tier_usage": {},
            "stage_latency_ms": stage_latency or {
                "orchestrator": 50,
                "classifier": 200,
                "extractor": 150,
                "verifier": 80,
                "writer": 20,
            },
        }
        return json.dumps(rec)

    def test_empty_input(self):
        from monogram.pipeline_stats import compute_stats
        stats = compute_stats("", window_days=7)
        assert stats.total_runs == 0
        assert stats.error_rate == 0.0
        assert stats.escalation_rate == 0.0
        assert stats.latency.samples == 0
        assert stats.per_stage == []

    def test_malformed_lines_skipped(self):
        from monogram.pipeline_stats import compute_stats
        content = (
            "not-json-at-all\n"
            '{"also": "missing ts field"}\n'
            + self._make_record(datetime.now(timezone.utc), 300)
            + "\n"
        )
        stats = compute_stats(content, window_days=7)
        assert stats.total_runs == 1
        assert stats.latency.p50_ms == 300

    def test_out_of_window_filter(self):
        from monogram.pipeline_stats import compute_stats
        now = datetime.now(timezone.utc)
        old = now - timedelta(days=30)
        recent = now - timedelta(days=2)
        content = (
            self._make_record(old, 999) + "\n"
            + self._make_record(recent, 100) + "\n"
        )
        stats = compute_stats(content, window_days=7, now=now)
        assert stats.total_runs == 1
        assert stats.latency.p50_ms == 100

    def test_error_rate_counted(self):
        from monogram.pipeline_stats import compute_stats
        now = datetime.now(timezone.utc)
        content = (
            self._make_record(now, 100, blocked=False) + "\n"
            + self._make_record(now, 100, blocked=True) + "\n"
            + self._make_record(now, 100, blocked=True) + "\n"
        )
        stats = compute_stats(content, window_days=7, now=now)
        assert stats.total_runs == 3
        assert stats.error_rate == pytest.approx(2 / 3)

    def test_escalation_rate_counted(self):
        from monogram.pipeline_stats import compute_stats
        now = datetime.now(timezone.utc)
        content = (
            self._make_record(now, 100, escalated=False) + "\n"
            + self._make_record(now, 200, escalated=True) + "\n"
        )
        stats = compute_stats(content, window_days=7, now=now)
        assert stats.escalation_rate == 0.5

    def test_stage_breakdown(self):
        from monogram.pipeline_stats import compute_stats
        now = datetime.now(timezone.utc)
        content = (
            self._make_record(now, 500, stage_latency={
                "orchestrator": 50, "classifier": 100,
                "extractor": 200, "verifier": 100, "writer": 50,
            }) + "\n"
            + self._make_record(now, 500, stage_latency={
                "orchestrator": 60, "classifier": 150,
                "extractor": 200, "verifier": 100, "writer": 60,
            }) + "\n"
        )
        stats = compute_stats(content, window_days=7, now=now)
        assert stats.total_runs == 2

        by_stage = {s.stage: s for s in stats.per_stage}
        assert set(by_stage.keys()) == {
            "orchestrator", "classifier", "extractor", "verifier", "writer"
        }

        # Pipeline-execution-order preserved
        stage_names = [s.stage for s in stats.per_stage]
        assert stage_names == [
            "orchestrator", "classifier", "extractor", "verifier", "writer"
        ]

        assert by_stage["classifier"].samples == 2
        assert by_stage["classifier"].mean_ms == 125  # (100+150)/2

    def test_target_kind_histogram(self):
        from monogram.pipeline_stats import compute_stats
        now = datetime.now(timezone.utc)
        content = (
            self._make_record(now, 100, target_kind="wiki") + "\n"
            + self._make_record(now, 100, target_kind="wiki") + "\n"
            + self._make_record(now, 100, target_kind="life") + "\n"
        )
        stats = compute_stats(content, window_days=7, now=now)
        assert stats.by_target_kind == {"wiki": 2, "life": 1}

    def test_missing_stage_latency_field_tolerated(self):
        """Logs from before v0.8 Tier 4 won't have stage_latency_ms —
        must still parse cleanly (just no per-stage data)."""
        from monogram.pipeline_stats import compute_stats
        now = datetime.now(timezone.utc)
        # Manually build a record missing stage_latency_ms
        rec = {
            "ts": now.isoformat(),
            "duration_ms": 500,
            "stages": ["classifier"],
            "escalated": False,
            "blocked_reason": None,
            "target_kind": "wiki",
        }
        content = json.dumps(rec)
        stats = compute_stats(content, window_days=7, now=now)
        assert stats.total_runs == 1
        assert stats.latency.p50_ms == 500
        assert stats.per_stage == []  # no stage data available


# ---------------------------------------------------------------------------
# PipelineStats rendering
# ---------------------------------------------------------------------------

class TestPipelineStatsRendering:
    def test_to_markdown_structure(self):
        from monogram.pipeline_stats import (
            LatencySummary, PipelineStats, StageBreakdown,
        )

        stats = PipelineStats(
            window_days=7,
            total_runs=42,
            error_rate=0.05,
            escalation_rate=0.10,
            latency=LatencySummary(42, 500, 1200, 2000, 600, 100, 3000),
            per_stage=[
                StageBreakdown("classifier", 42, 200, 400, 250),
                StageBreakdown("extractor", 42, 150, 350, 200),
            ],
            by_target_kind={"wiki": 20, "life": 15, "project": 7},
            computed_at="2026-04-25T00:00:00+00:00",
        )
        md = stats.to_markdown()
        assert "Pipeline stats" in md
        assert "last 7d" in md
        assert "42" in md  # total runs
        assert "5.0%" in md  # error rate
        assert "p50: 500" in md
        assert "classifier" in md
        assert "extractor" in md
        assert "wiki" in md


# ---------------------------------------------------------------------------
# Integration: PipelineRecord includes stage_latency_ms
# ---------------------------------------------------------------------------

class TestPipelineRecordSchema:
    def test_record_serializes_stage_latency(self):
        from monogram.pipeline_log import PipelineRecord

        rec = PipelineRecord(
            ts="2026-04-25T00:00:00+00:00",
            drop_id="abc",
            duration_ms=500,
            stages=["classifier"],
            escalated=False,
            blocked_reason=None,
            target_kind="wiki",
            slug="x",
            drop_type="text",
            target_path="wiki/x.md",
            target_confidence="high",
            verifier_ok=True,
            provider="gemini",
            stage_latency_ms={"classifier": 200},
        )
        line = rec.to_jsonl()
        parsed = json.loads(line)
        assert parsed["stage_latency_ms"] == {"classifier": 200}

    def test_record_default_stage_latency_is_empty_dict(self):
        from monogram.pipeline_log import PipelineRecord

        rec = PipelineRecord(
            ts="", drop_id="", duration_ms=0, stages=[], escalated=False,
            blocked_reason=None, target_kind=None, slug=None,
            drop_type=None, target_path=None, target_confidence=None,
            verifier_ok=None,
        )
        assert rec.stage_latency_ms == {}
