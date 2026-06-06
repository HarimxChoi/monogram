"""Tests for the secret-shape redaction backstop (secret_filter).

This is the defense-in-depth boundary: even if the classifier misroutes a
credential to a benign target_kind, no live key shape should survive into a
staged write or commit message.
"""
from monogram.secret_filter import PLACEHOLDER, contains_secret, redact


def test_redacts_openai_key():
    text = "my key is sk-proj-abcDEF0123456789ghiJKLmnop0123456789 ok"
    out = redact(text)
    assert "sk-proj-" not in out
    assert PLACEHOLDER in out
    assert out.endswith(" ok")  # trailing prose preserved


def test_redacts_anthropic_key():
    text = "ANTHROPIC=sk-ant-api03-AbC0123456789dEfGhIjKlMnOpQrStUv"
    assert contains_secret(text)
    assert "sk-ant-" not in redact(text)


def test_redacts_github_pat():
    classic = "token ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    assert PLACEHOLDER in redact(classic)
    fine = "github_pat_11ABCDE0000" + "a" * 60
    assert PLACEHOLDER in redact(fine)


def test_redacts_google_key():
    text = "AIzaSyA0123456789abcdefghijklmnopqrstuv"  # 39 chars
    assert PLACEHOLDER in redact(text)


def test_redacts_slack_and_aws():
    assert PLACEHOLDER in redact("xoxb-1234567890-abcdefghijkl")
    assert PLACEHOLDER in redact("AKIAIOSFODNN7EXAMPLE")


def test_redacts_pem_block():
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIBOgIBAAJBAKj34GkxFhD90vcN...\n"
        "-----END RSA PRIVATE KEY-----"
    )
    out = redact(pem)
    assert "PRIVATE KEY" not in out
    assert PLACEHOLDER in out


def test_ordinary_prose_untouched():
    prose = (
        "Refactored the task-management-system-architecture for the "
        "risk-assessment-workflow today."
    )
    assert redact(prose) == prose
    assert not contains_secret(prose)


def test_slug_untouched():
    slug = "pose-estimation-baseline-notes"
    assert redact(slug) == slug
    assert not contains_secret(slug)


def test_idempotent():
    text = "key sk-proj-abcDEF0123456789ghiJKLmnop0123456789 end"
    once = redact(text)
    assert redact(once) == once


def test_empty_and_none_safe():
    assert redact("") == ""
    assert not contains_secret("")
