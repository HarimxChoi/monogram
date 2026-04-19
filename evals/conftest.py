"""pytest configuration for the eval harness.

Installs the cassette shim and the CaptureStore via session/function fixtures.
Adds --record / --auto-record CLI flags. Forces serial execution during
--record to avoid xdist worker races on cassette file writes.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from evals.capture import CaptureStore, install as install_capture
from evals.cassette import Cassette
from evals.kill_switch import is_eval_enabled

log = logging.getLogger("monogram.evals.conftest")


_CASSETTE_ROOT = Path(__file__).parent / "cassettes"


# ── CLI flags ─────────────────────────────────────────────────────────

def pytest_addoption(parser):
    parser.addoption(
        "--record",
        action="store_true",
        default=False,
        help="Force re-record ALL cassettes (serial, burns LLM quota).",
    )
    parser.addoption(
        "--auto-record",
        action="store_true",
        default=False,
        help="Replay cache hits; record on miss (for adding new fixtures).",
    )
    parser.addoption(
        "--force-eval",
        action="store_true",
        default=False,
        help="Run evals even if kill-switch says disabled (for CI smoke tests).",
    )


def pytest_configure(config):
    # Review fix #2: serial when recording. xdist workers would race on
    # cassette file writes, losing entries to last-write-wins.
    if config.getoption("--record") and hasattr(config.option, "numprocesses"):
        config.option.numprocesses = 0
        log.warning(
            "--record forces serial execution. Parallelism disabled."
        )


# ── Kill-switch gate ──────────────────────────────────────────────────

def pytest_collection_modifyitems(config, items):
    """Honor the kill-switch unless --force-eval is passed."""
    if config.getoption("--force-eval"):
        return
    enabled, reason = is_eval_enabled()
    if not enabled:
        skip = pytest.mark.skip(reason=f"eval disabled: {reason}")
        for item in items:
            item.add_marker(skip)


# ── Cassette mode resolver ────────────────────────────────────────────

@pytest.fixture(scope="session")
def cassette_mode(request) -> str:
    if request.config.getoption("--record"):
        return "record"
    if request.config.getoption("--auto-record"):
        return "auto"
    return "replay"


@pytest.fixture(scope="session")
def cassette(cassette_mode: str):
    """Session-scoped cassette. Installed once, saved at session end."""
    c = Cassette(_CASSETTE_ROOT, mode=cassette_mode)
    c.install()
    yield c
    if cassette_mode in ("record", "auto"):
        c.save()
    c.uninstall()


# ── Per-test CaptureStore ─────────────────────────────────────────────

@pytest.fixture
def capture_store(monkeypatch, cassette) -> CaptureStore:
    """Fresh in-memory store per test.

    Depends on `cassette` so any LLM calls the test triggers are caught
    by the cassette layer first, then github_store writes are captured
    here.
    """
    store = CaptureStore()
    install_capture(monkeypatch, store)
    return store


# ── Convenience fixtures ──────────────────────────────────────────────

@pytest.fixture
def run_pipeline_sync():
    """Synchronous wrapper for convenience in parametrized tests."""
    import asyncio

    from monogram.pipeline import run_pipeline

    def _run(payload: str):
        return asyncio.run(run_pipeline(payload))

    return _run
