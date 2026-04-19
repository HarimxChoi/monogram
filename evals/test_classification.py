"""Classification correctness — target_kind routing + slug validation.

Loads every fixture from evals/fixtures/*.jsonl (except harvested-*.jsonl
dated audit files, which are loaded via _accepted.jsonl only) and runs
the classifier against each. Asserts shape: target_kind, slug, path.
"""
from __future__ import annotations

import asyncio
import re

import pytest

from evals.fixtures import load_all
from monogram.agents.classifier import run as classify
from monogram.agents.orchestrator import PipelinePlan


_SLUG_RE = re.compile(r"^[a-z0-9-]+$")


def _idfn(f):
    return f.get("id", "unknown")


@pytest.mark.parametrize("fixture", load_all(), ids=_idfn)
def test_classification_shape(fixture, capture_store, cassette):
    """Every fixture's classification must match expected shape."""
    expected = fixture.get("expected", {})
    if "target_kind" not in expected:
        pytest.skip("fixture has no target_kind expectation")

    payload = fixture["input"]["text"]
    plan = PipelinePlan(operation="ingest_drop", preload_files=[])

    result = asyncio.run(classify(payload, plan))

    assert result.target_kind == expected["target_kind"], (
        f"{fixture['id']}: expected target_kind={expected['target_kind']}, "
        f"got {result.target_kind}"
    )

    if "slug" in expected:
        assert result.slug == expected["slug"], (
            f"{fixture['id']}: expected slug={expected['slug']}, got {result.slug}"
        )

    assert _SLUG_RE.match(result.slug), (
        f"{fixture['id']}: slug {result.slug!r} must match {_SLUG_RE.pattern}"
    )

    if "target_path" in expected:
        assert result.target_path == expected["target_path"], (
            f"{fixture['id']}: expected path={expected['target_path']}, "
            f"got {result.target_path}"
        )
