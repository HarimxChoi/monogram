"""5-stage pipeline runner. See docs/agents.md §6 for escalation flow.

Chains: Orchestrator → Classifier → Extractor → Verifier → Writer.
Reads existing context (target file + MEMORY.md) for verification.
Returns a PipelineResult containing a FileChange with ALL writes staged.
No git side-effect — the actual commit happens in listener/bot via
github_store.write_multi().
"""
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
    """Run the full 5-stage pipeline on a raw payload string.

    Reads existing context from the scheduler repo so the Verifier can
    actually check for contradictions. Writer produces ALL writes
    (stable target + drops.md + MEMORY.md + decisions.md).

    Always emits a pipeline-trace line via log_pipeline_run, including for
    blocked/escalated paths. Observability MUST NOT crash the pipeline
    (see pipeline_log for swallowing guarantees).
    """
    start = time.monotonic()
    stages: list[str] = []
    classification = None
    verification = None
    escalated = False
    result: PipelineResult | None = None
    # v0.8 Tier 4: per-stage wall-clock timer
    timer = StageTimer()

    try:
        with timer.stage("orchestrator"):
            plan = await orchestrator.run(payload)
        stages.append("orchestrator")

        with timer.stage("classifier"):
            classification = await classifier.run(payload, plan)
        stages.append("classifier")

        # Read existing context for verification + writing.
        # Use safe_read so life/credentials/* is blocked even if the LLM ever
        # pointed us there (defense in depth — should not happen, but cheap).
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

        # Read existing append-target files for the Writer
        from datetime import datetime, timezone

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        existing_drops = safe_read(f"daily/{today}/drops.md")
        existing_decisions = safe_read("log/decisions.md")
        # wiki/index.md — only needed for wiki-kind drops, but cheap to always read
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
        # Best-effort trace for evals + dogfood observability. Captures
        # happy + blocked + raised paths equally. Exceptions inside
        # log_pipeline_run are swallowed by the logger itself.
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
