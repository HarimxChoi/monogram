"""GitHub Actions setup — cron conversion + workflow YAML (pure, no network)."""
from monogram.actions_setup import (
    _graph_workflow,
    _reindex_workflow,
    _workflow,
    local_to_utc_cron,
)


def test_daily_cron_offset():
    assert local_to_utc_cron(8, 0, 9) == "0 23 * * *"     # 08:00 KST → 23:00 UTC
    assert local_to_utc_cron(8, 30, 0) == "30 8 * * *"    # +0
    assert local_to_utc_cron(2, 0, -5) == "0 7 * * *"     # 02:00 EST → 07:00 UTC


def test_weekly_cron_dow_rollover():
    # Sunday(6) 21:00 KST(+9) → Sunday 12:00 UTC → cron dow 0
    assert local_to_utc_cron(21, 0, 9, dow=6) == "0 12 * * 0"
    # Sunday 06:00 KST(+9) → Saturday 21:00 UTC → cron dow 6 (rollover)
    assert local_to_utc_cron(6, 0, 9, dow=6) == "0 21 * * 6"


def test_workflow_yaml():
    y = _workflow("morning", "morning", "0 23 * * *")
    assert 'cron: "0 23 * * *"' in y
    assert "run: monogram morning" in y
    assert "${{ secrets.GEMINI_API_KEY }}" in y
    assert "${{ secrets.GITHUB_TOKEN }}" in y          # vault access via built-in token
    assert "contents: write" in y
    assert "workflow_dispatch" in y                    # manual trigger too


def test_reindex_workflow_yaml():
    y = _reindex_workflow("0 17 * * *")
    assert 'cron: "0 17 * * *"' in y
    assert "run: monogram reindex" in y
    assert "mono-gram[semantic-gemma]" in y            # local onnxruntime embedder
    assert "actions/cache" in y                        # model cached, not committed
    assert "${{ secrets.GITHUB_TOKEN }}" in y          # only the vault token, no LLM key
    assert "GEMINI_API_KEY" not in y                   # local embedding needs no LLM secret
    assert "contents: write" in y


def test_graph_workflow_yaml():
    y = _graph_workflow("30 16 * * *")
    assert 'cron: "30 16 * * *"' in y
    assert "run: monogram graph" in y
    assert "${{ secrets.GITHUB_TOKEN }}" in y          # only the vault token
    assert "GEMINI_API_KEY" not in y                   # deterministic build, no LLM secret
    assert "contents: write" in y
