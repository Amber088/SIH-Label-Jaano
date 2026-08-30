"""
Administration endpoints — accounts and the audit trail.

Everything here requires ``MANAGE_USERS``, which only ``admin`` holds. Three
decisions are load-bearing:

* **Every write is audited before it returns.** A role grant that leaves no trace is
  indistinguishable from a compromise. The audit row records the actor's role *as it
  was at the time*, so a later demotion does not rewrite who was allowed to do what.

* **Lockout is prevented structurally, not documented.** An administrator cannot
  demote or disable the last remaining enabled admin — including themselves. Getting
  this wrong means the only recovery is shell access to the database file, which on a
  deployed server is exactly when you do not have it.

* **The audit log is append-only over HTTP.** There is no DELETE route. Retention is
  enacted deliberately with ``manage.py audit --purge-before``; a log that can be
  edited through the same API it records is not evidence.

Note that ``admin`` accounts can be *created* here but never *self-granted*: no
enrolment code mints one (see :mod:`auth.registration`), so the first admin comes
from ``manage.py role`` on the host.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

import store
from auth.passwords import MIN_PASSWORD_LENGTH
from auth.roles import Permission, Role
from store.users import User

from .. import deps
from ..schemas import (
    AdminUserCreate,
    AdminUserListOut,
    AdminUserOut,
    AdminUserPatch,
    AuditEntryOut,
    AuditListOut,
)

router = APIRouter(
    prefix="/admin",
    tags=["administration"],
    dependencies=[
        Depends(deps.require_persistence),
        Depends(deps.require_permission(Permission.MANAGE_USERS)),
    ],
)


def _as_admin_user(user: User, scans: int) -> AdminUserOut:
    return AdminUserOut(**user.to_dict(), scans=scans)


def _parse_role_strict(value: str) -> Role:
    """Parse a role from an admin's request, rejecting anything unrecognised.

    Deliberately *not* :meth:`Role.parse`, which silently degrades to ``consumer``.
    That leniency is right for a value read back from the database — a corrupt row
    must not grant access — but wrong here: an admin who types ``"offcier"`` should
    get an error, not a silent demotion of the account they meant to promote.
    """
    text = (value or "").strip().lower()
    for role in Role:
        if role.value == text:
            return role
    raise HTTPException(
        status_code=deps.HTTP_422,
        detail=f"Unknown role {value!r}. Use one of: "
               + ", ".join(r.value for r in Role) + ".",
    )


def _enabled_admin_ids() -> set[str]:
    """Ids of every admin who can currently sign in.

    Disabled admins are excluded: an account that cannot log in cannot recover the
    server, so it does not count towards "somebody is still holding the keys".
    """
    rows, _ = store.list_users_page(role=Role.ADMIN, include_disabled=False, limit=500)
    return {u.id for u, _ in rows}


def _guard_last_admin(target: User, *, new_role: Optional[Role],
                      new_disabled: Optional[bool]) -> None:
    """Refuse a change that would leave the server with no usable administrator."""
    losing_admin = (
        Role.parse(target.role) is Role.ADMIN
        and ((new_role is not None and new_role is not Role.ADMIN)
             or new_disabled is True)
    )
    if not losing_admin:
        return
    remaining = _enabled_admin_ids() - {target.id}
    if not remaining:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This is the only administrator who can still sign in. Promote "
                   "another account to admin first, or the server would be left with "
                   "no way to manage accounts except shell access to the database.",
        )


# --------------------------------------------------------------------------- #
# Accounts
# --------------------------------------------------------------------------- #
@router.get("/users", response_model=AdminUserListOut,
            summary="List accounts, with each one's inspection count")
def list_accounts(
    request: Request,
    admin: User = Depends(deps.require_user),
    role: Optional[str] = Query(None, description="filter: consumer | officer | admin"),
    search: Optional[str] = Query(None, description="substring match on email or name"),
    include_disabled: bool = Query(True),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> AdminUserListOut:
    wanted = _parse_role_strict(role) if role else None
    rows, total = store.list_users_page(
        role=wanted, search=search, include_disabled=include_disabled,
        limit=limit, offset=offset,
    )
    deps.record_audit(
        store.audit.USER_LIST, admin, request,
        detail={"role": role, "search": search, "returned": len(rows), "total": total},
    )
    return AdminUserListOut(
        items=[_as_admin_user(u, n) for u, n in rows],
        total=total,
        limit=limit,
        offset=offset,
        by_role=store.count_users_by_role(),
    )


@router.post("/users", response_model=AdminUserOut,
             status_code=status.HTTP_201_CREATED,
             summary="Create an account at any role")
def create_account(
    body: AdminUserCreate,
    request: Request,
    admin: User = Depends(deps.require_user),
) -> AdminUserOut:
    role = _parse_role_strict(body.role)
    if len(body.password or "") < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=deps.HTTP_422,
            detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters.",
        )
    try:
        created = store.create_user(body.email, body.password,
                                    name=body.name, role=role)
    except store.UserExists:
        # Unlike /auth/register, being explicit is right here: an administrator
        # creating an account is entitled to know the address is already taken, and
        # they can already list every account anyway.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=f"{body.email} is already registered.")
    except ValueError as exc:
        raise HTTPException(status_code=deps.HTTP_422,
                            detail=str(exc))

    deps.record_audit(
        store.audit.USER_CREATE, admin, request,
        target=created.id,
        detail={"email": created.email, "role": created.role.value},
    )
    return _as_admin_user(created, 0)


@router.patch("/users/{user_id}", response_model=AdminUserOut,
              summary="Change an account's role and/or enabled state")
def patch_account(
    user_id: str,
    body: AdminUserPatch,
    request: Request,
    admin: User = Depends(deps.require_user),
) -> AdminUserOut:
    if body.role is None and body.disabled is None:
        raise HTTPException(
            status_code=deps.HTTP_422,
            detail="Nothing to change: provide 'role', 'disabled', or both.",
        )
    target = store.get_user(user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No such account.")

    new_role = _parse_role_strict(body.role) if body.role is not None else None
    _guard_last_admin(target, new_role=new_role, new_disabled=body.disabled)

    before = {"role": Role.parse(target.role).value, "disabled": target.disabled}

    if new_role is not None and new_role is not Role.parse(target.role):
        store.set_role(target.id, new_role)
        deps.record_audit(
            store.audit.USER_ROLE, admin, request,
            target=target.id,
            detail={"email": target.email, "from": before["role"], "to": new_role.value,
                    "self": target.id == admin.id},
        )
    if body.disabled is not None and body.disabled != target.disabled:
        store.set_disabled(target.id, body.disabled)
        deps.record_audit(
            store.audit.USER_DISABLE, admin, request,
            target=target.id,
            detail={"email": target.email, "disabled": body.disabled,
                    "self": target.id == admin.id},
        )

    fresh = store.get_user(target.id)
    if fresh is None:  # pragma: no cover - would mean a concurrent delete
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No such account.")
    _, scans_total = store.list_scans(user_id=fresh.id, limit=1)
    return _as_admin_user(fresh, scans_total)


# --------------------------------------------------------------------------- #
# Audit trail
# --------------------------------------------------------------------------- #
@router.get("/audit", response_model=AuditListOut,
            summary="The audit trail: privileged reads and writes, newest first")
def list_audit(
    actor_id: Optional[str] = Query(None, description="filter to one account's actions"),
    action: Optional[str] = Query(None, description="e.g. scans.list, user.role"),
    target: Optional[str] = Query(None, description="the scan id / user id acted on"),
    since: Optional[str] = Query(None, description="ISO-8601 lower bound, inclusive"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> AuditListOut:
    """Read the trail.

    Reading it is not itself audited. That is a deliberate stopping point: logging
    reads of the log invites an unbounded regress, and the table is append-only over
    HTTP, so the meaningful protection is that entries cannot be removed rather than
    that inspection is observed.
    """
    entries, total = store.audit.list_entries(
        actor_id=actor_id, action=action, target=target, since=since,
        limit=limit, offset=offset,
    )
    return AuditListOut(
        items=[AuditEntryOut(**e.to_dict()) for e in entries],
        total=total,
        limit=limit,
        offset=offset,
    )
