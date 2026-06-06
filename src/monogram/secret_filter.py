"""Secret-shape detection + redaction: writer backstop and pre-LLM credential gate."""
from __future__ import annotations

import re

PLACEHOLDER = "[REDACTED-SECRET]"

# (?<![A-Za-z0-9]) anchors to token start so prose substrings never match.
_SHAPES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]+?"
        r"-----END [A-Z0-9 ]*PRIVATE KEY-----"
    ), "private-key"),
    (re.compile(r"(?<![A-Za-z0-9])sk-ant-[A-Za-z0-9_-]{20,}"), "anthropic-api-key"),
    (re.compile(r"(?<![A-Za-z0-9])sk-(?:proj-)?[A-Za-z0-9_-]{20,}"), "openai-api-key"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"), "github-pat"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{40,}"), "github-pat"),
    (re.compile(r"(?<![A-Za-z0-9])AIza[0-9A-Za-z_-]{35}"), "google-api-key"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), "slack-token"),
    (re.compile(r"(?<![A-Za-z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}"), "aws-access-key"),
    (re.compile(
        r"(?<![A-Za-z0-9])eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
    ), "jwt"),
)

_PATTERNS: tuple[re.Pattern[str], ...] = tuple(pat for pat, _ in _SHAPES)


def redact(text: str) -> str:
    # Idempotent: PLACEHOLDER matches no pattern.
    if not text:
        return text
    for pat in _PATTERNS:
        text = pat.sub(PLACEHOLDER, text)
    return text


def contains_secret(text: str) -> bool:
    if not text:
        return False
    return any(pat.search(text) for pat in _PATTERNS)


def classify_secret(text: str) -> str | None:
    if not text:
        return None
    for pat, slug in _SHAPES:
        if pat.search(text):
            return slug
    return None
