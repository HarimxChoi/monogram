"""Tests for pipeline_log — drop_id determinism, record shape, failure swallowing."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from monogram import pipeline_log


def test_drop_id_deterministic():
    """Same payload → same ID."""
    a = pipeline_log.drop_id_for("hello world")
    b = pipeline_log.drop_id_for("hello world")
    assert a == b
    assert len(a) == 12
    assert all(c in "0123456789abcdef" for c in a)


def test_drop_id_differs_per_payload():
    assert pipeline_log.drop_id_for("x") != pipeline_log.drop_id_for("y")


def test_record_shape_with_full_context():
    rec = pipeline_log.PipelineRecord(
        ts="2026-04-25T00:00:00+00:00",
        drop_id="abc123def456",
        duration_ms=1234,
        stages=["orchestrator", "classifier", "extractor", "verifier", "writer"],
        escalated=False,
        blocked_reason=None,
        target_kind="wiki",
        slug="pose-estimation",
        drop_type="url",
        target_path="wiki/pose-estimation.md",
        target_confidence="high",
        verifier_ok=True,
        provider="gemini",
        model_tier_usage={"low": 3, "mid": 1, "high": 0},
    )
    line = rec.to_jsonl()
    assert "\n" not in line
    parsed = json.loads(line)
    assert parsed["drop_id"] == "abc123def456"
    assert parsed["stages"] == ["orchestrator", "classifier", "extractor", "verifier", "writer"]
    assert parsed["model_tier_usage"] == {"low": 3, "mid": 1, "high": 0}


def test_log_pipeline_run_happy(monkeypatch):
    """Normal pipeline completion appends exactly one JSONL line."""
    captured = {}

    def fake_append(path, content, msg):
        captured["path"] = path
        captured["content"] = content
        captured["msg"] = msg
        return True

    monkeypatch.setattr(pipeline_log.github_store, "append", fake_append)
    monkeypatch.setattr(
        pipeline_log, "load_vault_config",
        lambda: SimpleNamespace(llm_provider="gemini"),
        raising=False,
    )

    classification = SimpleNamespace(
        target_kind="project", slug="p1",
        drop_type="text", target_path="projects/p1.md",
    )
    verification = SimpleNamespace(target_confidence="high", ok=True)

    pipeline_log.log_pipeline_run(
        payload="test drop",
        classification=classification,
        verification=verification,
        stages=["orchestrator", "classifier", "extractor", "verifier", "writer"],
        escalated=False,
        duration_ms=400,
    )

    assert captured["path"] == "log/pipeline.jsonl"
    parsed = json.loads(captured["content"])
    assert parsed["target_kind"] == "project"
    assert parsed["verifier_ok"] is True
    assert parsed["drop_id"] == pipeline_log.drop_id_for("test drop")


def test_log_pipeline_run_blocked_path(monkeypatch):
    """Blocked pipeline (classifier ran, verifier rejected) still logs."""
    captured = {}
    monkeypatch.setattr(
        pipeline_log.github_store, "append",
        lambda p, c, m: captured.setdefault("c", c) or True,
    )
    classification = SimpleNamespace(
        target_kind="wiki", slug="x",
        drop_type="text", target_path="wiki/x.md",
    )
    verification = SimpleNamespace(target_confidence="low", ok=False)
    pipeline_log.log_pipeline_run(
        payload="ambiguous",
        classification=classification,
        verification=verification,
        stages=["orchestrator", "classifier", "extractor", "verifier"],
        escalated=True,
        duration_ms=900,
        blocked_reason="two escalations — ask the user",
    )
    parsed = json.loads(captured["c"])
    assert parsed["blocked_reason"] == "two escalations — ask the user"
    assert parsed["escalated"] is True
    assert parsed["verifier_ok"] is False


def test_log_pipeline_run_no_classification(monkeypatch):
    """Early failure (before classifier) logs None fields, no crash."""
    captured = {}
    monkeypatch.setattr(
        pipeline_log.github_store, "append",
        lambda p, c, m: captured.setdefault("c", c) or True,
    )
    pipeline_log.log_pipeline_run(
        payload="x",
        classification=None,
        verification=None,
        stages=["orchestrator"],
        escalated=False,
        duration_ms=50,
        blocked_reason="orchestrator crashed",
    )
    parsed = json.loads(captured["c"])
    assert parsed["target_kind"] is None
    assert parsed["verifier_ok"] is None
    assert parsed["stages"] == ["orchestrator"]


def test_log_pipeline_run_swallows_append_failure(monkeypatch):
    """GitHub write failure must not raise."""
    def raising_append(*a, **k):
        raise RuntimeError("github down")

    monkeypatch.setattr(pipeline_log.github_store, "append", raising_append)

    # Should not raise
    pipeline_log.log_pipeline_run(
        payload="x",
        classification=None,
        verification=None,
        stages=[],
        escalated=False,
        duration_ms=10,
    )


def test_log_pipeline_run_swallows_record_build_failure(monkeypatch):
    """Weird classification object (missing attrs accessed via getattr) still works."""
    captured = {}
    monkeypatch.setattr(
        pipeline_log.github_store, "append",
        lambda p, c, m: captured.setdefault("c", c) or True,
    )
    # Pass an object with no expected attributes — getattr returns None
    pipeline_log.log_pipeline_run(
        payload="x",
        classification=object(),
        verification=object(),
        stages=["orchestrator"],
        escalated=False,
        duration_ms=10,
    )
    parsed = json.loads(captured["c"])
    assert parsed["target_kind"] is None
    assert parsed["verifier_ok"] is None
