"""
SQLite connection + schema management — pure standard library.

Why sqlite3 and not SQLAlchemy/Postgres
---------------------------------------
The rule engine's defining property is that it has *zero* runtime dependencies.
Persistence is the first thing that usually breaks that, so we deliberately used
``sqlite3`` from the standard library: history, users, and aggregate statistics
work on a stock Python install, on an inspector's laptop, with no server to
provision and nothing to `pip install`. For a field-enforcement tool that may run
disconnected in a warehouse, a single-file database is a feature, not a shortcut.

The seam to grow out of it is intentional and narrow: every SQL statement in this
package lives in :mod:`store.scans` and :mod:`store.users`, and the rest of the
codebase only ever calls those functions. Swapping in Postgres means rewriting
two modules, not hunting queries across the API layer.

Concurrency model
-----------------
One connection, opened with ``check_same_thread=False`` and guarded by a
re-entrant lock, because FastAPI runs sync endpoints in a threadpool. Every query
here is indexed and touches a handful of rows, so holding the lock for the length
of a statement costs microseconds. WAL journalling is *preferred* so an external
reader (``sqlite3 data/labeljaano.db``) never blocks the API mid-demo, but it is not
assumed: see :func:`_configure_journal` for why some filesystems cannot provide it
and what we fall back to.

Configuration
-------------
``LABEL_JAANO_DB``   path to the database file. Default ``backend/data/labeljaano.db``.
                     Use ``:memory:`` for an ephemeral database (tests).
"""
from __future__ import annotations

import os
import sqlite3
import sys
import threading
from pathlib import Path
from typing import Any, Iterator, Optional

__all__ = [
    "SCHEMA_VERSION",
    "configure",
    "connection",
    "cursor",
    "db_path",
    "init_schema",
    "query",
    "query_one",
    "execute",
    "close",
    "stats",
    "journal_mode",
]

# Bump this and append to _MIGRATIONS when the schema changes. The pragma
# ``user_version`` in the file records which migrations have been applied, so an
# existing database upgrades in place instead of needing to be thrown away.
SCHEMA_VERSION = 2

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_DEFAULT_DB = _BACKEND_DIR / "data" / "labeljaano.db"

_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None
_configured_path: Optional[str] = None


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
# Note the denormalised verdict/score/severity columns on ``scans``. The full
# report is kept verbatim in ``report_json`` (it is the audit record and must not
# be lossy), but the dashboard's aggregates are computed in SQL over the extracted
# columns — so /stats never has to deserialise thousands of reports to count them.
_MIGRATION_1 = """
CREATE TABLE IF NOT EXISTS users (
    id            TEXT    PRIMARY KEY,
    email         TEXT    NOT NULL UNIQUE,          -- always stored lower-cased
    name          TEXT    NOT NULL DEFAULT '',
    role          TEXT    NOT NULL DEFAULT 'consumer',
    password_hash TEXT    NOT NULL,
    created_at    TEXT    NOT NULL,                 -- ISO-8601 UTC
    disabled      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS scans (
    id              TEXT    PRIMARY KEY,
    created_at      TEXT    NOT NULL,               -- ISO-8601 UTC
    user_id         TEXT,                           -- NULL = anonymous scan
    -- denormalised from the report for fast aggregates + indexed filtering
    verdict         TEXT    NOT NULL,
    score           REAL    NOT NULL DEFAULT 0,
    category        TEXT    NOT NULL DEFAULT 'unknown',
    packs_applied   TEXT    NOT NULL DEFAULT '[]',  -- JSON array of pack ids
    checks_total    INTEGER NOT NULL DEFAULT 0,
    passed          INTEGER NOT NULL DEFAULT 0,
    failed          INTEGER NOT NULL DEFAULT 0,
    skipped         INTEGER NOT NULL DEFAULT 0,
    critical        INTEGER NOT NULL DEFAULT 0,
    major           INTEGER NOT NULL DEFAULT 0,
    minor           INTEGER NOT NULL DEFAULT 0,
    -- provenance: was this a live model read or the offline mock? never guess.
    source          TEXT    NOT NULL DEFAULT 'json',   -- 'json' | 'image'
    mock            INTEGER NOT NULL DEFAULT 0,
    -- officer-supplied field metadata
    product_name    TEXT,
    note            TEXT,
    location        TEXT,
    -- the audit record, verbatim
    report_json     TEXT    NOT NULL,
    scan_input_json TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_scans_created ON scans(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_scans_verdict ON scans(verdict);
CREATE INDEX IF NOT EXISTS idx_scans_user    ON scans(user_id);
CREATE INDEX IF NOT EXISTS idx_scans_cat     ON scans(category);

-- One row per violation, exploded out of the report at save time. This is what
-- turns a per-label checker into an enforcement-intelligence tool: "which rule
-- is broken most often, and by what severity" becomes a GROUP BY instead of a
-- full re-parse of every stored report. Cascades with its parent scan.
CREATE TABLE IF NOT EXISTS scan_violations (
    scan_id           TEXT NOT NULL,
    declaration_id    TEXT NOT NULL,
    declaration_label TEXT NOT NULL DEFAULT '',
    legal_reference   TEXT NOT NULL DEFAULT '',
    severity          TEXT NOT NULL DEFAULT 'minor',
    check_type        TEXT NOT NULL DEFAULT '',
    message           TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (scan_id) REFERENCES scans(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_viol_scan ON scan_violations(scan_id);
CREATE INDEX IF NOT EXISTS idx_viol_decl ON scan_violations(declaration_id);
"""

