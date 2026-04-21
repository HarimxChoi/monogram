"""v0.6 — Client-side decryption envelope for the web UI.

Server produces an encrypted HTML blob. The browser prompts for a
password, derives a key via PBKDF2-HMAC-SHA256 (600k iterations),
and decrypts via AES-256-GCM. Tamper detection is free (GCM auth tag).

Hosting the ciphertext on a public bucket is safe because the bucket
never sees the password — derivation and decryption are 100% client-side.
"""
from __future__ import annotations

import base64
import os
import secrets
from typing import Final

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

SALT_BYTES: Final = 16
NONCE_BYTES: Final = 12
KEY_BYTES: Final = 32  # AES-256
PBKDF2_ITERATIONS: Final = 600_000
MIN_PASSWORD_LEN: Final = 10
MIN_UNIQUE_CHARS: Final = 6


def validate_password(password: str) -> list[str]:
    """Return list of validation error strings; empty list == OK."""
    errors: list[str] = []
    if not isinstance(password, str) or not password:
        errors.append("password is empty")
        return errors
    if len(password) < MIN_PASSWORD_LEN:
        errors.append(
            f"password must be at least {MIN_PASSWORD_LEN} characters "
            f"(got {len(password)})"
        )
    if len(set(password)) < MIN_UNIQUE_CHARS:
        errors.append(
            f"password needs at least {MIN_UNIQUE_CHARS} unique characters "
            "(use a password manager's generator)"
        )
    return errors


def _derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KEY_BYTES,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(password.encode("utf-8"))


def encrypt_blob(plaintext: bytes, password: str) -> bytes:
    """Return `base64(salt || nonce || ciphertext+tag)` as bytes.

    Raises ValueError if password fails validation.
    """
    errors = validate_password(password)
    if errors:
        raise ValueError("; ".join(errors))

    salt = secrets.token_bytes(SALT_BYTES)
    nonce = secrets.token_bytes(NONCE_BYTES)
    key = _derive_key(password, salt)
    aead = AESGCM(key)
    ciphertext_and_tag = aead.encrypt(nonce, plaintext, associated_data=None)
    packed = salt + nonce + ciphertext_and_tag
    return base64.b64encode(packed)


def decrypt_blob(blob_b64: bytes | str, password: str) -> bytes:
    """Symmetric counterpart used by tests. Never called from the server at runtime."""
    if isinstance(blob_b64, str):
        blob_b64 = blob_b64.encode("ascii")
    raw = base64.b64decode(blob_b64)
    salt = raw[:SALT_BYTES]
    nonce = raw[SALT_BYTES : SALT_BYTES + NONCE_BYTES]
    ciphertext_and_tag = raw[SALT_BYTES + NONCE_BYTES :]
    key = _derive_key(password, salt)
    aead = AESGCM(key)
    return aead.decrypt(nonce, ciphertext_and_tag, associated_data=None)


def wrap(plaintext_html: bytes | str, password: str) -> bytes:
    """Encrypt HTML and inline it into the shell template.

    Returns bytes ready to write to disk or upload. The returned bytes are
    a complete HTML page with password prompt + decrypt JS + encrypted blob.
    """
    if isinstance(plaintext_html, str):
        plaintext_html = plaintext_html.encode("utf-8")
    blob = encrypt_blob(plaintext_html, password).decode("ascii")
    shell = _shell_template()
    return shell.replace("__ENCRYPTED_BLOB__", blob).encode("utf-8")


_SHELL_TEMPLATE_CACHE: str | None = None


def _shell_template() -> str:
    """Load shell.html.j2 verbatim (no Jinja here; just a placeholder string).

    The shell is static — it doesn't need templating. We only replace one
    placeholder, `__ENCRYPTED_BLOB__`. Kept out of Jinja so encryption
    doesn't depend on jinja2 being importable.
    """
    global _SHELL_TEMPLATE_CACHE
    if _SHELL_TEMPLATE_CACHE is not None:
        return _SHELL_TEMPLATE_CACHE
    # Look it up next to this module.
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "webui", "templates", "shell.html.j2")
    with open(path, "r", encoding="utf-8") as f:
        _SHELL_TEMPLATE_CACHE = f.read()
    return _SHELL_TEMPLATE_CACHE
