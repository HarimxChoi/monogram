"""C3 listener tests — mocked pipeline + github_store, no real Telegram."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from monogram.agents.writer import FileChange
from monogram.pipeline import PipelineResult


_MOCK_FILE_CHANGE = FileChange(
    writes={
        "projects/paper-a.md": "---\nconfidence: high\n---\n\n# paper-a\n",
        "daily/2026-04-17/drops.md": "## 14:00\n**task** → paper-a\n",
        "MEMORY.md": "paper-a  projects/paper-a.md  phase 0 done  [high]",
        "log/decisions.md": "## 2026-04-17\nPipeline: task\n",
    },
    commit_message="monogram: task — paper-a",
    primary_path="projects/paper-a.md",
    confidence="high",
)

_MOCK_RESULT = PipelineResult(
    file_change=_MOCK_FILE_CHANGE,
    stages_executed=["orchestrator", "classifier", "extractor", "verifier", "writer"],
    escalated=False,
)


@patch("monogram.listener.run_pipeline", new_callable=AsyncMock, return_value=_MOCK_RESULT)
@patch("monogram.listener.github_store")
def test_handle_drop_uses_write_multi(mock_store, mock_pipeline):
    mock_store.write_multi.return_value = True
    from monogram.listener import handle_drop

    reply = asyncio.run(handle_drop("mark paper-a phase 0 done"))
    assert "paper-a" in reply
    assert "committed" in reply
    assert "4 paths" in reply
    mock_store.write_multi.assert_called_once()
    call_args = mock_store.write_multi.call_args
    writes = call_args[0][0]
    assert len(writes) == 4


@patch("monogram.listener.run_pipeline", new_callable=AsyncMock)
def test_handle_drop_blocked_returns_reason(mock_pipeline):
    mock_pipeline.return_value = PipelineResult(blocked_reason="test block")
    from monogram.listener import handle_drop

    reply = asyncio.run(handle_drop("something weird"))
    assert "blocked" in reply


@patch("monogram.listener.run_pipeline", new_callable=AsyncMock, return_value=_MOCK_RESULT)
@patch("monogram.listener.github_store")
def test_handle_drop_write_multi_failure(mock_store, mock_pipeline):
    mock_store.write_multi.return_value = False
    from monogram.listener import handle_drop

    reply = asyncio.run(handle_drop("mark paper-a phase 0 done"))
    assert "write failed" in reply
