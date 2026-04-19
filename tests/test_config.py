import pytest

from monogram.config import MonogramConfig, load_config


def test_missing_env_raises(isolated_env):
    with pytest.raises(SystemExit):
        load_config()


def test_valid_env_loads(isolated_env, monkeypatch):
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setenv("TELEGRAM_USER_ID", "42")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.setenv("GITHUB_PAT", "ghp-test")

    cfg = load_config()

    assert cfg.github_repo == "example-org/mono"
    assert cfg.monogram_model == "gemini/gemini-2.5-flash-lite"
    assert cfg.telegram_api_id == 12345
    assert cfg.telegram_user_id == 42


def test_types_coerced_from_strings(isolated_env, monkeypatch):
    monkeypatch.setenv("TELEGRAM_API_ID", "999")
    monkeypatch.setenv("TELEGRAM_API_HASH", "h")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "b")
    monkeypatch.setenv("TELEGRAM_USER_ID", "7")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("GITHUB_PAT", "p")

    cfg = load_config()

    assert isinstance(cfg.telegram_api_id, int)
    assert isinstance(cfg.telegram_user_id, int)


def test_loads_from_dotenv_file(isolated_env):
    (isolated_env / ".env").write_text(
        "TELEGRAM_API_ID=5\n"
        "TELEGRAM_API_HASH=h\n"
        "TELEGRAM_BOT_TOKEN=b\n"
        "TELEGRAM_USER_ID=6\n"
        "GEMINI_API_KEY=g\n"
        "GITHUB_PAT=p\n"
        "GITHUB_REPO=me/custom-repo\n"
    )

    cfg = load_config()

    assert cfg.github_repo == "me/custom-repo"
    assert cfg.telegram_api_id == 5


def test_error_message_is_actionable(isolated_env):
    with pytest.raises(SystemExit) as exc_info:
        load_config()
    msg = str(exc_info.value)
    assert ".env" in msg
    assert "Config error" in msg


def test_instantiable_class():
    assert issubclass(MonogramConfig, object)
