"""Pipeline tests — end-to-end through all 5 stages.

v0.3: covers all 5 target_kind routing paths.
"""
from __future__ import annotations

import asyncio

import pytest

from monogram.agents.classifier import Classification
from monogram.agents.extractor import (
    CredentialEntry,
    LifeEntry,
    PersonalLog,
    ProjectUpdate,
    ConceptDrop,
)
from monogram.agents.orchestrator import PipelinePlan
from monogram.agents.verifier import VerifyResult
from monogram.pipeline import run_pipeline
from monogram.vault_config import load_vault_config


@pytest.fixture(autouse=True)
def _clear_vault_cache():
    load_vault_config.cache_clear()
    yield
    load_vault_config.cache_clear()


_MOCK_PLAN = PipelinePlan(
    operation="update_project",
    preload_files=[],
    notes="mock orchestrator",
)

_MOCK_VERIFY = VerifyResult(
    ok=True,
    target_confidence="high",
    escalate=False,
    reasoning="mock verifier",
)


def _patch_pipeline(
    monkeypatch,
    *,
    classification: Classification,
    extraction,
    verify: VerifyResult = _MOCK_VERIFY,
):
    async def fake_orch(prompt, schema, **kw):
        return _MOCK_PLAN

    async def fake_cls(prompt, **kw):
        return classification.model_dump_json()

    async def fake_ext(prompt, **kw):
        return extraction.model_dump_json()

    async def fake_ver(prompt, schema, **kw):
        return verify

    monkeypatch.setattr("monogram.agents.orchestrator.extract", fake_orch)
    monkeypatch.setattr("monogram.agents.classifier.complete", fake_cls)
    monkeypatch.setattr("monogram.agents.extractor.complete", fake_ext)
    monkeypatch.setattr("monogram.agents.verifier.llm_extract", fake_ver)
    monkeypatch.setattr("monogram.pipeline.safe_read", lambda p: "")
    # vault_config reads github_store directly — short-circuit to defaults
    monkeypatch.setattr("monogram.vault_config.github_store.read", lambda p: "")


# ── PROJECT KIND ────────────────────────────────────────────────────────


def _project_classification() -> Classification:
    return Classification(
        drop_type="task",
        target_kind="project",
        slug="paper-a",
        confidence="high",
        tags=["phase-0"],
        reasoning="mock",
    )


def test_project_drop_writes_all_4_paths(monkeypatch):
    _patch_pipeline(
        monkeypatch,
        classification=_project_classification(),
        extraction=ProjectUpdate(project_name="paper-a", progress_note="phase 0 done"),
    )
    fc = asyncio.run(run_pipeline("mark paper-a phase 0 done")).file_change
    assert fc is not None
    paths = set(fc.writes.keys())
    assert "projects/paper-a.md" in paths
    assert any("daily/" in p and "drops.md" in p for p in paths)
    assert "MEMORY.md" in paths
    assert "log/decisions.md" in paths


def test_project_target_has_yaml_frontmatter(monkeypatch):
    _patch_pipeline(
        monkeypatch,
        classification=_project_classification(),
        extraction=ProjectUpdate(project_name="paper-a", progress_note="phase 0 done"),
    )
    fc = asyncio.run(run_pipeline("mark paper-a phase 0 done")).file_change
    target = fc.writes["projects/paper-a.md"]
    assert target.startswith("---\n")
    assert "confidence: high" in target
    assert "phase 0 done" in target


# ── LIFE KIND ───────────────────────────────────────────────────────────


def test_life_drop_appends_no_memory_pointer(monkeypatch):
    cls = Classification(
        drop_type="life_item",
        target_kind="life",
        life_area="shopping",
        slug="wireless-earbuds",
        confidence="high",
        tags=[],
        reasoning="shopping item",
    )
    _patch_pipeline(
        monkeypatch,
        classification=cls,
        extraction=LifeEntry(title="wireless earbuds", content="need a pair"),
    )
    fc = asyncio.run(run_pipeline("need wireless earbuds")).file_change
    assert fc is not None
    assert "life/shopping.md" in fc.writes
    body = fc.writes["life/shopping.md"]
    assert "wireless earbuds" in body
    assert body.startswith("# life/shopping") or body.strip().startswith("##")
    # Life kind has NO MEMORY pointer
    assert "MEMORY.md" not in fc.writes


# ── WIKI KIND ───────────────────────────────────────────────────────────


def test_wiki_drop_flat_path_plus_index_line(monkeypatch):
    cls = Classification(
        drop_type="technical_link",
        target_kind="wiki",
        slug="rtmpose",
        confidence="high",
        tags=["pose-estimation", "inference"],
        reasoning="tech note",
    )
    _patch_pipeline(
        monkeypatch,
        classification=cls,
        extraction=ConceptDrop(
            title="RTMPose", summary="Real-time pose estimation at 500 FPS",
        ),
    )
    fc = asyncio.run(run_pipeline("RTMPose 500 FPS")).file_change
    assert fc is not None
    assert "wiki/rtmpose.md" in fc.writes  # FLAT — no category subfolder
    assert "wiki/index.md" in fc.writes
    assert "MEMORY.md" in fc.writes
    idx = fc.writes["wiki/index.md"]
    assert "[[rtmpose]]" in idx
    assert "#pose-estimation" in idx or "#inference" in idx


