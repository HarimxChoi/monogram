import os
import time
from unittest.mock import patch

import pytest

_PREFIXES = ("TELEGRAM_", "GEMINI_", "ANTHROPIC_", "OPENAI_", "GITHUB_", "MONOGRAM_", "NOTION_", "OBSIDIAN_")


def pytest_addoption(parser):
    parser.addoption(
        "--live-llm",
        action="store_true",
        default=False,
        help="run tests marked live_llm (hits Gemini — quota / rate-limited)",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live_llm: makes a live Gemini call. Skipped by default; pass --live-llm.",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--live-llm"):
        return
    skip = pytest.mark.skip(reason="live LLM test (pass --live-llm to run)")
    for item in items:
        if "live_llm" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _throttle_after_live_llm(request):
    """Sleep after any test marked live_llm so Gemini free-tier RPM holds."""
    yield
    if request.node.get_closest_marker("live_llm"):
        time.sleep(6.5)


@pytest.fixture
def isolated_env(monkeypatch, tmp_path):
    """Strip monogram env vars and chdir to a fresh tmp dir (no .env present)."""
    for key in list(os.environ):
        if key.startswith(_PREFIXES):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def default_vault_config():
    """Default English VaultConfig."""
    from monogram.vault_config import VaultConfig
    return VaultConfig()


@pytest.fixture
def korean_vault_config():
    """Korean VaultConfig for multilingual tests."""
    from monogram.vault_config import VaultConfig
    return VaultConfig(primary_language="ko")


@pytest.fixture
def mock_vault_config(request):
    """Parametrized VaultConfig mock. Default: English with defaults.

    Usage:
        def test_thing(mock_vault_config):
            ...
        @pytest.mark.parametrize("mock_vault_config", [korean_cfg], indirect=True)
        def test_thing_in_korean(mock_vault_config):
            ...
    """
    from monogram.vault_config import VaultConfig, load_vault_config
    cfg = getattr(request, "param", None) or VaultConfig()
    load_vault_config.cache_clear()
    patches = [
        patch("monogram.vault_config.load_vault_config", return_value=cfg),
        patch("monogram.taxonomy.load_vault_config", return_value=cfg),
        patch("monogram.safe_read.load_vault_config", return_value=cfg),
        patch("monogram.agents.classifier.load_vault_config", return_value=cfg),
    ]
    for p in patches:
        p.start()
    try:
        yield cfg
    finally:
        for p in patches:
            p.stop()
        load_vault_config.cache_clear()