# Migration 2 — the audit trail.
#
# Two distinct things are recorded, deliberately in one table:
#   * privileged *reads* — an officer opening the whole-corpus queue or another
#     account's inspection. An enforcement record that anyone with a role can read
#     unobserved is not an enforcement record, and "who looked at this file" is the
#     first question asked when a case is disputed.
#   * privileged *writes* — role changes, account disables, rulepack reloads.
#
# ``actor_id`` is nullable on purpose: it is set NULL rather than cascade-deleted when
# an account is removed, because deleting the officer must not erase the fact that
# somebody looked. The row survives with the email string frozen in ``actor_email``.
_MIGRATION_2 = """
CREATE TABLE IF NOT EXISTS audit_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT    NOT NULL,               -- ISO-8601 UTC
    actor_id     TEXT,                           -- NULL once the account is deleted
    actor_email  TEXT    NOT NULL DEFAULT '',    -- frozen copy, survives deletion
    actor_role   TEXT    NOT NULL DEFAULT '',    -- role AT THE TIME of the action
    action       TEXT    NOT NULL,               -- e.g. 'scans.list', 'user.role'
    target       TEXT,                           -- scan id / user id / pack id
    detail       TEXT,                           -- JSON: filters used, old->new value
    FOREIGN KEY (actor_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_actor   ON audit_log(actor_id);
CREATE INDEX IF NOT EXISTS idx_audit_action  ON audit_log(action);
"""

_MIGRATIONS: list[str] = [_MIGRATION_1, _MIGRATION_2]


# --------------------------------------------------------------------------- #
# Configuration / connection
# --------------------------------------------------------------------------- #
def db_path() -> str:
    """Resolve the database location: explicit :func:`configure` > env > default."""
    if _configured_path is not None:
        return _configured_path
    env = os.environ.get("LABEL_JAANO_DB", "").strip()
    return env or str(_DEFAULT_DB)


def configure(path: str | os.PathLike[str] | None) -> None:
    """Point the store at *path*, closing any existing connection.

    Passing ``None`` reverts to the env var / default. ``":memory:"`` gives an
    ephemeral database — used by the tests so they never touch real history.
    """
    global _configured_path
    with _lock:
        close()
        _configured_path = None if path is None else str(path)


def connection() -> sqlite3.Connection:
    """The process-wide connection, opening + migrating it on first use."""
    global _conn
    with _lock:
        if _conn is None:
            _conn = _open(db_path())
            init_schema(_conn)
        return _conn


def _open(path: str) -> sqlite3.Connection:
    if path == ":memory:":
        # A plain ":memory:" database is private to its connection. We share one
        # connection process-wide so that is fine, and it keeps tests hermetic.
        target = ":memory:"
    else:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        target = str(p)

    conn = sqlite3.connect(target, check_same_thread=False, timeout=10.0)
    conn.row_factory = sqlite3.Row
    if target != ":memory:":
        _configure_journal(conn)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


# Journal modes to try, best first. ``OFF`` is deliberately absent: it makes writes
# unrecoverable if the process dies mid-transaction, and a corrupt history file is a
# worse outcome than not starting.
_JOURNAL_PREFERENCE = ("WAL", "TRUNCATE", "PERSIST", "MEMORY")

_journal_mode: str = "unknown"


