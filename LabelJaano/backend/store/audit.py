"""
Audit trail — who exercised a privilege, over what, and when.

Two rules govern everything in this module:

* **Recording must never break the request.** :func:`record` swallows every
  exception. An officer opening the queue must not get a 500 because the audit
  insert hit a locked database; the read is the product, the log is bookkeeping.
  The failure is printed to stderr so it is visible in the server log rather than
  silently dropped.
* **The actor's identity is frozen at write time.** ``actor_email`` and
  ``actor_role`` are copied into the row rather than joined from ``users`` on read.
  A role change or an account deletion must not rewrite history — the question the
  log answers is "what was this person allowed to do *then*", not "what are they
  allowed to do now".

Only *privileged* actions are logged. A consumer reading their own scan is not an
event; an officer reading someone else's is. Logging the ordinary case would bury
the interesting one and turn the table into a traffic log.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from . import db

__all__ = ["AuditEntry", "record", "list_entries", "count_entries", "purge_before"]


# Action vocabulary. Strings rather than an enum so a new pack of endpoints can log
# without a migration, but centralised here so the set stays greppable.
CORPUS_LIST = "scans.list"          # opened the whole-corpus queue
CORPUS_STATS = "stats.aggregate"    # read corpus-wide aggregates
CORPUS_READ = "scan.read"           # opened another account's inspection
CORPUS_EXPORT = "scans.export"      # bulk CSV of the corpus
REPORT_SHARE = "report.share"       # minted a no-login link to a report
SCAN_DELETE = "scan.delete"         # destroyed an inspection
USER_LIST = "users.list"
USER_CREATE = "user.create"
USER_ROLE = "user.role"
USER_DISABLE = "user.disable"
PACKS_RELOAD = "rulepacks.reload"
AUDIT_PURGE = "audit.purge"         # enacted a retention period (see purge_before)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class AuditEntry:
    id: int
    created_at: str
    actor_id: Optional[str]
    actor_email: str
    actor_role: str
    action: str
    target: Optional[str]
    detail: Optional[dict]

    @classmethod
    def from_row(cls, row) -> "AuditEntry":
        raw = row["detail"]
        try:
            parsed = json.loads(raw) if raw else None
        except (TypeError, ValueError):
            # A malformed detail blob should show up in the log, not hide the row.
            parsed = {"unparsed": str(raw)}
        return cls(
            id=int(row["id"]),
            created_at=row["created_at"],
            actor_id=row["actor_id"],
            actor_email=row["actor_email"] or "",
            actor_role=row["actor_role"] or "",
            action=row["action"],
            target=row["target"],
            detail=parsed,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "actor_id": self.actor_id,
            "actor_email": self.actor_email,
            "actor_role": self.actor_role,
            "action": self.action,
            "target": self.target,
            "detail": self.detail,
        }


def record(
    action: str,
    *,
    actor_id: Optional[str] = None,
    actor_email: str = "",
    actor_role: str = "",
    target: Optional[str] = None,
    detail: Optional[dict] = None,
) -> None:
    """Append one entry. Never raises — see the module docstring."""
    try:
        db.execute(
            """
            INSERT INTO audit_log
                   (created_at, actor_id, actor_email, actor_role, action, target, detail)
            VALUES (:created_at, :actor_id, :actor_email, :actor_role, :action, :target, :detail)
            """,
            {
                "created_at": _now(),
                "actor_id": actor_id,
                "actor_email": actor_email or "",
                "actor_role": actor_role or "",
                "action": action,
                "target": target,
                "detail": json.dumps(detail, default=str) if detail else None,
            },
        )
    except Exception as exc:  # noqa: BLE001 - auditing must not break the request
        print(f"[label-jaano] WARNING: audit write failed ({action}): {exc}",
              file=sys.stderr)


def list_entries(
    *,
    actor_id: Optional[str] = None,
    action: Optional[str] = None,
    target: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[AuditEntry], int]:
    """Newest first, with the pre-paging total. All filters compose."""
    where: list[str] = []
    params: dict[str, Any] = {}
    if actor_id:
        where.append("actor_id = :actor_id")
        params["actor_id"] = actor_id
    if action:
        where.append("action = :action")
        params["action"] = action
    if target:
        where.append("target = :target")
        params["target"] = target
    if since:
        where.append("created_at >= :since")
        params["since"] = since
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    row = db.query_one(f"SELECT COUNT(*) AS n FROM audit_log{clause}", params)
    total = int(row["n"]) if row else 0

    page = dict(params)
    page["limit"] = max(1, min(int(limit), 500))
    page["offset"] = max(0, int(offset))
    rows = db.query(
        f"SELECT * FROM audit_log{clause} ORDER BY created_at DESC, id DESC "
        "LIMIT :limit OFFSET :offset",
        page,
    )
    return [AuditEntry.from_row(r) for r in rows], total


def count_entries() -> int:
    row = db.query_one("SELECT COUNT(*) AS n FROM audit_log")
    return int(row["n"]) if row else 0


def purge_before(cutoff_iso: str) -> int:
    """Delete entries older than *cutoff_iso*. Returns how many went.

    Retention is a policy decision, so there is no automatic expiry — an audit trail
    that quietly deletes itself is worse than none, because it looks complete. This
    exists so an operator can enact a stated retention period explicitly.
    """
    return db.execute("DELETE FROM audit_log WHERE created_at < ?", (cutoff_iso,))
