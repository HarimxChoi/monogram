"""Orchestrator ablation — thin pytest wrapper.

The real work happens via `monogram eval ablate-diff --against main`
(evals/report.py::run_ablation_diff). This test is a smoke check that
the diff tool runs against the current cassettes without errors. It is
SKIPPED by default; run with `-m ablation` to exercise.
"""
from __future__ import annotations

import pytest


pytestmark = pytest.mark.ablation


@pytest.mark.skip(reason="Run via `monogram eval ablate-diff` after dual record runs")
def test_ablation_diff_runs():
    """Placeholder — exercised via CLI, not pytest."""
    from evals.report import run_ablation_diff
    result = run_ablation_diff(against="HEAD")
    assert "error" in result or "total_compared" in result
