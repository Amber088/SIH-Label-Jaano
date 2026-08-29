"""
Persistence — scan history, users, and aggregates on stdlib ``sqlite3``.

Import from here rather than reaching into the submodules::

    from store import save_scan, list_scans, aggregate_stats, create_user

Layering rule this package obeys: **all SQL lives in this package, and nothing in
this package knows about HTTP.** :mod:`store.scans` and :mod:`store.users` are the
only modules that write a query, and neither imports FastAPI or pydantic. That is
what makes the persistence layer swappable — moving to Postgres is a rewrite of two
modules with the API layer untouched — and testable without a running server.

See :mod:`store.db` for the connection model and the reasoning behind sqlite3.
"""
from __future__ import annotations

from . import db
from .db import SCHEMA_VERSION, close, configure, connection, db_path, init_schema
from .db import journal_mode
from .db import stats as db_stats
from .scans import (
    SCORED_VERDICTS,
    ScanRow,
    aggregate_stats,
    delete_scan,
    get_scan,
    list_scans,
    save_scan,
    top_violations,
)
from .users import (
    User,
    UserExists,
    authenticate,
    count_users,
    create_user,
    get_user,
    get_user_by_email,
    list_users,
    normalise_email,
    set_disabled,
    set_role,
)

__all__ = [
    # connection / schema
    "db",
    "configure",
    "connection",
    "close",
    "db_path",
    "init_schema",
    "db_stats",
    "journal_mode",
    "SCHEMA_VERSION",
    # scans
    "ScanRow",
    "save_scan",
    "get_scan",
    "list_scans",
    "delete_scan",
    "aggregate_stats",
    "top_violations",
    "SCORED_VERDICTS",
    # users
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
    "normalise_email",
]
