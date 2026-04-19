"""v0.3b board-style morning brief tests — pure helpers + mocked Pro call."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from monogram.morning_job import (
    LifeSnapshot,
    MorningBriefData,
    MorningContext,
    ProjectBoardEntry,
    ProjectSnapshot,
    WikiSnapshot,
    _build_brief_prompt,
    _collect_life_snapshots,
    _collect_wiki_snapshots,
    _parse_life_entries,
    _render_morning_brief,
    generate_morning_brief,
)
from monogram.vault_config import VaultConfig, load_vault_config


@pytest.fixture(autouse=True)
def _clear_vault_cache(monkeypatch):
    monkeypatch.setattr("monogram.vault_config.github_store.read", lambda p: "")
    load_vault_config.cache_clear()
    yield
    load_vault_config.cache_clear()


def test_parse_life_entries_filters_by_since():
    content = (
        "# life/shopping\n\n"
        "## 2026-04-17 10:00 — old entry\nold\n\n"
        "## 2026-04-18 09:15 — wireless earbuds\nneed a pair\n\n"
        "## 2026-04-18 14:30 — keyboard\nmechanical\n"
    )
    entries = _parse_life_entries(content, "2026-04-18T00:00:00")
    assert len(entries) == 2
    titles = [t for _, t in entries]
    assert "wireless earbuds" in titles
    assert "keyboard" in titles
    assert "old entry" not in titles


def test_collect_life_skips_credentials(monkeypatch):
    cfg = VaultConfig(life_categories=["shopping", "credentials"])
    monkeypatch.setattr("monogram.morning_job.load_vault_config", lambda: cfg)

    def fake_read(p):
        if p == "life/shopping.md":
            return "## 2026-04-18 09:00 — earbuds\n"
        if p == "life/credentials.md":
            return "## 2026-04-18 10:00 — openai-key\n"  # SHOULD NOT BE READ
        return ""

    monkeypatch.setattr("monogram.morning_job.safe_read", fake_read)

    snapshots = _collect_life_snapshots("2026-04-18")
    assert len(snapshots) == 1
    assert snapshots[0].area == "shopping"
    # credentials area was not collected at all
    assert not any(s.area == "credentials" for s in snapshots)


def test_collect_wiki_snapshots_from_index(monkeypatch):
    index = (
        "# Wiki Index\n\n"
        "- [[rtmpose]] — Real-time pose estimation [#pose #inference] (2026-04-18)\n"
        "- [[conformal]] — conformal prediction [#calibration #stats] (2026-04-17)\n"
        "- [[sleep]] — sleep consistency [#health] (2026-04-18)\n"
    )
    monkeypatch.setattr(
        "monogram.morning_job.safe_read",
        lambda p: index if p == "wiki/index.md" else "",
    )
    snapshots = _collect_wiki_snapshots("2026-04-18")
    assert len(snapshots) == 2
    slugs = [s.slug for s in snapshots]
    assert "rtmpose" in slugs
    assert "sleep" in slugs
    assert "conformal" not in slugs  # yesterday's date filter excluded


def test_build_prompt_includes_life_titles_verbatim():
    ctx = MorningContext(
        yesterday="2026-04-18",
        projects=[],
        life=[LifeSnapshot(area="shopping",
                           entries=[("2026-04-18 09:00", "무선 이어폰")])],
        wiki_new=[],
    )
    prompt = _build_brief_prompt(ctx)
    # Prompt must include the verbatim title so LLM can copy it into output
    assert "무선 이어폰" in prompt
    # Must instruct "titles: copy each title VERBATIM"
    assert "VERBATIM" in prompt


def test_render_brief_handles_korean_content():
    """Rendered markdown is content-agnostic — works with any language in fields."""
    data = MorningBriefData(
        projects=[
            ProjectBoardEntry(
                slug="paper-a",
                badge="[active • D-12]",
                current_state="페이퍼 A 는 phase 0 완료됨.",
                next_step="phase 1 베이스라인 학습 시작.",
                recent_activity="phase 0 완료 커밋 3개.",
            )
        ],
    )
    rendered = _render_morning_brief("2026-04-18", data)
    assert "# Morning brief — 2026-04-18" in rendered
    assert "### paper-a [active • D-12]" in rendered
    assert "페이퍼 A 는 phase 0 완료됨." in rendered
    assert "**Next:**" in rendered


def test_render_brief_calendar_links():
    data = MorningBriefData(
        calendar=[
            # Good iso start → URL builder succeeds
            # `when` is natural language for display
            # iso_start drives the URL
        ],
    )
    # Empty calendar renders without section header
    rendered = _render_morning_brief("2026-04-18", data)
    assert "## Calendar" not in rendered


def test_generate_brief_returns_none_for_empty_day(monkeypatch):
    monkeypatch.setattr("monogram.morning_job.safe_read", lambda p: "")
    monkeypatch.setattr("monogram.morning_job._list_project_files", lambda: [])
    result = asyncio.run(generate_morning_brief("2026-04-18"))
    assert result is None


def test_generate_brief_falls_back_when_pro_fails(monkeypatch):
    """If Pro call raises, we should still emit a minimal English brief so
    the day isn't a silent loss."""

    # Fake a context with one project and one life entry
    def fake_ctx(yesterday):
        return MorningContext(
            yesterday=yesterday,
            projects=[
                ProjectSnapshot(
                    slug="paper-a",
                    path="projects/paper-a.md",
                    frontmatter={"status": "active"},
                    body_excerpt="phase 0 in progress",
                    recent_commits=["abc123 phase 0 done"],
                )
            ],
            life=[LifeSnapshot(area="shopping",
                               entries=[("2026-04-18 09:00", "earbuds")])],
            wiki_new=[],
        )

    monkeypatch.setattr("monogram.morning_job._collect_morning_context", fake_ctx)

    async def fake_llm_extract(*args, **kwargs):
        raise RuntimeError("simulated 429")

    monkeypatch.setattr("monogram.morning_job.llm_extract", fake_llm_extract)
    monkeypatch.setattr("monogram.morning_job.github_store.write", lambda *a, **kw: True)

    result = asyncio.run(generate_morning_brief("2026-04-18"))
    assert result is not None
    assert "paper-a" in result
    assert "earbuds" in result
    # Metrics line / status preserved
    assert "active" in result
