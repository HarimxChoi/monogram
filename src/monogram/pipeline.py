"""5-stage pipeline runner. No git side-effect — commit happens via github_store.write_multi()."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import github_store
from .agents import classifier, extractor, orchestrator, verifier, writer
from .agents.writer import FileChange
from .models import get_model
from .pipeline_log import StageTimer, log_pipeline_run
from .safe_read import safe_read


@dataclass
class PipelineResult:
    file_change: FileChange | None = None
    stages_executed: list[str] = field(default_factory=list)
    escalated: bool = False
    blocked_reason: str | None = None


async def run_pipeline(payload: str) -> PipelineResult:
    start = time.monotonic()
    stages: list[str] = []
    classification = None
    verification = None
    escalated = False
    result: PipelineResult | None = None
    timer = StageTimer()

    try:
        # Credential quarantine: short-circuit before any LLM call so secret never crosses the provider.
        from .secret_filter import classify_secret

        cred_slug = classify_secret(payload)
        if cred_slug is not None:
            from datetime import datetime, timezone

            from .agents.classifier import Classification
            from .agents.extractor import CredentialEntry
            from .agents.verifier import VerifyResult

            classification = Classification(
                drop_type="credential",
                target_kind="credential",
                slug=cred_slug,
                confidence="high",
                tags=[],
                reasoning="pre-LLM secret-shape match (no LLM call)",
            )
            verification = VerifyResult(
                ok=True,
                target_confidence="high",
                escalate=False,
                reasoning="credential quarantined pre-LLM",
            )
            extraction = CredentialEntry(
                label=cred_slug.replace("-", " "), body=payload.strip()
            )
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            file_change = await writer.run(
                extraction,
                verification,
                classification,
                existing_drops=safe_read(f"daily/{today}/drops.md"),
                existing_decisions=safe_read("log/decisions.md"),
            )
            stages.append("credential_gate")
            result = PipelineResult(file_change=file_change, stages_executed=stages)
            return result

        with timer.stage("orchestrator"):
            plan = await orchestrator.run(payload)
        stages.append("orchestrator")

        with timer.stage("classifier"):
            classification = await classifier.run(payload, plan)
        stages.append("classifier")

        # safe_read blocks life/credentials/* even if LLM misdirects (defense-in-depth).
        target_content = (
            safe_read(classification.target_path)
            if classification.target_path
            else ""
        )
        memory_content = safe_read("MEMORY.md")

        with timer.stage("extractor"):
            extraction = await extractor.run(payload, classification)
        stages.append("extractor")

        with timer.stage("verifier"):
            verification = await verifier.run(
                extraction,
                classification,
                target_content=target_content,
                memory_content=memory_content,
            )
        stages.append("verifier")

        if verification.escalate:
            with timer.stage("extractor"):
                extraction = await extractor.run(
                    payload, classification, model_override=get_model("mid")
                )
            with timer.stage("verifier"):
                verification = await verifier.run(
                    extraction,
                    classification,
                    target_content=target_content,
                    memory_content=memory_content,
                )
            escalated = True

            if verification.escalate:
                result = PipelineResult(
                    stages_executed=stages,
                    escalated=True,
                    blocked_reason="two escalations — ask the user",
                )
                return result

        if not verification.ok:
            result = PipelineResult(
                stages_executed=stages,
                escalated=escalated,
                blocked_reason=verification.reasoning,
            )
            return result

        from datetime import datetime, timezone

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        existing_drops = safe_read(f"daily/{today}/drops.md")
        existing_decisions = safe_read("log/decisions.md")
        existing_wiki_index = (
            safe_read("wiki/index.md")
            if classification.target_kind == "wiki"
            else ""
        )

        with timer.stage("writer"):
            file_change = await writer.run(
                extraction,
                verification,
                classification,
                existing_target=target_content,
                existing_memory=memory_content,
                existing_drops=existing_drops,
                existing_decisions=existing_decisions,
                existing_wiki_index=existing_wiki_index,
            )
        stages.append("writer")

        result = PipelineResult(
            file_change=file_change,
            stages_executed=stages,
            escalated=escalated,
        )
        return result
    finally:
        # Best-effort trace: captures happy, blocked, and raised paths equally.
        duration_ms = int((time.monotonic() - start) * 1000)
        log_pipeline_run(
            payload=payload,
            classification=classification,
            verification=verification,
            stages=stages,
            escalated=escalated,
            duration_ms=duration_ms,
            blocked_reason=(result.blocked_reason if result else None),
            stage_latency_ms=timer.latencies_ms,
        )