def _configure_journal(conn: sqlite3.Connection) -> str:
    """Pick the best journal mode this filesystem will actually honour.

    WAL is what we want: it lets ``sqlite3 data/label_jaano.db`` tail the history
    live without blocking the API. But WAL needs shared-memory (mmap) support, and
    several filesystems people really do run this on cannot provide it — network
    shares, some Docker volume drivers, and cloud-synced folders. On a Mac this is
    not hypothetical: a checkout under ``~/Desktop`` or ``~/Documents`` is inside
    the iCloud sync root on a default install.

    Worse, the SQLite default (``DELETE``) is not a safe fallback either, because it
    removes the journal file after each commit and some of those filesystems refuse
    to unlink. ``TRUNCATE`` and ``PERSIST`` reuse one journal file instead and
    survive there.

    The awkward part is that the ``PRAGMA`` cannot be trusted on its own — asking for
    a mode can report success and still fail on the first real write. So each
    candidate is probed with an actual committed write, and we keep the first that
    survives. Returns the mode chosen, also reported by :func:`stats` so a running
    deployment can say which one it got.
    """
    global _journal_mode
    for mode in _JOURNAL_PREFERENCE:
        try:
            conn.execute(f"PRAGMA journal_mode = {mode}")
            # A committed write is the only honest test. CREATE + DROP touches the
            # schema, takes the same locks as a real INSERT, and leaves nothing.
            conn.execute("CREATE TABLE IF NOT EXISTS _journal_probe (x INTEGER)")
            conn.execute("DROP TABLE _journal_probe")
            conn.commit()
        except sqlite3.DatabaseError:
            try:
                conn.rollback()
            except sqlite3.DatabaseError:  # pragma: no cover - already unusable
                pass
            continue
        if mode != _JOURNAL_PREFERENCE[0]:
            # stderr, not stdout: manage.py's tables are parsed by humans and
            # scripts alike, and a diagnostic in the middle of them corrupts both.
            print(
                f"[label-jaano] note: this filesystem rejected "
                f"{_JOURNAL_PREFERENCE[0]} journalling; using {mode} instead. "
                "History still works; concurrent readers may block briefly.",
                file=sys.stderr,
            )
        _journal_mode = mode.lower()
        return _journal_mode

    # Every candidate failed. Let the caller's own first write raise the real error
    # rather than inventing one here — it will carry a better message than we can.
    _journal_mode = "unavailable"
    return _journal_mode


def journal_mode() -> str:
    """The journal mode actually in force (see :func:`_configure_journal`)."""
    return _journal_mode


def close() -> None:
    """Close the connection (next call to :func:`connection` reopens it)."""
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            finally:
                _conn = None


def init_schema(conn: sqlite3.Connection) -> int:
    """Apply any migrations the file has not seen yet. Returns the new version."""
    with _lock:
        current = int(conn.execute("PRAGMA user_version").fetchone()[0])
        for version in range(current, len(_MIGRATIONS)):
            conn.executescript(_MIGRATIONS[version])
            # user_version cannot be parameterised, hence the f-string over an int.
            conn.execute(f"PRAGMA user_version = {version + 1}")
        conn.commit()
        return int(conn.execute("PRAGMA user_version").fetchone()[0])


# --------------------------------------------------------------------------- #
# Tiny query helpers — every SQL string in this package flows through these
# --------------------------------------------------------------------------- #
def query(sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
    with _lock:
        return connection().execute(sql, params).fetchall()


def query_one(sql: str, params: tuple | dict = ()) -> Optional[sqlite3.Row]:
    with _lock:
        return connection().execute(sql, params).fetchone()


def execute(sql: str, params: tuple | dict = ()) -> int:
    """Run a write statement, commit, and return the affected row count."""
    with _lock:
        conn = connection()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.rowcount


def cursor() -> Iterator[sqlite3.Cursor]:  # pragma: no cover - convenience only
    """Escape hatch for multi-statement writes; caller commits."""
    with _lock:
        yield connection().cursor()


def stats() -> dict[str, Any]:
    """Small health payload so ``GET /health`` can prove the DB is reachable."""
    with _lock:
        conn = connection()
        scans = int(conn.execute("SELECT COUNT(*) FROM scans").fetchone()[0])
        users = int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    path = db_path()
    size = None
    if path != ":memory:":
        try:
            size = Path(path).stat().st_size
        except OSError:  # pragma: no cover
            size = None
    return {
        "path": path,
        "schema_version": version,
        "scans": scans,
        "users": users,
        "size_bytes": size,
        "journal_mode": journal_mode(),
    }
