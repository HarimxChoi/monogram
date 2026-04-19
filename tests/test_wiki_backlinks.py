"""v0.3b wiki backlinks tests."""
from __future__ import annotations

from unittest.mock import patch

from monogram.wiki_backlinks import (
    _MAX_BACKLINKS_PER_DROP,
    append_backlink,
    compute_backlink_writes,
    find_peers,
)


_SAMPLE_INDEX = """\
# Wiki Index

- [[rtmpose]] — real-time pose [#pose #inference] (2026-04-17)
- [[conformal]] — conformal prediction [#calibration #stats] (2026-04-17)
- [[yolo]] — object detection [#detection #pose] (2026-04-16)
- [[bayes]] — bayesian calibration [#calibration #bayes] (2026-04-15)
- [[sleep]] — sleep consistency [#health] (2026-04-15)
"""


def test_find_peers_empty_tags_returns_empty():
    assert find_peers("new", [], _SAMPLE_INDEX) == []


def test_find_peers_excludes_self():
    peers = find_peers("rtmpose", ["pose"], _SAMPLE_INDEX)
    assert "rtmpose" not in peers


def test_find_peers_ranks_by_overlap():
    # New tags: pose + calibration. Overlap:
    #  rtmpose    → {pose} = 1
    #  conformal  → {calibration} = 1
    #  yolo       → {pose} = 1
    #  bayes      → {calibration} = 1
    #  sleep      → {} = 0
    # Top 5 sorted by overlap desc, slug asc: bayes, conformal, rtmpose, yolo
    peers = find_peers("new-entry", ["pose", "calibration"], _SAMPLE_INDEX)
    assert "sleep" not in peers
    assert set(peers) == {"bayes", "conformal", "rtmpose", "yolo"}


def test_find_peers_cap_at_max():
    many_index = "# Wiki Index\n\n" + "\n".join(
        f"- [[entry-{i}]] — summary [#common] (2026-04-01)" for i in range(20)
    )
    peers = find_peers("new", ["common"], many_index)
    assert len(peers) == _MAX_BACKLINKS_PER_DROP


def test_append_backlink_creates_section():
    body = "---\nconfidence: high\n---\n\n# Title\n\nbody text\n"
    out = append_backlink(body, "newcomer")
    assert "## Related" in out
    assert "- [[newcomer]]" in out
    assert "# Title" in out  # original preserved


def test_append_backlink_appends_to_existing_section():
    body = (
        "# Title\n\nbody\n\n"
        "## Related\n"
        "- [[existing]]\n"
    )
    out = append_backlink(body, "newcomer")
    assert "- [[existing]]" in out
    assert "- [[newcomer]]" in out


def test_append_backlink_is_idempotent():
    body = "# Title\n\n## Related\n- [[newcomer]]\n"
    assert append_backlink(body, "newcomer") == body


def test_append_backlink_respects_section_boundaries():
    """A Related section followed by another ## header should not swallow it."""
    body = (
        "# Title\n\n"
        "## Related\n"
        "- [[existing]]\n\n"
        "## References\n"
        "- [paper](url)\n"
    )
    out = append_backlink(body, "newcomer")
    # newcomer should be inserted in Related, not References
    related_idx = out.index("## Related")
    references_idx = out.index("## References")
    newcomer_idx = out.index("[[newcomer]]")
    assert related_idx < newcomer_idx < references_idx


@patch("monogram.wiki_backlinks.safe_read")
def test_compute_backlink_writes_integration(mock_read):
    def fake_read(path):
        if path == "wiki/rtmpose.md":
            return "# RTMPose\n\nreal-time pose\n"
        if path == "wiki/conformal.md":
            return "# Conformal\n\ncalibration method\n"
        return ""

    mock_read.side_effect = fake_read
    writes = compute_backlink_writes(
        new_slug="new-entry",
        new_tags=["pose", "calibration"],
        index_content=_SAMPLE_INDEX,
    )
    # Both peers should have writes
    assert "wiki/rtmpose.md" in writes
    assert "wiki/conformal.md" in writes
    assert "[[new-entry]]" in writes["wiki/rtmpose.md"]
    assert "[[new-entry]]" in writes["wiki/conformal.md"]


@patch("monogram.wiki_backlinks.safe_read")
def test_compute_backlink_writes_skips_missing_files(mock_read):
    """If a peer is in the index but file was deleted, skip gracefully."""
    mock_read.return_value = ""  # all reads return empty
    writes = compute_backlink_writes(
        new_slug="new",
        new_tags=["pose"],
        index_content=_SAMPLE_INDEX,
    )
    assert writes == {}
