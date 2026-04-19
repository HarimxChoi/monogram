"""B8/A8 tests — monogram init wizard. Mocks GitHub + LLM + filesystem."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from monogram.cli import main


def _fake_repo(private=True):
    r = MagicMock()
    r.private = private
    return r


@patch("monogram.cli._test_llm_reachable", return_value=None)
@patch("monogram.cli._init_skeleton", new_callable=AsyncMock)
@patch("github.Github")
def test_init_happy_path_english_default_gemini(
    mock_github_cls, mock_init_skel, mock_test, tmp_path, monkeypatch
):
    """Full wizard with English + default categories + [1] Default Gemini."""
    monkeypatch.chdir(tmp_path)
    mock_github_cls.return_value.get_repo.return_value = _fake_repo()

    runner = CliRunner()
    inputs = "\n".join([
        "ghp_FAKE_PAT",          # PAT
        "example-org",            # username
        "",                      # repo default=mono
        "en",                    # language
        "y",                     # default categories
        "",                      # tg bot token
        "",                      # tg user_id
        "",                      # tg api_id
        "",                      # tg api_hash
        "1",                     # LLM path: default Gemini
        "AIza_FAKE_KEY",         # Gemini API key
    ]) + "\n"
    result = runner.invoke(main, ["init"], input=inputs, catch_exceptions=False)

    assert result.exit_code == 0, result.output
    env_body = (tmp_path / ".env").read_text()
    assert "GITHUB_REPO=example-org/mono" in env_body
    assert "GEMINI_API_KEY=AIza_FAKE_KEY" in env_body
    # v0.4: MONOGRAM_MODEL line no longer written
    assert "MONOGRAM_MODEL=" not in env_body
    mock_init_skel.assert_awaited_once()
    # _init_skeleton: (language, categories, init_call_model, llm_config, webui_config)
    call_args = mock_init_skel.await_args[0]
    assert len(call_args) == 5
    language, categories, init_call_model, llm_config, webui_config = call_args
    assert language == "en"
    assert llm_config["llm_provider"] == "gemini"
    assert llm_config["llm_mode"] == "tiered"
    assert "low" in llm_config["llm_models"]


@patch("github.Github")
def test_init_aborts_on_bad_pat(mock_github_cls, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mock_github_cls.return_value.get_repo.side_effect = Exception("401 Bad credentials")

    runner = CliRunner()
    inputs = "\n".join([
        "bad_pat", "example-org", "", "en",
    ]) + "\n"
    result = runner.invoke(main, ["init"], input=inputs)
    assert result.exit_code != 0
    assert "Could not access" in result.output
    assert not (tmp_path / ".env").exists()


@patch("monogram.cli._test_llm_reachable", return_value=None)
@patch("monogram.cli._init_skeleton", new_callable=AsyncMock)
@patch("github.Github")
def test_init_refuses_to_overwrite_existing_env(
    mock_github_cls, mock_init_skel, mock_test, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("EXISTING=yes\n")
    mock_github_cls.return_value.get_repo.return_value = _fake_repo()

    runner = CliRunner()
    inputs = "\n".join([
        "ghp_X", "user", "", "en", "y",
        "", "", "", "",                 # Telegram blanks
        "1",                            # Default Gemini path
        "AIza_X",                       # Gemini key
        "n",                            # don't overwrite .env
    ]) + "\n"
    result = runner.invoke(main, ["init"], input=inputs)
    assert result.exit_code != 0
    assert (tmp_path / ".env").read_text() == "EXISTING=yes\n"
    mock_init_skel.assert_not_awaited()


@patch("monogram.cli._test_llm_reachable", return_value=None)
@patch("monogram.cli._init_skeleton", new_callable=AsyncMock)
@patch("github.Github")
def test_init_custom_categories_always_add_credentials(
    mock_github_cls, mock_init_skel, mock_test, tmp_path, monkeypatch
):
    """Custom categories omitting 'credentials' → init appends it."""
    monkeypatch.chdir(tmp_path)
    mock_github_cls.return_value.get_repo.return_value = _fake_repo()

    runner = CliRunner()
    inputs = "\n".join([
        "ghp_X", "user", "", "en",
        "n",                            # custom categories
        "hobbies,travel",               # user list (no 'credentials')
        "", "", "", "",                 # Telegram blanks
        "1",                            # Default Gemini
        "AIza_X",                       # Gemini key
    ]) + "\n"
    result = runner.invoke(main, ["init"], input=inputs, catch_exceptions=False)
    assert result.exit_code == 0, result.output
    _, categories, _, _, _ = mock_init_skel.await_args[0]
    assert "credentials" in categories
    assert "hobbies" in categories
    assert "travel" in categories


@patch("monogram.cli._test_llm_reachable", return_value=None)
@patch("monogram.cli._init_skeleton", new_callable=AsyncMock)
@patch("github.Github")
def test_init_byo_anthropic_tiered(
    mock_github_cls, mock_init_skel, mock_test, tmp_path, monkeypatch
):
    """BYO-LLM → anthropic → tiered → verify exact user-typed model strings
    flow through to config.md via llm_config argument."""
    monkeypatch.chdir(tmp_path)
    mock_github_cls.return_value.get_repo.return_value = _fake_repo()

    runner = CliRunner()
    inputs = "\n".join([
        "ghp_X", "user", "", "en", "y",
        "", "", "", "",                         # Telegram blanks
        "2",                                    # BYO-LLM
        "2",                                    # anthropic (endpoint [2])
        "sk-ant-TEST",                          # API key
        "2",                                    # tiered mode
        "anthropic/claude-haiku-4-5",           # low
        "anthropic/claude-sonnet-4-6",          # mid
        "anthropic/claude-opus-4-7",            # high
    ]) + "\n"
    result = runner.invoke(main, ["init"], input=inputs, catch_exceptions=False)
    assert result.exit_code == 0, result.output
    env_body = (tmp_path / ".env").read_text()
    assert "ANTHROPIC_API_KEY=sk-ant-TEST" in env_body
    _, _, init_call_model, llm_config, _ = mock_init_skel.await_args[0]
    assert llm_config["llm_provider"] == "anthropic"
    assert llm_config["llm_mode"] == "tiered"
    assert llm_config["llm_models"]["low"] == "anthropic/claude-haiku-4-5"
    assert llm_config["llm_models"]["high"] == "anthropic/claude-opus-4-7"
    assert init_call_model == "anthropic/claude-haiku-4-5"


@patch("monogram.cli._test_llm_reachable", return_value=None)
@patch("monogram.cli._init_skeleton", new_callable=AsyncMock)
@patch("github.Github")
def test_init_byo_ollama_single(
    mock_github_cls, mock_init_skel, mock_test, tmp_path, monkeypatch
):
    """BYO → ollama → single mode → verify base_url + dummy-less config."""
    monkeypatch.chdir(tmp_path)
    mock_github_cls.return_value.get_repo.return_value = _fake_repo()

    runner = CliRunner()
    inputs = "\n".join([
        "ghp_X", "user", "", "en", "y",
        "", "", "", "",
        "2",                                 # BYO
        "4",                                 # ollama (endpoint [4])
        "http://localhost:11434",            # base URL default
        "1",                                 # single mode
        "ollama/qwen2.5:7b",                 # model
    ]) + "\n"
    result = runner.invoke(main, ["init"], input=inputs, catch_exceptions=False)
    assert result.exit_code == 0, result.output
    _, _, init_call_model, llm_config, _ = mock_init_skel.await_args[0]
    assert llm_config["llm_provider"] == "ollama"
    assert llm_config["llm_mode"] == "single"
    assert llm_config["llm_models"]["single"] == "ollama/qwen2.5:7b"
    assert llm_config["llm_base_url"] == "http://localhost:11434"
    assert init_call_model == "ollama/qwen2.5:7b"
