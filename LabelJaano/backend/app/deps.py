"""
Request dependencies — the one place auth, persistence and HTTP meet.

The two lower layers are deliberately ignorant of each other and of the web:
:mod:`auth` does crypto and knows nothing about a database; :mod:`store` does SQL and
knows nothing about a request. Neither imports FastAPI. This module is the seam that
joins them, and it is the *only* module allowed to turn a failure into a status code.

The access model in one paragraph
---------------------------------
Anonymity is a first-class case, not an error. A consumer must be able to scan a
label and get a verdict without an account — that is the whole point of the consumer
mode — so every scanning endpoint accepts an anonymous request and simply skips
persistence. Presenting a *valid* token upgrades the request: the scan is saved and
becomes history. Presenting a *broken* token is an error, never a silent downgrade to
anonymous — see :func:`optional_user`.

Roles are resolved from the database on every request, not from the token. A token
is only an assertion of identity; authority is looked up fresh. That costs one
indexed SQLite read and buys immediate effect for disabling an account or changing a
role, instead of waiting out a 12-hour token lifetime.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import auth  # noqa: E402
import store  # noqa: E402
from auth.roles import Permission, Role  # noqa: E402
from store.users import User  # noqa: E402

__all__ = [
    "bearer_scheme",
    "optional_user",
    "require_user",
    "require_permission",
    "user_from_report_ticket",
    "unauthorised",
    "history_scope",
    "init_persistence",
    "persistence_ready",
    "PERSISTENCE_DISABLED_ENV",
]

# Set LABEL_JAANO_NO_DB=1 to run the API as a pure stateless judge — no database file
# is opened and history endpoints answer 503. Useful for a read-only demo box.
PERSISTENCE_DISABLED_ENV = "LABEL_JAANO_NO_DB"

# auto_error=False is what makes anonymity possible: without it FastAPI would 403 any
# request lacking the header before our own code ever runs.
bearer_scheme = HTTPBearer(auto_error=False, description="Bearer token from /auth/login")

_persistence_ready = False


# --------------------------------------------------------------------------- #
# Startup
# --------------------------------------------------------------------------- #
def init_persistence() -> bool:
    """Open the database and apply migrations. Returns False if disabled or broken.

    A failure here must not stop the server. The rule engine is the product and it
    needs no database; losing history is a degradation, not an outage. So this logs
    loudly and lets the app boot with history endpoints returning 503.
    """
    global _persistence_ready
    if os.environ.get(PERSISTENCE_DISABLED_ENV, "").strip().lower() in ("1", "true", "yes"):
        _persistence_ready = False
        print("[label-jaano] persistence disabled via " + PERSISTENCE_DISABLED_ENV,
              file=sys.stderr)
        return False
    try:
        store.init_schema(store.connection())
        _persistence_ready = True
        print(f"[label-jaano] history database ready at {store.db_path()}",
              file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 - must never prevent boot
        _persistence_ready = False
        print(f"[label-jaano] WARNING: history database unavailable ({exc}). "
              "Scanning still works; history and accounts are disabled.",
              file=sys.stderr)
    return _persistence_ready


def persistence_ready() -> bool:
    return _persistence_ready


def require_persistence() -> None:
    """Dependency for endpoints that cannot function without the database."""
    if not _persistence_ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="History and accounts are unavailable: no database is configured "
                   "on this server. Scanning is unaffected.",
        )


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #
def unauthorised(detail: str) -> HTTPException:
    # WWW-Authenticate is what tells a client to re-authenticate rather than retry.
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _live_account(user_id: Optional[str], *, gone: str) -> User:
    """Look the subject up and insist it is still usable. Shared by both credentials.

    Both a session token and a report ticket are signed assertions about a subject,
    and both must be re-checked against the database on every request rather than
    trusted for their lifetime. Keeping that in one function is the point: if the
    ticket path had its own copy, a later change to how accounts are disabled would
    have to be remembered twice, and the forgotten copy would be the security hole.
    """
    user = store.get_user(str(user_id)) if user_id else None
    if user is None:
        # Signed correctly but the subject is gone — a deleted account, or a token
        # minted against a different database.
        raise unauthorised(gone)
    if user.disabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been disabled.",
        )
    return user


def optional_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> Optional[User]:
    """Resolve the caller, or ``None`` when the request is anonymous.

    Note the asymmetry, which is deliberate: *no* credentials yields ``None``, but
    *bad* credentials raise 401. Treating an expired token as anonymous would be the
    worse failure — a user whose session quietly lapsed would keep scanning while
    their history silently stopped being recorded, and they would have no way to tell.
    Better to say "your session ended, sign in again".

    A report share ticket presented here is *bad* credentials, not a login: it is
    signed by the same key but carries a different purpose claim, and
    :func:`auth.decode` rejects it. See :mod:`auth.tickets`.
    """
    if creds is None or not (creds.credentials or "").strip():
        return None

    try:
        claims = auth.decode(creds.credentials)
    except auth.TokenWrongPurpose:
        raise unauthorised(
            "That is a report share link, not a sign-in token. Sign in to use the API."
        )
    except auth.TokenExpired:
        raise unauthorised("Your session has expired. Please sign in again.")
    except auth.TokenError:
        raise unauthorised("Invalid authentication token.")

    if not _persistence_ready:
        # A validly signed token cannot be honoured without the user table.
        raise unauthorised("Accounts are unavailable on this server.")

    return _live_account(claims.get("sub"), gone="This account no longer exists.")


def require_user(user: Optional[User] = Depends(optional_user)) -> User:
    """Resolve the caller, or 401. For endpoints with no anonymous meaning."""
    if user is None:
        raise unauthorised("Sign in to use this endpoint.")
    return user


def require_permission(permission: Permission):
    """Build a dependency that admits only callers holding *permission*.

    Returns 403 (authenticated but not allowed), never 404 — for these endpoints the
    existence of the route is not a secret worth keeping. Per-record hiding is handled
    differently: see :func:`history_scope`.
    """

    def dependency(user: User = Depends(require_user)) -> User:
        if not Role.parse(user.role).can(permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Your role ({Role.parse(user.role).label}) is not permitted "
                       f"to {permission.value.replace('_', ' ').lower()}.",
            )
        return user

    return dependency


def user_from_report_ticket(ticket: str, *, scan_id: str) -> User:
    """Resolve the holder of a report share ticket for *scan_id*, or raise.

    This is **not** a FastAPI dependency, and deliberately so. A dependency would
    have to be attached to a route before knowing which scan the path refers to, and
    the ticket is only meaningful against a specific scan — binding the two is the
    whole safeguard (see :func:`auth.tickets.read_report_ticket`). So the route calls
    this with the path parameter in hand.

    What comes back is a :class:`User`, not a bare permission, because the report
    renderer needs an identity: it prints a signature block only when the reader is
    the officer who filed the inspection. The ticket's subject is looked up live, so a
    link outlives neither a deleted account nor a disabled one.
    """
    try:
        claims = auth.read_report_ticket(ticket, scan_id=scan_id)
    except auth.TokenExpired:
        raise unauthorised(
            "This report link has expired. Open the inspection in the app and share "
            "it again."
        )
    except auth.TokenError:
        raise unauthorised("This report link is not valid.")

    if not _persistence_ready:
        raise unauthorised("Accounts are unavailable on this server.")

    return _live_account(
        claims.get("sub"),
        gone="The account that created this link no longer exists.",
    )


# --------------------------------------------------------------------------- #
# Row-level scoping
# --------------------------------------------------------------------------- #
def history_scope(user: User) -> Optional[str]:
    """The ``user_id`` filter to apply to history queries for this caller.

    ``None`` means unfiltered — the whole corpus. An officer needs that: enforcement
    intelligence across every inspection is the reason the role exists. A consumer
    gets their own id, so the same query serves both and no endpoint has to remember
    to add a WHERE clause.

    Scoping (rather than a 403) is also why a consumer probing another user's scan id
    gets a plain 404: the filtered lookup simply finds nothing, so the response cannot
    distinguish "not yours" from "does not exist" and leaks no ids.
    """
    return None if Role.parse(user.role).can(Permission.VIEW_ALL_SCANS) else user.id


def client_ip(request: Request) -> str:
    """Best-effort client address for audit lines. Not to be trusted for authz."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