# ── CREDENTIAL KIND ─────────────────────────────────────────────────────


def test_credential_drop_is_isolated(monkeypatch):
    cls = Classification(
        drop_type="credential",
        target_kind="credential",
        slug="openai-api-key",
        confidence="high",
        tags=[],
        reasoning="credential capture",
    )
    _patch_pipeline(
        monkeypatch,
        classification=cls,
        extraction=CredentialEntry(label="OpenAI API key", body="sk-SECRET"),
    )
    fc = asyncio.run(run_pipeline("openai key sk-SECRET")).file_change
    assert fc is not None
    # Credential file is written
    assert "life/credentials/openai-api-key.md" in fc.writes
    # But drops.md content is REDACTED — no slug, no value leaked
    drops = [v for k, v in fc.writes.items() if "drops.md" in k][0]
    assert "(redacted)" in drops
    assert "openai-api-key" not in drops
    assert "sk-SECRET" not in drops
    # MEMORY.md is NOT updated — slug names themselves are sensitive
    assert "MEMORY.md" not in fc.writes
    # Decisions log has slug redacted
    decisions = fc.writes["log/decisions.md"]
    assert "openai-api-key" not in decisions
    assert "(redacted)" in decisions
    # Commit message is redacted
    assert "openai" not in fc.commit_message.lower()
    assert "redacted" in fc.commit_message.lower()


# ── DAILY_ONLY KIND ─────────────────────────────────────────────────────


def test_daily_only_drop_has_no_stable_target(monkeypatch):
    cls = Classification(
        drop_type="personal_thought",
        target_kind="daily_only",
        slug="reflection",
        confidence="medium",
        tags=[],
        reasoning="reflection",
    )
    _patch_pipeline(
        monkeypatch,
        classification=cls,
        extraction=PersonalLog(content="feeling stuck"),
    )
    fc = asyncio.run(run_pipeline("feeling stuck")).file_change
    assert fc is not None
    # No stable target path, no MEMORY
    assert "MEMORY.md" not in fc.writes
    assert not any(k.startswith("projects/") for k in fc.writes)
    assert not any(k.startswith("wiki/") for k in fc.writes)
    assert not any(k.startswith("life/") for k in fc.writes)
    # drops + decisions always present
    assert any("drops.md" in k for k in fc.writes)
    assert "log/decisions.md" in fc.writes


# ── BLOCKING / ESCALATION ───────────────────────────────────────────────


def test_pipeline_blocked_when_verifier_says_no(monkeypatch):
    _patch_pipeline(
        monkeypatch,
        classification=_project_classification(),
        extraction=ProjectUpdate(project_name="paper-a", progress_note="x"),
        verify=VerifyResult(
            ok=False, target_confidence="low", escalate=False,
            reasoning="test: verifier blocked this",
        ),
    )
    result = asyncio.run(run_pipeline("something suspicious"))
    assert result.file_change is None
    assert result.blocked_reason == "test: verifier blocked this"


def test_pipeline_escalation_then_blocked(monkeypatch):
    _patch_pipeline(
        monkeypatch,
        classification=_project_classification(),
        extraction=ProjectUpdate(project_name="paper-a", progress_note="x"),
        verify=VerifyResult(
            ok=False, target_confidence="low", escalate=True,
            reasoning="always escalate",
        ),
    )
    result = asyncio.run(run_pipeline("ambiguous input"))
    assert result.escalated is True
    assert result.file_change is None
    assert "two escalations" in (result.blocked_reason or "")


def test_pipeline_stages_list_correct_on_success(monkeypatch):
    _patch_pipeline(
        monkeypatch,
        classification=_project_classification(),
        extraction=ProjectUpdate(project_name="paper-a", progress_note="x"),
    )
    result = asyncio.run(run_pipeline("hello"))
    assert result.stages_executed == [
        "orchestrator", "classifier", "extractor", "verifier", "writer",
    ]


# ── Live pipeline ───────────────────────────────────────────────────────


def _has_real_key():
    try:
        from monogram.config import load_config
        key = load_config().gemini_api_key
    except Exception:
        return False
    return bool(key) and not key.lower().startswith(("test", "dummy", "fake"))


@pytest.mark.live_llm
@pytest.mark.skipif(not _has_real_key(), reason="real GEMINI_API_KEY required")
def test_pipeline_live_end_to_end():
    result = asyncio.run(run_pipeline("mark paper-a phase 0 done"))
    fc = result.file_change
    assert fc is not None, f"blocked: {result.blocked_reason}"
    assert len(fc.writes) >= 4
    assert fc.primary_path
    assert fc.confidence in ("high", "medium", "low")
