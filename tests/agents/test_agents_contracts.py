"""v0.3 contract tests — 5-kind routing, dynamic life_area.

No quota burn: all LLM calls are monkey-patched.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

from monogram.agents import classifier, extractor, orchestrator, verifier, writer
from monogram.agents.classifier import Classification
from monogram.agents.extractor import (
    ConceptDrop,
    CredentialEntry,
    LifeEntry,
    PersonalLog,
    ProjectUpdate,
    QueryIntent,
)
from monogram.agents.orchestrator import PipelinePlan
from monogram.agents.verifier import Contradiction, VerifyResult
from monogram.agents.writer import FileChange
from monogram.vault_config import load_vault_config


@pytest.fixture(autouse=True)
def _clear_vault_cache():
    load_vault_config.cache_clear()
    yield
    load_vault_config.cache_clear()


@pytest.fixture
def mock_vault(monkeypatch):
    """Force VaultConfig to return defaults (no actual repo read)."""
    monkeypatch.setattr("monogram.vault_config.github_store.read", lambda p: "")
    load_vault_config.cache_clear()


# ── Each stage module has an async run() ───────────────────────────────


@pytest.mark.parametrize(
    "mod",
    [orchestrator, classifier, extractor, verifier, writer],
    ids=["orchestrator", "classifier", "extractor", "verifier", "writer"],
)
def test_module_has_async_run(mod):
    assert hasattr(mod, "run"), f"{mod.__name__} missing run()"
    assert inspect.iscoroutinefunction(mod.run), f"{mod.__name__}.run not async"


# ── Schema roundtrip ──


def test_pipeline_plan_schema_roundtrip():
    p = PipelinePlan(
        operation="ingest_drop",
        preload_files=["projects/paper-a.md"],
        skip_stages=[],
        notes="project status update",
    )
    dumped = p.model_dump_json()
    assert PipelinePlan.model_validate_json(dumped).operation == "ingest_drop"


# ── Classification — path derivation for 5 kinds ──


def test_classification_project_path(mock_vault):
    c = Classification(
        drop_type="task", target_kind="project", slug="paper-a",
        confidence="medium", tags=["phase-0"], reasoning="status update",
    )
    assert c.target_path == "projects/paper-a.md"
    assert Classification.model_validate_json(c.model_dump_json()).target_kind == "project"


def test_classification_life_path(mock_vault):
    c = Classification(
        drop_type="life_item", target_kind="life", life_area="shopping",
        slug="earbuds", confidence="high", reasoning="shopping item",
    )
    assert c.target_path == "life/shopping.md"


def test_classification_life_unknown_area_is_empty_path(mock_vault):
    # life_area isn't in VaultConfig.life_categories
    c = Classification(
        drop_type="life_item", target_kind="life",
        life_area="not-a-known-category",
        slug="x", confidence="low", reasoning="unrecognized area",
    )
    assert c.target_path == ""  # unroutable, caller should fall back


def test_classification_wiki_is_flat(mock_vault):
    c = Classification(
        drop_type="technical_link", target_kind="wiki", slug="rtmpose",
        confidence="high", reasoning="tech note",
    )
    assert c.target_path == "wiki/rtmpose.md"
    assert "tech" not in c.target_path  # no category subfolder in v0.3


def test_classification_credential_path(mock_vault):
    c = Classification(
        drop_type="credential", target_kind="credential",
        slug="openai-api-key", confidence="high", reasoning="credential",
    )
    assert c.target_path == "life/credentials/openai-api-key.md"


def test_classification_daily_only_empty_path(mock_vault):
    c = Classification(
        drop_type="personal_thought", target_kind="daily_only",
        slug="reflection", confidence="medium", reasoning="",
    )
    assert c.target_path == ""


def test_classification_rejects_float_confidence(mock_vault):
    with pytest.raises(Exception):
        Classification(
            drop_type="task", target_kind="project", slug="x",
            confidence=0.7, reasoning="",  # type: ignore[arg-type]
        )


def test_classification_rejects_unknown_target_kind(mock_vault):
    with pytest.raises(Exception):
        Classification(
            drop_type="task", target_kind="legacy_scheduler",  # type: ignore[arg-type]
            slug="x", confidence="low", reasoning="",
        )


# ── Extractor variants ──


def test_extractor_variants_instantiate():
    a = ProjectUpdate(project_name="paper-a", progress_note="phase 0 done")
    b = ConceptDrop(title="t", summary="s")
    c = PersonalLog(content="thought at 2am")
    d = QueryIntent(question="what's due this week?", scope="scheduler")
    e = LifeEntry(title="earbuds", content="need wireless")
    f = CredentialEntry(label="API", body="sk-xxx")
    for obj, kind in [
        (a, "project_update"), (b, "concept_drop"), (c, "personal_log"),
        (d, "query_intent"), (e, "life_entry"), (f, "credential_entry"),
    ]:
        assert obj.kind == kind


# ── Verifier schema ──


def test_verifier_schema():
    v = VerifyResult(ok=True, target_confidence="high", reasoning="clean")
    assert v.contradictions == []
    assert v.escalate is False
    Contradiction(
        existing_path="wiki/paper-a.md",
        existing_claim="baseline A",
        new_claim="baseline B",
        severity="direct",
    )


# ── Stub behavior ──


def test_extractor_without_classification_returns_personal_log():
    result = asyncio.run(extractor.run("mark paper-a phase 0 done"))
    assert isinstance(result, PersonalLog)
    assert result.content == "mark paper-a phase 0 done"


def test_verifier_without_inputs_returns_ok():
    result = asyncio.run(verifier.run(extraction=None, classification=None))
    assert isinstance(result, VerifyResult)
    assert result.ok is True


# ── Classifier normalization defends against Gemini drift ──


def _patch_classifier_complete(monkeypatch, classification_json: str):
    async def fake(prompt, **kw):
        return classification_json
    monkeypatch.setattr("monogram.agents.classifier.complete", fake)


def test_classifier_normalizes_bad_literals(mock_vault, monkeypatch):
    bad = (
        '{"drop_type":"Task","target_kind":"Project",'
        '"life_area":null,"slug":"paper a","confidence":"HIGH",'
        '"tags":[],"reasoning":"noisy output"}'
    )
    _patch_classifier_complete(monkeypatch, bad)
    plan = PipelinePlan(operation="ingest_drop")
    result = asyncio.run(classifier.run("mark paper-a done", plan))
    assert result.drop_type == "task"
    assert result.target_kind == "project"
    assert result.confidence == "high"
    assert result.slug == "paper-a"


def test_classifier_demotes_life_with_unknown_area(mock_vault, monkeypatch):
    """When LLM says life but life_area doesn't match config, demote to daily_only."""
    bad = (
        '{"drop_type":"life_item","target_kind":"life",'
        '"life_area":"not-a-category-at-all","slug":"x",'
        '"confidence":"medium","tags":[],"reasoning":"unroutable"}'
    )
    _patch_classifier_complete(monkeypatch, bad)
    plan = PipelinePlan(operation="ingest_drop")
    result = asyncio.run(classifier.run("something", plan))
    assert result.target_kind == "daily_only"
    assert result.life_area is None


