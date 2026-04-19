"""Monogram agents — the five-stage pipeline.

Orchestrator → Classifier → Extractor → Verifier → Writer

Per-stage spec: docs/agents.md (authoritative).
Pipeline overview: docs/architecture.md §3.

C1 skeleton state:
  orchestrator  FULL  — Flash-Lite, returns PipelinePlan
  classifier    FULL  — Flash-Lite, returns Classification
  extractor     STUB  — returns PersonalLog(content=payload); Phase D replaces
  verifier      STUB  — returns VerifyResult(ok=True, ...); Phase D replaces
  writer        FULL  — deterministic, returns FileChange (no git commit yet)
"""
from . import classifier, extractor, orchestrator, verifier, writer  # noqa: F401

__all__ = ["orchestrator", "classifier", "extractor", "verifier", "writer"]
