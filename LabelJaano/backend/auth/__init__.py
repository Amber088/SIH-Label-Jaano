"""
Authentication primitives — password hashing, signed tokens, roles.

Pure standard library by design: the rule engine's selling point is that it has no
runtime dependencies, and bolting on PyJWT + passlib would have quietly ended that.
See :mod:`auth.tokens` for why a hand-rolled JWT is *safer* here rather than merely
lighter — the algorithm is pinned in code, so alg-confusion attacks are impossible
by construction.

Nothing in this package touches the database or FastAPI. The DB-facing half lives
in :mod:`store.users`; the HTTP-facing half (request dependencies, 401/403
responses) lives in :mod:`app.deps`. That split is what keeps the crypto unit
-testable without a server or a database.
"""
from __future__ import annotations

from .passwords import (
    MIN_PASSWORD_LENGTH,
    hash_password,
    needs_rehash,
    password_problem,
    verify_password,
)
from .registration import OFFICER_CODE_ENV, officer_code_configured, role_for_signup
from .roles import DEFAULT_ROLE, Permission, Role
from .tickets import (
    PURPOSE_REPORT,
    REPORT_TICKET_TTL_SECONDS,
    mint_report_ticket,
    read_report_ticket,
)
from .tokens import (
    DEFAULT_TTL_SECONDS,
    PURPOSE_API,
    TokenError,
    TokenExpired,
    TokenInvalid,
    TokenWrongPurpose,
    decode,
    encode,
    secret_is_ephemeral,
)

__all__ = [
    # passwords
    "hash_password",
    "verify_password",
    "needs_rehash",
    "password_problem",
    "MIN_PASSWORD_LENGTH",
    # tokens
    "encode",
    "decode",
    "TokenError",
    "TokenExpired",
    "TokenInvalid",
    "TokenWrongPurpose",
    "DEFAULT_TTL_SECONDS",
    "PURPOSE_API",
    "secret_is_ephemeral",
    # report share tickets
    "PURPOSE_REPORT",
    "REPORT_TICKET_TTL_SECONDS",
    "mint_report_ticket",
    "read_report_ticket",
    # roles
    "Role",
    "Permission",
    "DEFAULT_ROLE",
    # registration
    "role_for_signup",
    "officer_code_configured",
    "OFFICER_CODE_ENV",
]
