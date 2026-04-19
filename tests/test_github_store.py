"""GitHub store tests.

Metadata helpers are pure — always run.
Network ops are gated on a real PAT being loadable (skipped otherwise).
"""
from __future__ import annotations

import time

import pytest

from monogram.github_store import (
    append,
    build_metadata,
    parse_metadata,
    read,
    serialize_with_metadata,
    write,
)


# ── Pure metadata helpers ────────────────────────────────────────────────


def test_parse_metadata_returns_empty_for_plain_body():
    assert parse_metadata("just some body") == ({}, "just some body")


def test_build_metadata_defaults_are_sane():
    meta = build_metadata()
    for key in (
        "confidence",
        "sources",
        "created",
        "last_accessed",
        "last_confirmed",
        "tags",
    ):
        assert key in meta
    assert meta["confidence"] == "medium"
    assert meta["sources"] == 1
    assert "supersedes" not in meta
    assert "superseded_by" not in meta


def test_metadata_roundtrip():
    meta = build_metadata(
        confidence="high", sources=3, tags=["ml", "calibration"]
    )
    body = "# Title\n\nsome text here."
    serialized = serialize_with_metadata(meta, body)
    parsed_meta, parsed_body = parse_metadata(serialized)
    assert parsed_meta["confidence"] == "high"
    assert parsed_meta["sources"] == 3
    assert parsed_meta["tags"] == ["ml", "calibration"]
    assert "supersedes" not in parsed_meta
    assert parsed_body.strip() == body


def test_metadata_preserves_field_order_readability():
    """Field order matters for git diff readability."""
    meta = build_metadata(confidence="high")
    serialized = serialize_with_metadata(meta, "body")
    assert serialized.index("confidence:") < serialized.index("tags:")


# ── Network ops (gated) ──────────────────────────────────────────────────


def _has_real_pat() -> bool:
    try:
        from monogram.config import load_config

        pat = load_config().github_pat
    except Exception:
        return False
    return bool(pat) and pat.lower().startswith(("ghp_", "github_pat_"))


network = pytest.mark.skipif(
    not _has_real_pat(), reason="real GITHUB_PAT not set (or dummy value)"
)


@network
def test_read_existing_file():
    content = read("README.md")
    assert len(content) > 0


@network
def test_read_missing_file_returns_empty():
    assert read(f"definitely/not/here/{time.time()}.md") == ""


@pytest.mark.skip(reason="writes to live scheduler repo — would pollute prod data")
def test_write_and_read_roundtrip():
    path = f"tests/pytest_selftest_{int(time.time())}.md"
    assert write(path, "# pytest roundtrip", f"pytest selftest {path}")
    assert read(path) == "# pytest roundtrip"


@pytest.mark.skip(reason="writes to live scheduler repo — would pollute prod data")
def test_append_creates_then_appends():
    path = f"tests/pytest_append_{int(time.time())}.md"
    assert append(path, "# header", f"pytest append 1 {path}")
    assert append(path, "line 2", f"pytest append 2 {path}")
    content = read(path)
    assert "# header" in content
    assert "line 2" in content
