"""
User repository — accounts, lookup, and role changes.

Only three things here deserve comment:

* **Emails are the identity** and are stored lower-cased and stripped, so
  ``Officer@Dept.gov.in`` and ``officer@dept.gov.in`` cannot become two accounts.
* **The password never enters this module in plain form beyond hashing it.** We
  call :func:`auth.passwords.hash_password` on the way in and store only the
  verifier; there is no code path that reads a password back out.
* **:func:`authenticate` is uniform-cost.** A missing email still performs a hash
  comparison against a dummy verifier, so an attacker cannot enumerate which
  addresses are registered by timing the login endpoint.
"""
from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from auth.passwords import hash_password, needs_rehash, verify_password
from auth.roles import DEFAULT_ROLE, Role

from . import db

__all__ = [
    "User",
    "UserExists",
    "create_user",
    "get_user",
    "get_user_by_email",
    "authenticate",
    "set_role",
    "set_disabled",
    "list_users",
    "count_users",
]


class UserExists(Exception):
    """Raised when an email is already registered."""


# A well-formed verifier for a password nobody has. Compared against on the
# "no such user" path so failed logins cost the same whether or not the account
# exists. Computed lazily to keep import time fast.
_DUMMY_HASH: Optional[str] = None


def _dummy_hash() -> str:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password(uuid.uuid4().hex)
    return _DUMMY_HASH


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalise_email(email: str) -> str:
    return (email or "").strip().lower()


@dataclass
class User:
    id: str
    email: str
    name: str
    role: Role
    created_at: str
    disabled: bool = False

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "User":
        return cls(
            id=row["id"],
            email=row["email"],
            name=row["name"] or "",
            role=Role.parse(row["role"]),
            created_at=row["created_at"],
            disabled=bool(row["disabled"]),
        )

    def to_dict(self) -> dict[str, Any]:
        """Public shape. Note the absence of ``password_hash`` — by construction."""
        return {
            "id": self.id,
            "email": self.email,
            "name": self.name,
            "role": self.role.value,
            "role_label": self.role.label,
            "created_at": self.created_at,
            "disabled": self.disabled,
        }


# --------------------------------------------------------------------------- #
# Writes
# --------------------------------------------------------------------------- #
def create_user(
    email: str,
    password: str,
    *,
    name: str = "",
    role: Role | str = DEFAULT_ROLE,
) -> User:
    """Register an account. Raises :class:`UserExists` on a duplicate email."""
    addr = normalise_email(email)
    if not addr or "@" not in addr:
        raise ValueError("a valid email address is required")

    user = User(
        id=uuid.uuid4().hex,
        email=addr,
        name=(name or "").strip(),
        role=Role.parse(role),
        created_at=_now(),
        disabled=False,
    )
    try:
        db.execute(
            """
            INSERT INTO users (id, email, name, role, password_hash, created_at, disabled)
            VALUES (:id, :email, :name, :role, :password_hash, :created_at, 0)
            """,
            {
                "id": user.id,
                "email": user.email,
                "name": user.name,
                "role": user.role.value,
                "password_hash": hash_password(password),
                "created_at": user.created_at,
            },
        )
    except sqlite3.IntegrityError as exc:  # UNIQUE(email)
        raise UserExists(f"{addr} is already registered") from exc
    return user


def set_role(user_id: str, role: Role | str) -> bool:
    """Promote or demote an account. Used by ``manage.py``, not by the HTTP API."""
    n = db.execute(
        "UPDATE users SET role = ? WHERE id = ?", (Role.parse(role).value, user_id)
    )
    return n > 0


def set_disabled(user_id: str, disabled: bool) -> bool:
    n = db.execute(
        "UPDATE users SET disabled = ? WHERE id = ?", (1 if disabled else 0, user_id)
    )
    return n > 0


# --------------------------------------------------------------------------- #
# Reads / authentication
# --------------------------------------------------------------------------- #
def get_user(user_id: str) -> Optional[User]:
    row = db.query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    return User.from_row(row) if row else None


def get_user_by_email(email: str) -> Optional[User]:
    row = db.query_one("SELECT * FROM users WHERE email = ?", (normalise_email(email),))
    return User.from_row(row) if row else None


def authenticate(email: str, password: str) -> Optional[User]:
    """Return the user when the credentials are right, else None.

    Deliberately gives the caller no way to distinguish "no such account" from
    "wrong password" — the login endpoint reports one message for both, and the
    dummy-hash comparison below makes the two paths cost the same.
    """
    row = db.query_one("SELECT * FROM users WHERE email = ?", (normalise_email(email),))
    if row is None:
        verify_password(password or "", _dummy_hash())  # equalise timing
        return None

    stored = row["password_hash"]
    if not verify_password(password or "", stored):
        return None
    if bool(row["disabled"]):
        return None

    user = User.from_row(row)
    # Transparent cost upgrade: if this verifier predates a raised iteration
    # count, re-hash now that we legitimately hold the plaintext.
    if needs_rehash(stored):
        try:
            db.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (hash_password(password), user.id),
            )
        except sqlite3.DatabaseError:  # pragma: no cover - never block a good login
            pass
    return user


def list_users(*, limit: int = 100, offset: int = 0) -> list[User]:
    rows = db.query(
        "SELECT * FROM users ORDER BY created_at ASC LIMIT ? OFFSET ?",
        (max(1, min(int(limit), 500)), max(0, int(offset))),
    )
    return [User.from_row(r) for r in rows]


def count_users() -> int:
    row = db.query_one("SELECT COUNT(*) AS n FROM users")
    return int(row["n"]) if row else 0
