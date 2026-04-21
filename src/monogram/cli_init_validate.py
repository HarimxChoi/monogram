"""Lightweight inline validators used by the `monogram init` wizard.

Each returns (ok: bool, message: str). Failure messages are user-facing
— keep them short, actionable, and honest about what was probed.

These helpers are intentionally separate from `models.validate_llm_config`:
that one reads the persisted vault config, this one validates single
user-entered values BEFORE anything is written to disk.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from typing import Any

log = logging.getLogger("monogram.cli_init_validate")


# ── GitHub ────────────────────────────────────────────────────────────

def validate_github_pat(pat: str, repo: str) -> tuple[bool, str]:
    """Probe the PAT against the repo via `get_contents('')`.

    `get_contents('')` exercises both Metadata:R (repo visible) and
    Contents:R (can list the tree). A PAT missing Contents:R/W on the
    target repo fails here with a clear message.
    """
    if not pat:
        return (False, "empty PAT")
    if not repo or "/" not in repo:
        return (False, f"repo must be in <user>/<name> form, got: {repo!r}")

    try:
        from github import Auth, Github
        from github.GithubException import GithubException
    except ImportError:  # pragma: no cover
        return (False, "PyGithub not installed")

    try:
        gh = Github(auth=Auth.Token(pat))
        gh_repo = gh.get_repo(repo)
        _ = gh_repo.get_contents("")
        return (True, f"reached {repo} ({'private' if gh_repo.private else 'public'})")
    except GithubException as e:
        status = getattr(e, "status", "?")
        if status == 401:
            return (False, "401 — PAT is invalid or expired")
        if status == 403:
            return (False, "403 — PAT lacks Contents:R/W on this repo")
        if status == 404:
            return (False, f"404 — repo not found (or PAT can't see it): {repo}")
        return (False, f"{status}: {e.data if hasattr(e, 'data') else e}")
    except Exception as e:
        return (False, f"{type(e).__name__}: {e}")


# ── Telegram bot ──────────────────────────────────────────────────────

def validate_telegram_bot_token(token: str) -> tuple[bool, str]:
    """Hit `/getMe` on the Bot API. Confirms the token is live and returns
    the bot's `@username` in the success message so the user sees that
    the token maps to the bot they just created."""
    if not token or ":" not in token:
        return (False, "bot token must be of the form `<id>:<secret>`")

    try:
        import httpx
    except ImportError:  # pragma: no cover
        return (False, "httpx not installed")

    try:
        resp = httpx.get(
            f"https://api.telegram.org/bot{token}/getMe",
            timeout=10.0,
        )
    except Exception as e:
        return (False, f"network error: {type(e).__name__}: {e}")

    if resp.status_code == 401:
        return (False, "401 — token invalid or revoked")
    if resp.status_code != 200:
        return (False, f"HTTP {resp.status_code}: {resp.text[:100]}")

    try:
        data = resp.json()
    except Exception:
        return (False, "response was not JSON")

    if not data.get("ok"):
        return (False, f"telegram: {data.get('description', 'unknown error')}")

    result = data.get("result") or {}
    username = result.get("username") or "?"
    return (True, f"bot @{username}")


def validate_telegram_user_id(raw: str) -> tuple[bool, str]:
    """Confirm the entered user id is an integer in a sensible range.

    We don't hit the API — the bot can't look up users by id without
    that user first messaging the bot, which would create a chicken-
    and-egg step here. Integer check is enough.
    """
    try:
        uid = int(raw.strip())
    except (ValueError, AttributeError):
        return (False, f"must be a numeric id (got {raw!r}) — use @userinfobot")
    if uid <= 0:
        return (False, "id must be positive")
    if uid > 10**15:
        return (False, "id is suspiciously large — double-check @userinfobot")
    return (True, f"uid={uid}")


# ── LLM reachability ──────────────────────────────────────────────────

def validate_llm_model(
    model: str,
    api_key: str = "",
    base_url: str = "",
    timeout: float = 15.0,
) -> tuple[bool, str]:
    """Make a minimal `Say OK` call through litellm.

    Returns (ok, message). The monogram pipeline will use the same
    client + auth path, so a successful probe here means the real
    pipeline will work too.
    """
    if not model:
        return (False, "empty model string")

    try:
        import litellm
    except ImportError:  # pragma: no cover
        return (False, "litellm not installed")

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": "Say OK"}],
        "max_tokens": 5,
        "temperature": 0,
    }
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["api_base"] = base_url

    async def _call() -> str:
        out = await asyncio.wait_for(litellm.acompletion(**kwargs), timeout=timeout)
        msg = out.choices[0].message.content or ""
        return msg.strip()[:40]

    try:
        reply = asyncio.run(_call())
    except asyncio.TimeoutError:
        return (False, f"timeout after {timeout}s — key may be invalid, or provider is slow")
    except Exception as e:
        return (False, f"{type(e).__name__}: {str(e)[:120]}")

    return (True, f"reached {model} (said: {reply!r})")


# ── gcloud preflight ─────────────────────────────────────────────────

def gcloud_available() -> tuple[bool, str]:
    """Is gcloud on PATH and a version we can call?"""
    if not shutil.which("gcloud"):
        return (False, "gcloud not on PATH — install: https://cloud.google.com/sdk/docs/install")
    try:
        out = subprocess.run(
            ["gcloud", "version"], capture_output=True, text=True, timeout=10
        )
    except Exception as e:
        return (False, f"gcloud check failed: {e}")
    if out.returncode != 0:
        return (False, f"gcloud version returned non-zero: {out.stderr[:100]}")
    first_line = (out.stdout.splitlines() or [""])[0]
    return (True, first_line)


def gcloud_active_account() -> tuple[bool, str]:
    """Is there an active gcloud auth account?"""
    try:
        out = subprocess.run(
            [
                "gcloud", "auth", "list",
                "--filter=status:ACTIVE",
                "--format=value(account)",
            ],
            capture_output=True, text=True, timeout=10,
        )
    except Exception as e:
        return (False, f"gcloud auth list failed: {e}")
    if out.returncode != 0:
        return (False, out.stderr.strip()[:120])
    account = out.stdout.strip()
    if not account:
        return (False, "no active account — run `gcloud auth login`")
    return (True, account)


def gcloud_project_billing_ok(project: str) -> tuple[bool, str]:
    """Has the project got billing enabled? GCS API needs it even on
    free tier. Returns (True, …) for both enabled-and-paid and
    enabled-but-free-tier; only (False, …) for truly unlinked."""
    try:
        out = subprocess.run(
            [
                "gcloud", "beta", "billing", "projects", "describe", project,
                "--format=value(billingEnabled)",
            ],
            capture_output=True, text=True, timeout=15,
        )
    except Exception as e:
        return (False, f"billing check failed: {e}")
    if out.returncode != 0:
        return (False, out.stderr.strip()[:120])
    if out.stdout.strip().lower() != "true":
        return (False, "billing not enabled — link at https://console.cloud.google.com/billing")
    return (True, "billing enabled")