def test_classifier_coerces_life_area_typo(mock_vault, monkeypatch):
    """LLM drift: 'shopping-list' → 'shopping' via prefix match."""
    bad = (
        '{"drop_type":"life_item","target_kind":"life",'
        '"life_area":"shopping-list","slug":"earbuds",'
        '"confidence":"high","tags":[],"reasoning":"drift"}'
    )
    _patch_classifier_complete(monkeypatch, bad)
    plan = PipelinePlan(operation="ingest_drop")
    result = asyncio.run(classifier.run("earbuds", plan))
    assert result.target_kind == "life"
    assert result.life_area == "shopping"


# ── Writer — 5-kind dispatch ──


def _project_classification():
    return Classification(
        drop_type="task", target_kind="project", slug="paper-a",
        confidence="high", tags=["phase-0"], reasoning="",
    )


def test_writer_project_has_4_paths_with_memory():
    payload = ProjectUpdate(project_name="paper-a", progress_note="phase 0 done")
    v = VerifyResult(ok=True, target_confidence="high", reasoning="ok")
    fc = asyncio.run(writer.run(payload, v, _project_classification()))
    assert fc.primary_path == "projects/paper-a.md"
    assert "projects/paper-a.md" in fc.writes
    assert "MEMORY.md" in fc.writes
    assert any("drops.md" in k for k in fc.writes)
    assert "log/decisions.md" in fc.writes
    target = fc.writes["projects/paper-a.md"]
    assert target.startswith("---\n")
    assert "confidence: high" in target
    assert "phase 0 done" in target


