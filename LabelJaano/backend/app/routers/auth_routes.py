"""
Account endpoints — sign up, sign in, who am I.

Three decisions worth stating, because each is a place where the obvious
implementation leaks something:

**One error message for both login failures.** "No such account" and "wrong
password" get the identical 401. Distinguishing them turns the login form into an
account-enumeration oracle: an attacker learns which addresses are registered.
:func:`store.users.authenticate` also equalises the *timing* of the two paths, so
the leak does not simply move from the message to the stopwatch.

**Sign-up cannot grant privilege.** The requested role passes through
:func:`auth.role_for_signup`, which lets consumer through, gates officer behind a
shared enrolment code, and refuses admin outright. Without that, "officer" would be
a checkbox on a public form and the access model would be decorative.

**Tokens are not revocable.** They are stateless and signed, so a token stays valid
until it expires (12h). Disabling an account *does* take effect immediately — the
per-request dependency re-reads the user row — but there is no logout-everywhere. A
deny-list table is the honest fix; it is not built, and pretending otherwise would be
worse than saying so here.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

import auth
import store
from auth.registration import OfficerCodeRejected
from store.users import User

from .. import deps
from ..schemas import AuthConfigOut, LoginRequest, SignupRequest, TokenOut, UserOut

router = APIRouter(prefix="/auth", tags=["accounts"])


def _token_for(user: User) -> TokenOut:
    """Mint a bearer token for *user*.

    The role is embedded for the client's convenience only — so the app can render
    the right navigation without a second round trip. Authorisation never reads it;
    :func:`app.deps.optional_user` re-reads the role from the database on every
    request, so a demotion or a disabled account takes effect at once rather than
    when the token happens to expire.
    """
    token = auth.encode({"sub": user.id, "email": user.email, "role": user.role.value})
    return TokenOut(
        access_token=token,
        token_type="bearer",
        expires_in=auth.DEFAULT_TTL_SECONDS,
        user=UserOut(**user.to_dict()),
    )


@router.get("/config", response_model=AuthConfigOut,
            summary="What sign-up options this server offers")
def auth_config() -> AuthConfigOut:
    return AuthConfigOut(
        accounts_available=deps.persistence_ready(),
        officer_signup_enabled=auth.officer_code_configured(),
        min_password_length=auth.MIN_PASSWORD_LENGTH,
        ephemeral_secret=auth.secret_is_ephemeral(),
    )


@router.post("/register", response_model=TokenOut,
             status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(deps.require_persistence)],
             summary="Create an account and sign in")
def register(req: SignupRequest) -> TokenOut:
    problem = auth.password_problem(req.password)
    if problem:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=problem)

    try:
        role = auth.role_for_signup(req.role, req.officer_code)
    except OfficerCodeRejected as exc:
        # 403, not 400: the request was well-formed, the caller just is not entitled
        # to the role it asked for.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))

    try:
        user = store.create_user(req.email, req.password, name=req.name, role=role)
    except store.UserExists:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That email address is already registered. Sign in instead.",
        )
    except ValueError as exc:  # malformed address
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    return _token_for(user)


@router.post("/login", response_model=TokenOut,
             dependencies=[Depends(deps.require_persistence)],
             summary="Exchange credentials for a bearer token")
def login(req: LoginRequest) -> TokenOut:
    user = store.authenticate(req.email, req.password)
    if user is None:
        # Deliberately identical for unknown address, wrong password, and disabled
        # account. See the module docstring.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _token_for(user)


@router.get("/me", response_model=UserOut, summary="The signed-in account")
def me(user: User = Depends(deps.require_user)) -> UserOut:
    return UserOut(**user.to_dict())


@router.post("/refresh", response_model=TokenOut,
             summary="Exchange a valid token for a fresh one")
def refresh(user: User = Depends(deps.require_user)) -> TokenOut:
    """Extend a session without re-entering a password.

    This only works while the current token is still valid, so it slides an active
    session forward rather than granting indefinite access from an expired one. It
    also picks up a role change on the way through, since the user row is re-read.
    """
    return _token_for(user)
