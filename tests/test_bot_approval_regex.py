"""Regression tests for the /approve_<token> and /deny_<token> regexes.

v0.5.1 changed tokens from 8-char hex to `secrets.token_urlsafe(16)`
(~22 URL-safe base64 chars). Regex must accept the new alphabet.
"""
from __future__ import annotations

import secrets

from monogram.bot import _APPROVE_RE, _DENY_RE


def test_approve_regex_accepts_real_token_urlsafe():
    """Runtime token format: secrets.token_urlsafe(16)."""
    for _ in range(20):  # many random draws to cover alphabet
        token = secrets.token_urlsafe(16)
        msg = f"/approve_{token}"
        m = _APPROVE_RE.match(msg)
        assert m is not None, f"regex rejected real token: {token!r}"
        assert m.group(1) == token


def test_deny_regex_accepts_real_token_urlsafe():
    for _ in range(20):
        token = secrets.token_urlsafe(16)
        msg = f"/deny_{token}"
        m = _DENY_RE.match(msg)
        assert m is not None, f"regex rejected real token: {token!r}"
        assert m.group(1) == token


def test_regexes_accept_legacy_hex_tokens():
    """v0.4 in-memory queue used 8-char hex. Stale Telegram messages
    should still route correctly during the upgrade window."""
    assert _APPROVE_RE.match("/approve_abcd1234")
    assert _DENY_RE.match("/deny_deadbeef")


def test_regexes_accept_url_safe_dashes_and_underscores():
    """token_urlsafe can emit both `-` and `_`. Explicit check."""
    assert _APPROVE_RE.match("/approve_abc-def_ghi-jkl123")
    assert _DENY_RE.match("/deny_ABC-def_ghi_JKL123")


def test_regexes_reject_short_tokens():
    assert _APPROVE_RE.match("/approve_xyz") is None  # 3 chars < 8
    assert _DENY_RE.match("/deny_abc") is None


def test_regexes_reject_garbage_chars():
    assert _APPROVE_RE.match("/approve_abc!def") is None
    assert _APPROVE_RE.match("/approve_abc def") is None
    assert _APPROVE_RE.match("/approve_abc$def") is None


def test_regexes_accept_token_with_trailing_space_and_text():
    """Telegram appends nothing typically, but space/newline tolerated."""
    m = _APPROVE_RE.match("/approve_abcdefgh1234 some extra")
    assert m is not None
    assert m.group(1) == "abcdefgh1234"


def test_regexes_anchored_to_start():
    """Must not match mid-string to avoid accidental activation."""
    assert _APPROVE_RE.match("hello /approve_abcdefgh") is None
    assert _DENY_RE.match("hello /deny_abcdefgh") is None
