"""
Password hashing — PBKDF2-HMAC-SHA256 from the standard library.

We never store a password, only a verifier. The encoded form is self-describing::

    pbkdf2_sha256$600000$<salt-b64>$<derived-key-b64>

Keeping the algorithm, cost and salt *inside* the stored string is what makes the
cost parameter upgradable: :func:`needs_rehash` tells you when a stored hash was
made with a weaker setting than today's default, so you can transparently
re-hash on the user's next successful login without a migration or a forced reset.

Why PBKDF2 and not bcrypt/argon2: those are C extensions that would break the
"no dependencies" property, and ``hashlib.pbkdf2_hmac`` is a well-specified,
FIPS-approved KDF that is entirely adequate at a high iteration count. The honest
trade-off is that PBKDF2 is cheaper to attack on GPUs than memory-hard Argon2id;
if this ever guards real officer credentials in production, argon2-cffi is the
upgrade and :func:`hash_password` is the only function that changes.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

__all__ = [
    "ALGORITHM",
    "DEFAULT_ITERATIONS",
    "MIN_PASSWORD_LENGTH",
    "hash_password",
    "verify_password",
    "needs_rehash",
    "password_problem",
]

ALGORITHM = "pbkdf2_sha256"
DEFAULT_ITERATIONS = 600_000  # ~0.2s on a modern laptop; raise as hardware improves
SALT_BYTES = 16
MIN_PASSWORD_LENGTH = 8


def _b64e(raw: bytes) -> str:
    """URL-safe base64 without padding — keeps the encoded hash free of '='/'+'."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def hash_password(password: str, *, iterations: int = DEFAULT_ITERATIONS) -> str:
    """Derive a fresh salted verifier for *password*."""
    if not isinstance(password, str) or password == "":
        raise ValueError("password must be a non-empty string")
    salt = secrets.token_bytes(SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{ALGORITHM}${iterations}${_b64e(salt)}${_b64e(dk)}"


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time check of *password* against a stored verifier.

    Returns False for malformed or unknown-algorithm records rather than raising,
    so a corrupted row denies access instead of crashing the login endpoint.
    """
    if not isinstance(password, str) or not isinstance(encoded, str):
        return False
    try:
        algorithm, iter_text, salt_text, hash_text = encoded.split("$")
        if algorithm != ALGORITHM:
            return False
        iterations = int(iter_text)
        salt = _b64d(salt_text)
        expected = _b64d(hash_text)
    except (ValueError, TypeError, base64.binascii.Error):  # type: ignore[attr-defined]
        return False
    if iterations <= 0 or not salt or not expected:
        return False

    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations, dklen=len(expected)
    )
    # compare_digest, not ==, so a wrong password cannot be found byte-by-byte
    # from response timing.
    return hmac.compare_digest(candidate, expected)


def needs_rehash(encoded: str, *, iterations: int = DEFAULT_ITERATIONS) -> bool:
    """True when a stored verifier is weaker than today's default settings."""
    try:
        algorithm, iter_text, _salt, _hash = encoded.split("$")
    except (ValueError, AttributeError):
        return True
    if algorithm != ALGORITHM:
        return True
    try:
        return int(iter_text) < iterations
    except ValueError:
        return True


def password_problem(password: str) -> str | None:
    """Return a human reason the password is unacceptable, or None if it is fine.

    Deliberately minimal: length is the only requirement that reliably correlates
    with strength. Composition rules ("one capital, one symbol") mostly push people
    toward predictable substitutions, so we do not impose them.
    """
    if not isinstance(password, str) or password.strip() == "":
        return "Password must not be empty."
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    if len(password) > 200:
        return "Password must be 200 characters or fewer."
    return None
