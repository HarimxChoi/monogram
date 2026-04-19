"""Extraction — per-drop-type schema shape."""
from __future__ import annotations

import asyncio

import pytest

from evals.fixtures import load_all
from monogram.agents.classifier import run as classify
from monogram.agents.extractor import run as extract
from monogram.agents.orchestrator import PipelinePlan


def _idfn(f):
    return f.get("id", "unknown")


@pytest.mark.parametrize("fixture", load_all(), ids=_idfn)
def test_extraction_has_discriminator(fixture, capture_store, cassette):
    """Every extracted payload must have a kind discriminator."""
    expected = fixture.get("expected", {})
    expected_kind = expected.get("extraction_kind")
    if expected_kind is None:
        pytest.skip("no extraction_kind expected")

    payload = fixture["input"]["text"]

    async def _run():
        plan = PipelinePlan(operation="ingest_drop", preload_files=[])
        cls = await classify(payload, plan)
        return await extract(payload, cls)

    result = asyncio.run(_run())
    assert result.kind == expected_kind, (
        f"{fixture['id']}: expected extraction kind={expected_kind}, got {result.kind}"
    )

    # Any required fields from the fixture must be present and truthy
    for field in expected.get("required_fields", []):
        assert getattr(result, field, None), (
            f"{fixture['id']}: required field {field!r} missing/empty"
        )
