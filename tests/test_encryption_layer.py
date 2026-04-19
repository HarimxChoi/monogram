"""A2 tests — encryption_layer: wrap/unwrap + shell integration."""
from __future__ import annotations

import base64

import pytest

from monogram.encryption_layer import (
    MIN_PASSWORD_LEN,
    PBKDF2_ITERATIONS,
    decrypt_blob,
    encrypt_blob,
    validate_password,
    wrap,
)


_STRONG_PW = "abcdefgh12345678!@"  # 18 chars, 14 unique


# ── password validation ──


def test_validate_rejects_too_short():
    errs = validate_password("abc123")
    assert any("at least" in e for e in errs)


def test_validate_rejects_low_entropy():
    errs = validate_password("aaaaaaaaaaaaaaaaa")  # 17 chars but 1 unique
    assert any("unique" in e for e in errs)


def test_validate_accepts_strong():
    assert validate_password(_STRONG_PW) == []


def test_validate_min_length_constant():
    assert MIN_PASSWORD_LEN == 16


# ── round-trip ──


def test_encrypt_decrypt_roundtrip():
    plaintext = b"hello, monogram"
    blob = encrypt_blob(plaintext, _STRONG_PW)
    # Should be base64-decodable and structured as salt(16) || nonce(12) || ciphertext+tag
    raw = base64.b64decode(blob)
    assert len(raw) >= 16 + 12 + 16  # tag alone is 16
    recovered = decrypt_blob(blob, _STRONG_PW)
    assert recovered == plaintext


def test_wrong_password_fails():
    blob = encrypt_blob(b"secret", _STRONG_PW)
    with pytest.raises(Exception):
        decrypt_blob(blob, "wrongpassword12345")


def test_tampered_ciphertext_fails():
    blob = encrypt_blob(b"secret", _STRONG_PW)
    raw = bytearray(base64.b64decode(blob))
    # Flip a byte in the ciphertext portion (after salt+nonce)
    raw[30] ^= 0x01
    tampered = base64.b64encode(bytes(raw))
    with pytest.raises(Exception):
        decrypt_blob(tampered, _STRONG_PW)


def test_different_salts_produce_different_ciphertext():
    a = encrypt_blob(b"same plaintext", _STRONG_PW)
    b = encrypt_blob(b"same plaintext", _STRONG_PW)
    assert a != b  # random salts + nonces guarantee uniqueness


def test_weak_password_rejected_at_encrypt():
    with pytest.raises(ValueError, match="16 characters"):
        encrypt_blob(b"anything", "short")


# ── wrap() produces a full HTML page ──


def test_wrap_returns_html_with_embedded_blob():
    out = wrap(b"<div>decrypted payload</div>", _STRONG_PW)
    assert out.startswith(b"<!DOCTYPE html>")
    assert b"id=\"prompt\"" in out
    assert b"id=\"unlock\"" in out
    assert b"crypto.subtle.importKey" in out
    # Placeholder was substituted
    assert b"__ENCRYPTED_BLOB__" not in out
    # Base64 blob is present
    assert b"const ENCRYPTED = \"" in out


def test_wrap_string_plaintext_works():
    out = wrap("<b>hi</b>", _STRONG_PW)
    assert b"const ENCRYPTED =" in out


def test_pbkdf2_iterations_match_js_client():
    """Client shell.html.j2 hard-codes 600000. Contract check."""
    from monogram.encryption_layer import _shell_template
    shell = _shell_template()
    assert "PBKDF2_ITERATIONS = 600000" in shell
    assert PBKDF2_ITERATIONS == 600000
