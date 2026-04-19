"""LLM integration tests. Skipped if GEMINI_API_KEY not set or obviously fake."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import BaseModel

from monogram.llm import complete, complete_vision, extract


def _has_real_key() -> bool:
    try:
        from monogram.config import load_config

        key = load_config().gemini_api_key
    except Exception:
        return False
    return bool(key) and not key.lower().startswith(("test", "dummy", "fake"))


pytestmark = [
    pytest.mark.skipif(
        not _has_real_key(), reason="GEMINI_API_KEY not set (or is a dummy value)"
    ),
    pytest.mark.live_llm,
]


def test_basic_completion():
    result = asyncio.run(complete("Say hello in exactly 2 words."))
    assert isinstance(result, str)
    assert len(result.split()) <= 6, f"Too long: {result!r}"


def test_json_mode_returns_parsable_json():
    import json

    result = asyncio.run(
        complete(
            'Return JSON: {"status": "ok"}',
            response_format={"type": "json_object"},
        )
    )
    parsed = json.loads(result)
    assert parsed.get("status") == "ok"


class Pick(BaseModel):
    color: str
    confidence: float


def test_extract_returns_pydantic_instance():
    result = asyncio.run(
        extract(
            "Pick one primary color (red, green, or blue). "
            "Return a confidence score between 0 and 1.",
            Pick,
        )
    )
    assert isinstance(result, Pick)
    assert result.color.lower() in {"red", "green", "blue"}
    assert 0.0 <= result.confidence <= 1.0


def test_vision_describes_image():
    fixture = Path(__file__).parent / "fixtures" / "test.jpg"
    assert fixture.exists(), "missing tests/fixtures/test.jpg"
    result = asyncio.run(
        complete_vision(
            fixture.read_bytes(),
            "In one short sentence, what colors dominate this image?",
        )
    )
    assert isinstance(result, str) and len(result) > 5
