"""Escalation — verifier escalate=True iff ambiguity/contradiction present.

Fixtures in escalation.jsonl carry `should_escalate: true|false`.
The test runs the full pipeline up to verifier and asserts the binary matches.
"""
from __future__ import annotations

import asyncio

import pytest

from evals.fixtures import load
from monogram.agents.classifier import run as classify
from monogram.agents.extractor import run as extract
from monogram.agents.orchestrator import PipelinePlan
from monogram.agents.verifier import run as verify


def _idfn(f):
    return f.get("id", "unknown")


@pytest.mark.parametrize("fixture", load("escalation"), ids=_idfn)
def test_escalation_binary(fixture, capture_store, cassette):
    """Verifier must escalate iff fixture says it should."""
    expected = fixture["expected"]
    if "should_escalate" not in expected:
        pytest.skip("no should_escalate expectation")

    # Seed existing content if the fixture provides it (contradiction case)
    for path, content in fixture["input"].get("seed_files", {}).items():
        capture_store.seed[path] = content

    payload = fixture["input"]["text"]

    async def _run():
        plan = PipelinePlan(operation="ingest_drop", preload_files=[])
        cls = await classify(payload, plan)
        ext = await extract(payload, cls)
        target = capture_store.read(cls.target_path)
        ver = await verify(ext, cls, target_content=target)
        return ver

    result = asyncio.run(_run())

    assert result.escalate == expected["should_escalate"], (
        f"{fixture['id']}: expected escalate={expected['should_escalate']}, "
        f"got {result.escalate} (reasoning: {result.reasoning})"
    )