def test_writer_life_appends_no_memory(mock_vault):
    cls = Classification(
        drop_type="life_item", target_kind="life", life_area="shopping",
        slug="earbuds", confidence="medium", reasoning="",
    )
    payload = LifeEntry(title="earbuds", content="wireless, ANC")
    v = VerifyResult(ok=True, target_confidence="medium", reasoning="ok")
    fc = asyncio.run(writer.run(payload, v, cls))
    assert "life/shopping.md" in fc.writes
    assert "MEMORY.md" not in fc.writes
    body = fc.writes["life/shopping.md"]
    assert "earbuds" in body
    # Second entry appends to existing content
    cls2 = Classification(
        drop_type="life_item", target_kind="life", life_area="shopping",
        slug="keyboard", confidence="medium", reasoning="",
    )
    payload2 = LifeEntry(title="keyboard", content="mechanical")
    fc2 = asyncio.run(
        writer.run(payload2, v, cls2, existing_target=body)
    )
    body2 = fc2.writes["life/shopping.md"]
    assert "earbuds" in body2
    assert "keyboard" in body2


def test_writer_wiki_flat_plus_index(mock_vault):
    cls = Classification(
        drop_type="technical_link", target_kind="wiki", slug="rtmpose",
        confidence="high", tags=["pose-estimation"], reasoning="",
    )
    payload = ConceptDrop(title="RTMPose", summary="500 FPS pose estimation")
    v = VerifyResult(ok=True, target_confidence="high", reasoning="ok")
    fc = asyncio.run(writer.run(payload, v, cls))
    assert "wiki/rtmpose.md" in fc.writes
    assert "wiki/index.md" in fc.writes
    assert "MEMORY.md" in fc.writes
    idx = fc.writes["wiki/index.md"]
    assert "[[rtmpose]]" in idx


def test_writer_credential_isolated(mock_vault):
    cls = Classification(
        drop_type="credential", target_kind="credential",
        slug="openai-api-key", confidence="high", reasoning="",
    )
    payload = CredentialEntry(label="OpenAI", body="sk-SECRET")
    v = VerifyResult(ok=True, target_confidence="high", reasoning="ok")
    fc = asyncio.run(writer.run(payload, v, cls))
    # File written
    assert "life/credentials/openai-api-key.md" in fc.writes
    # No frontmatter (credentials are never re-read by LLM)
    body = fc.writes["life/credentials/openai-api-key.md"]
    assert not body.startswith("---\n")
    assert "sk-SECRET" in body
    # drops.md redacted
    drops = [v for k, v in fc.writes.items() if "drops.md" in k][0]
    assert "sk-SECRET" not in drops
    assert "openai-api-key" not in drops
    assert "(redacted)" in drops
    # No MEMORY pointer
    assert "MEMORY.md" not in fc.writes
    # Commit message redacted
    assert "openai" not in fc.commit_message.lower()


def test_writer_daily_only_minimal():
    cls = Classification(
        drop_type="personal_thought", target_kind="daily_only",
        slug="reflection", confidence="medium", reasoning="",
    )
    payload = PersonalLog(content="stuck")
    v = VerifyResult(ok=True, target_confidence="medium", reasoning="ok")
    fc = asyncio.run(writer.run(payload, v, cls))
    assert "MEMORY.md" not in fc.writes
    assert not any(k.startswith("projects/") for k in fc.writes)
    assert any("drops.md" in k for k in fc.writes)
    assert "log/decisions.md" in fc.writes


def test_writer_confidence_is_always_enum():
    payload = ProjectUpdate(project_name="x", progress_note="y")
    cls = _project_classification()
    for conf in ("high", "medium", "low"):
        v = VerifyResult(ok=True, target_confidence=conf, reasoning="ok")
        fc = asyncio.run(writer.run(payload, v, cls))
        target = fc.writes["projects/paper-a.md"]
        assert f"confidence: {conf}" in target
        assert "0.5" not in target
