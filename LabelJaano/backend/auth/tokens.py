"""
Signed access tokens — a minimal, deliberately strict HS256 JWT implementation.

This is the JWT structure everyone already knows (``header.payload.signature``,
base64url, HMAC-SHA256) written against ``hmac`` and ``hashlib`` so the backend
keeps its no-dependency property. It is intentionally *less* flexible than a
general JWT library, and that is the security argument for it: the classic JWT
vulnerabilities come from libraries being permissive.

What this implementation refuses to do:

* **Honour the token's own ``alg``.** The algorithm is pinned to HS256 in code.
  ``{"alg":"none"}`` and ``{"alg":"RS256"}`` tokens are rejected outright, which
  kills the algorithm-confusion family of attacks by construction.
* **Read claims before verifying the signature.** The MAC is checked first, with
  :func:`hmac.compare_digest`, so unsigned input never reaches claim parsing.
* **Accept a token without an expiry.** A missing ``exp`` is an error, not an
  eternal token.
* **Let a token be used for something other than what it was minted for.** Every
  token carries a ``pur`` (purpose) claim and :func:`decode` requires the caller to
  say which purpose it expects. See below.

Purpose separation
------------------
The same signing key protects two very different things: the bearer token that
authorises the whole API, and the short-lived ticket embedded in a shareable report
link (:mod:`auth.tickets`). Without a purpose claim those would be interchangeable —
a report link forwarded over WhatsApp would be a full API credential, which is the
kind of privilege escalation that only shows up after the demo. So ``pur`` is set on
mint and checked on decode, and a token minted for one purpose fails verification for
the other.

Secret management
-----------------
``LABEL_JAANO_SECRET`` supplies the signing key. If it is unset we generate a
random key *in memory at import time*: the app still works out of the box for a
demo, but tokens silently become invalid on restart and cannot be forged from a
value committed to git. That is the correct failure mode — a hardcoded fallback
secret is how real systems get broken into.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any, Optional

__all__ = [
    "ALGORITHM",
    "DEFAULT_TTL_SECONDS",
    "PURPOSE_API",
    "PURPOSE_CLAIM",
    "TokenError",
    "TokenExpired",
    "TokenInvalid",
    "TokenWrongPurpose",
    "encode",
    "decode",
    "secret_is_ephemeral",
    "signing_secret",
]

ALGORITHM = "HS256"
ISSUER = "label-jaano"
DEFAULT_TTL_SECONDS = 12 * 60 * 60  # one inspection shift
_LEEWAY_SECONDS = 30               # tolerate mild clock skew between phone and server
_MAX_TOKEN_CHARS = 8192            # bound the work an attacker can make us do

# What a token is *for*. Kept to three characters because it is on every request.
PURPOSE_CLAIM = "pur"
PURPOSE_API = "api"

# Resolved once at import. An ephemeral key is a safe default, not a good one:
# /health advertises which mode you are in so the omission is visible, not silent.
_ENV_SECRET = os.environ.get("LABEL_JAANO_SECRET", "").strip()
_EPHEMERAL = _ENV_SECRET == ""
_SECRET = (_ENV_SECRET or secrets.token_urlsafe(48)).encode("utf-8")


class TokenError(Exception):
    """Base class for anything wrong with a presented token."""


class TokenExpired(TokenError):
    """Signature was valid but the token is past its ``exp``."""


class TokenInvalid(TokenError):
    """Malformed, wrong algorithm, or bad signature."""


class TokenWrongPurpose(TokenInvalid):
    """Correctly signed and unexpired, but minted for a different job.

    A subclass of :class:`TokenInvalid` so that existing ``except TokenError`` and
    ``except TokenInvalid`` handlers keep rejecting it — a new failure mode should
    not become an accidental hole in code written before it existed.
    """


def signing_secret() -> bytes:
    return _SECRET


def secret_is_ephemeral() -> bool:
    """True when no LABEL_JAANO_SECRET was configured (tokens die on restart)."""
    return _EPHEMERAL


# --------------------------------------------------------------------------- #
# base64url helpers (JWT uses the unpadded variant)
# --------------------------------------------------------------------------- #
def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _sign(signing_input: bytes, secret: bytes) -> str:
    return _b64e(hmac.new(secret, signing_input, hashlib.sha256).digest())


# --------------------------------------------------------------------------- #
# Encode / decode
# --------------------------------------------------------------------------- #
def encode(
    claims: dict[str, Any],
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    secret: Optional[bytes] = None,
    now: Optional[int] = None,
) -> str:
    """Sign *claims* into a token. ``iss``/``iat``/``exp``/``pur`` are added for you.

    ``pur`` defaults to :data:`PURPOSE_API` rather than being required, so that the
    ordinary case — minting a session token — cannot forget it and produce a token
    that then fails its own verification. Restricted tokens pass ``pur`` explicitly;
    :mod:`auth.tickets` is the only thing in the codebase that does.
    """
    issued = int(now if now is not None else time.time())
    payload = dict(claims)
    payload.setdefault("iss", ISSUER)
    payload.setdefault(PURPOSE_CLAIM, PURPOSE_API)
    payload["iat"] = issued
    payload["exp"] = issued + int(ttl_seconds)

    header = {"alg": ALGORITHM, "typ": "JWT"}
    # separators + sort_keys => byte-identical output for identical claims, which
    # makes the signature reproducible and the tests meaningful.
    segments = [
        _b64e(json.dumps(header, separators=(",", ":"), sort_keys=True).encode()),
        _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()),
    ]
    signing_input = ".".join(segments).encode("ascii")
    segments.append(_sign(signing_input, secret or _SECRET))
    return ".".join(segments)


def decode(
    token: str,
    *,
    purpose: str = PURPOSE_API,
    secret: Optional[bytes] = None,
    now: Optional[int] = None,
    verify_exp: bool = True,
) -> dict[str, Any]:
    """Verify *token* for *purpose* and return its claims.

    Raises :class:`TokenInvalid` for anything structurally wrong or unsigned,
    :class:`TokenExpired` when a properly-signed token has aged out — the caller can
    tell the user "your session ended" versus "that token is not ours" — and
    :class:`TokenWrongPurpose` for a genuine token being used for the wrong job.

    *purpose* defaults to :data:`PURPOSE_API`, which is the overwhelmingly common
    case and the one where a mistake is most costly. Every caller that wants a
    restricted token must ask for it by name, so no code path accepts "whatever
    purpose this token happens to claim".
    """
    if not isinstance(token, str) or not token:
        raise TokenInvalid("no token supplied")
    if len(token) > _MAX_TOKEN_CHARS:
        raise TokenInvalid("token is implausibly long")

    parts = token.split(".")
    if len(parts) != 3:
        raise TokenInvalid("token must have three dot-separated segments")
    header_b64, payload_b64, signature = parts

    # 1. Signature first — nothing below this line trusts attacker-controlled data.
    expected = _sign(f"{header_b64}.{payload_b64}".encode("ascii"), secret or _SECRET)
    if not hmac.compare_digest(expected, signature):
        raise TokenInvalid("signature does not verify")

    # 2. Algorithm is pinned in code; the header may not talk us out of it.
    try:
        header = json.loads(_b64d(header_b64))
    except Exception as exc:
        raise TokenInvalid(f"unreadable header: {exc}") from exc
    if not isinstance(header, dict) or header.get("alg") != ALGORITHM:
        raise TokenInvalid(f"unsupported alg (only {ALGORITHM} is accepted)")

    # 3. Claims.
    try:
        claims = json.loads(_b64d(payload_b64))
    except Exception as exc:
        raise TokenInvalid(f"unreadable payload: {exc}") from exc
    if not isinstance(claims, dict):
        raise TokenInvalid("payload is not a JSON object")
    if claims.get("iss") != ISSUER:
        raise TokenInvalid("unexpected issuer")

    # 4. Purpose, checked before expiry: "this is not a session token" is both more
    #    accurate and more useful than "your session expired" when someone pastes a
    #    stale report link into the Authorization header.
    #
    #    An absent claim reads as PURPOSE_API because that is what encode() writes by
    #    default, so the only tokens carrying `pur` at all are the restricted ones —
    #    which means a *new* restricted purpose added later is rejected by every
    #    existing call site without anyone having to remember to update them.
    presented = claims.get(PURPOSE_CLAIM, PURPOSE_API)
    if presented != purpose:
        raise TokenWrongPurpose(
            f"this token was issued for '{presented}', not '{purpose}'"
        )

    exp = claims.get("exp")
    if not isinstance(exp, (int, float)):
        raise TokenInvalid("token has no usable exp claim")
    if verify_exp:
        current = int(now if now is not None else time.time())
        if current > int(exp) + _LEEWAY_SECONDS:
            raise TokenExpired("token has expired")

    return claims
