"""B7 tests — weekly lint pass. Mocked github_store + safe_read."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from monogram.wiki_lint import (
    LintReport,
    _check_broken_wikilinks,
    _check_orphan_pointers,
    _check_stale_confidence,
    _regenerate_wiki_index,
    format_lint_section,
    run_lint,
)


def _iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).replace(
        microsecond=0
    ).isoformat()


@patch("monogram.wiki_lint.github_store")
@patch("monogram.wiki_lint.safe_read")
def test_stale_high_confidence_demoted(mock_safe_read, mock_store):
    old_iso = _iso_days_ago(45)
    wiki_content = (
        f"---\nconfidence: high\nlast_confirmed: {old_iso}\n---\n\nbody\n"
    )
    mock_safe_read.side_effect = lambda p: wiki_content if p == "wiki/old.md" else ""
    mock_store.parse_metadata.return_value = (
        {"confidence": "high", "last_confirmed": old_iso},
        "body\n",
    )
    mock_store.serialize_with_metadata.side_effect = (
        lambda fm, body: f"---\nconfidence: {fm['confidence']}\n---\n\n{body}"
    )

    report = LintReport()
    _check_stale_confidence(["wiki/old.md"], report)
    assert len(report.demoted_confidence) == 1
    path, from_c, to_c = report.demoted_confidence[0]
    assert path == "wiki/old.md"
    assert from_c == "high" and to_c == "medium"
    assert "wiki/old.md" in report.writes


@patch("monogram.wiki_lint.github_store")
@patch("monogram.wiki_lint.safe_read")
def test_recent_high_confidence_not_demoted(mock_safe_read, mock_store):
    recent_iso = _iso_days_ago(5)
    content = f"---\nconfidence: high\nlast_confirmed: {recent_iso}\n---\n\nbody\n"
    mock_safe_read.return_value = content
    mock_store.parse_metadata.return_value = (
        {"confidence": "high", "last_confirmed": recent_iso},
        "body\n",
    )

    report = LintReport()
    _check_stale_confidence(["wiki/fresh.md"], report)
    assert report.demoted_confidence == []


@patch("monogram.wiki_lint.safe_read")
def test_broken_wikilinks_detected(mock_safe_read):
    contents = {
        "wiki/a.md": "some text [[b]] and [[missing]]",
        "wiki/b.md": "linked from a",
    }
    mock_safe_read.side_effect = lambda p: contents.get(p, "")
    report = LintReport()
    _check_broken_wikilinks(["wiki/a.md", "wiki/b.md"], report)
    targets = [t for _, t in report.broken_wikilinks]
    assert "missing" in targets
    assert "b" not in targets


@patch("monogram.wiki_lint.github_store")
@patch("monogram.wiki_lint.safe_read")
def test_regenerate_index_produces_canonical_format(mock_safe_read, mock_store):
    wiki_paths = ["wiki/alpha.md", "wiki/beta.md"]
    contents = {
        "wiki/alpha.md": "---\ntags:\n  - pose\ncreated: 2026-04-10\n---\n\n# Alpha\n\nFirst line here\n",
        "wiki/beta.md": "---\ntags:\n  - math\ncreated: 2026-04-11\n---\n\n# Beta\n\nBeta summary\n",
        "wiki/index.md": "# Wiki Index\n\n(old stale content)\n",
    }
    mock_safe_read.side_effect = lambda p: contents.get(p, "")
    mock_store.parse_metadata.side_effect = lambda c: (
        ({"tags": ["pose"], "created": "2026-04-10"}, "# Alpha\n\nFirst line here\n")
        if "alpha" in c.lower()
        else ({"tags": ["math"], "created": "2026-04-11"}, "# Beta\n\nBeta summary\n")
    )

    report = LintReport()
    _regenerate_wiki_index(wiki_paths, report)
    assert report.index_regenerated is True
    new_index = report.writes["wiki/index.md"]
    assert "- [[alpha]]" in new_index
    assert "- [[beta]]" in new_index
    assert "#pose" in new_index
    assert "(2026-04-10)" in new_index
    # Old stale content is gone
    assert "(old stale content)" not in new_index


@patch("monogram.wiki_lint.safe_read")
def test_orphan_pointers_in_memory(mock_safe_read):
    memory = (
        "paper-a            projects/paper-a.md              ok  [high]\n"
        "ghost              wiki/deleted-file.md             gone  [high]\n"
    )
    def fake_read(p):
        if p == "MEMORY.md":
            return memory
        if p == "projects/paper-a.md":
            return "content"
        return ""  # deleted-file.md missing

    mock_safe_read.side_effect = fake_read
    report = LintReport()
    _check_orphan_pointers(["wiki/other.md"], report)
    # ghost pointer should be flagged
    names = [n for n, _ in report.orphan_pointers]
    assert "ghost" in names
    assert "paper-a" not in names


def test_format_lint_section_empty_is_clean():
    report = LintReport()
    section = format_lint_section(report)
    assert "Health check" in section
    assert "Clean." in section


def test_format_lint_section_with_findings():
    report = LintReport(
        demoted_confidence=[("wiki/x.md", "high", "medium")],
        broken_wikilinks=[("wiki/a.md", "missing")],
        orphan_pointers=[("ghost", "wiki/gone.md")],
        index_regenerated=True,
    )
    section = format_lint_section(report)
    assert "Confidence decay" in section
    assert "Broken wikilinks" in section
    assert "Orphan" in section
    assert "regenerated from filesystem" in section


@patch("monogram.wiki_lint._list_wiki_files", return_value=[])
@patch("monogram.wiki_lint.safe_read", return_value="")
def test_run_lint_empty_repo_is_clean(mock_read, mock_list):
    report = run_lint()
    assert report.summary().startswith("orphan_pointers=0")
    # Even with no wiki files, index regeneration still runs → header-only index
    assert report.index_regenerated is True
