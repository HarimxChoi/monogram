from pydantic_settings import BaseSettings, SettingsConfigDict


class MonogramConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    telegram_api_id: int
    telegram_api_hash: str
    telegram_bot_token: str
    telegram_user_id: int
    # E1: gemini_api_key is optional in v0.4 (users may pick anthropic/openai/ollama only)
    gemini_api_key: str = ""
    # v0.4: additional provider credentials (fill only the one you use)
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # v0.6 — Web UI password (plaintext here; used only for client-side
    # PBKDF2 derivation at publish time). Min 16 chars enforced by
    # validate_webui_config() at startup.
    monogram_webui_password: str = ""
    github_pat: str
    github_repo: str = "example-org/mono"

    # Legacy fallback — DO NOT DELETE. Used by models.get_model() when
    # mono/config.md has no llm_provider (v0.3 users who upgraded but
    # haven't re-run init). New setups leave this empty and use vault config.
    monogram_model: str = "gemini/gemini-2.5-flash-lite"
    monogram_watch_repos: str = ""
    notion_token: str = ""
    obsidian_vault_path: str = ""


def load_config() -> MonogramConfig:
    try:
        return MonogramConfig()  # type: ignore[call-arg]
    except Exception as e:
        raise SystemExit(
            f"Config error: {e}\nCopy .env.example to .env and fill values."
        )
