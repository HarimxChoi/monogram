"""Weekly job tests — mocked github_store + LLM."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from monogram.weekly_job import archival_sweep


def test_archival_sweep_returns_empty_in_sandbox():
    result = asyncio.run(archival_sweep())
    assert result == []
