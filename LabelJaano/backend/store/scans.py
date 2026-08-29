"""
Scan history repository — every SQL statement about inspections lives here.

Two things worth knowing about the design:

1. **The stored report is verbatim.** ``report_json`` holds exactly what the engine
   produced, byte for byte. It is the audit record: if an officer's finding is ever
   challenged, we can reproduce the report that was shown. The extra columns
   (verdict, score, severity counts) are *derived* copies kept alongside it purely
   so aggregates are indexable — they are never the source of truth.

2. **Violations are exploded into their own table** at save time. That turns
   "which declaration do sellers breach most often?" from a full re-parse of every
   report into one ``GROUP BY``. It is what makes the dashboard an enforcement
   -intelligence view rather than a list of past scans.

Saving is deliberately forgiving: :func:`save_scan` accepts any report dict and
falls back to safe defaults for missing keys, because a scan must never fail just
because history could not be written.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from . import db

__all__ = [
    "ScanRow",
    "save_scan",
    "get_scan",
    "list_scans",
    "delete_scan",
    "aggregate_stats",
    "top_violations",
    "SCORED_VERDICTS",
]

# Verdicts that represent an actual inspection. "no_label_detected" means the photo
# was not a packaged-commodity label at all, so it is excluded from score and rate
# averages — counting a mis-aimed photo as a 0 would slander the product. The
# Flutter ScanStore applies the identical rule, so client and server agree.
SCORED_VERDICTS = ("compliant", "needs_review", "non_compliant")

_LIST_COLUMNS = """
    id, created_at, user_id, verdict, score, category, packs_applied,
    checks_total, passed, failed, skipped, critical, major, minor,
    source, mock, product_name, note, location
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


# --------------------------------------------------------------------------- #
# Row model
# --------------------------------------------------------------------------- #
@dataclass
class ScanRow:
    """One stored inspection. ``report`` is None in list views (not fetched)."""

    id: str
    created_at: str
    verdict: str
    score: float
    category: str
    packs_applied: list[str] = field(default_factory=list)
    checks_total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    critical: int = 0
    major: int = 0
    minor: int = 0
    source: str = "json"
    mock: bool = False
    user_id: Optional[str] = None
    product_name: Optional[str] = None
    note: Optional[str] = None
    location: Optional[str] = None
    report: Optional[dict] = None
    scan_input: Optional[dict] = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ScanRow":
        keys = row.keys()

        def maybe_json(name: str) -> Optional[dict]:
            if name not in keys or row[name] is None:
                return None
            try:
                return json.loads(row[name])
            except (json.JSONDecodeError, TypeError):
                return None

        try:
            packs = json.loads(row["packs_applied"]) if "packs_applied" in keys else []
        except (json.JSONDecodeError, TypeError):
            packs = []

        return cls(
            id=row["id"],
            created_at=row["created_at"],
            verdict=row["verdict"],
            score=_as_float(row["score"]),
            category=row["category"],
            packs_applied=packs if isinstance(packs, list) else [],
            checks_total=_as_int(row["checks_total"]),
            passed=_as_int(row["passed"]),
            failed=_as_int(row["failed"]),
            skipped=_as_int(row["skipped"]),
            critical=_as_int(row["critical"]),
            major=_as_int(row["major"]),
            minor=_as_int(row["minor"]),
            source=row["source"] if "source" in keys else "json",
            mock=bool(_as_int(row["mock"])) if "mock" in keys else False,
            user_id=row["user_id"] if "user_id" in keys else None,
            product_name=row["product_name"] if "product_name" in keys else None,
            note=row["note"] if "note" in keys else None,
            location=row["location"] if "location" in keys else None,
            report=maybe_json("report_json"),
            scan_input=maybe_json("scan_input_json"),
        )

    def to_dict(self, *, include_report: bool = False) -> dict:
        out: dict[str, Any] = {
            "id": self.id,
            "created_at": self.created_at,
            "user_id": self.user_id,
            "verdict": self.verdict,
            "score": self.score,
            "category": self.category,
            "packs_applied": self.packs_applied,
            "summary": {
                "checks_total": self.checks_total,
                "passed": self.passed,
                "failed": self.failed,
                "skipped": self.skipped,
                "violations_total": self.critical + self.major + self.minor,
                "violations_by_severity": {
                    "critical": self.critical,
                    "major": self.major,
                    "minor": self.minor,
                },
            },
            "source": self.source,
            "mock": self.mock,
            "product_name": self.product_name,
            "note": self.note,
            "location": self.location,
        }
        if include_report:
            out["report"] = self.report
            out["scan_input"] = self.scan_input
        return out


# --------------------------------------------------------------------------- #
# Writes
# --------------------------------------------------------------------------- #
def save_scan(
    report: dict,
    *,
    user_id: Optional[str] = None,
    source: str = "json",
    mock: bool = False,
    product_name: Optional[str] = None,
    note: Optional[str] = None,
    location: Optional[str] = None,
    scan_input: Optional[dict] = None,
    scan_id: Optional[str] = None,
) -> ScanRow:
    """Persist one report and its violations. Returns the stored row.

    Tolerant by design: any missing report key degrades to a safe default rather
    than raising, because losing history is far better than failing a scan.
    """
    summary = report.get("summary") or {}
    severity = summary.get("violations_by_severity") or {}
    violations = report.get("violations") or []
    packs = report.get("packs_applied") or []

    row = ScanRow(
        id=scan_id or uuid.uuid4().hex,
        created_at=_now(),
        verdict=str(report.get("verdict") or "unknown"),
        score=_as_float(report.get("score")),
        category=str(report.get("category") or "unknown"),
        packs_applied=[str(p) for p in packs] if isinstance(packs, list) else [],
        checks_total=_as_int(summary.get("checks_total")),
        passed=_as_int(summary.get("passed")),
        failed=_as_int(summary.get("failed")),
        skipped=_as_int(summary.get("skipped")),
        critical=_as_int(severity.get("critical")),
        major=_as_int(severity.get("major")),
        minor=_as_int(severity.get("minor")),
        source=source,
        mock=bool(mock),
        user_id=user_id,
        product_name=product_name,
        note=note,
        location=location,
        report=report,
        scan_input=scan_input,
    )

    # Scan + its violations go in as one transaction: history is never half-written.
    with db._lock:  # noqa: SLF001 - same package, documented concurrency model
        conn = db.connection()
        try:
            conn.execute(
                """
                INSERT INTO scans (
                    id, created_at, user_id, verdict, score, category, packs_applied,
                    checks_total, passed, failed, skipped, critical, major, minor,
                    source, mock, product_name, note, location,
                    report_json, scan_input_json
                ) VALUES (
                    :id, :created_at, :user_id, :verdict, :score, :category, :packs,
                    :checks_total, :passed, :failed, :skipped, :critical, :major, :minor,
                    :source, :mock, :product_name, :note, :location,
                    :report_json, :scan_input_json
                )
                """,
                {
                    "id": row.id,
                    "created_at": row.created_at,
                    "user_id": row.user_id,
                    "verdict": row.verdict,
                    "score": row.score,
                    "category": row.category,
                    "packs": json.dumps(row.packs_applied),
                    "checks_total": row.checks_total,
                    "passed": row.passed,
                    "failed": row.failed,
                    "skipped": row.skipped,
                    "critical": row.critical,
                    "major": row.major,
                    "minor": row.minor,
                    "source": row.source,
                    "mock": 1 if row.mock else 0,
                    "product_name": row.product_name,
                    "note": row.note,
                    "location": row.location,
                    "report_json": json.dumps(report),
                    "scan_input_json": json.dumps(scan_input) if scan_input else None,
                },
            )
            if isinstance(violations, list) and violations:
                conn.executemany(
                    """
                    INSERT INTO scan_violations (
                        scan_id, declaration_id, declaration_label,
                        legal_reference, severity, check_type, message
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            row.id,
                            str(v.get("declaration_id") or ""),
                            str(v.get("declaration_label") or ""),
                            str(v.get("legal_reference") or ""),
                            str(v.get("severity") or "minor"),
                            str(v.get("check_type") or ""),
                            str(v.get("message") or ""),
                        )
                        for v in violations
                        if isinstance(v, dict)
                    ],
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return row


def delete_scan(scan_id: str, *, user_id: Optional[str] = None) -> bool:
    """Delete one scan. When *user_id* is given, only that user's own scan.

    Violations cascade. Returns False when nothing matched, which the API turns
    into a 404 — so a consumer probing another user's scan id cannot tell the
    difference between "not yours" and "does not exist".
    """
    if user_id is None:
        n = db.execute("DELETE FROM scans WHERE id = ?", (scan_id,))
    else:
        n = db.execute(
            "DELETE FROM scans WHERE id = ? AND user_id = ?", (scan_id, user_id)
        )
    return n > 0


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #
def get_scan(scan_id: str, *, user_id: Optional[str] = None) -> Optional[ScanRow]:
    """Full scan including the verbatim report. *user_id* scopes it to an owner."""
    if user_id is None:
        row = db.query_one("SELECT * FROM scans WHERE id = ?", (scan_id,))
    else:
        row = db.query_one(
            "SELECT * FROM scans WHERE id = ? AND user_id = ?", (scan_id, user_id)
        )
    return ScanRow.from_row(row) if row else None


def list_scans(
    *,
    user_id: Optional[str] = None,
    verdict: Optional[str] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[ScanRow], int]:
    """Newest-first page of scans plus the total matching count.

    Filters compose. ``user_id`` is how the API enforces that a consumer sees only
    their own history while an officer sees everything — the caller decides, this
    function just honours it.
    """
    where: list[str] = []
    params: dict[str, Any] = {}

    if user_id is not None:
        where.append("user_id = :user_id")
        params["user_id"] = user_id
    if verdict:
        where.append("verdict = :verdict")
        params["verdict"] = verdict
    if category:
        where.append("category = :category")
        params["category"] = category
    if search:
        where.append(
            "(COALESCE(product_name,'') LIKE :q OR COALESCE(note,'') LIKE :q"
            " OR COALESCE(location,'') LIKE :q)"
        )
        params["q"] = f"%{search}%"

    clause = f" WHERE {' AND '.join(where)}" if where else ""

    total = _as_int(
        db.query_one(f"SELECT COUNT(*) AS n FROM scans{clause}", params)["n"]
    )

    page_params = dict(params)
    page_params["limit"] = max(1, min(int(limit), 200))
    page_params["offset"] = max(0, int(offset))
    rows = db.query(
        f"SELECT {_LIST_COLUMNS} FROM scans{clause}"
        " ORDER BY created_at DESC, id DESC LIMIT :limit OFFSET :offset",
        page_params,
    )
    return [ScanRow.from_row(r) for r in rows], total


def top_violations(*, user_id: Optional[str] = None, limit: int = 10) -> list[dict]:
    """Most frequently breached declarations, worst first.

    The enforcement-intelligence query: it answers "what are sellers getting wrong
    across the whole corpus", which no single-label check can tell you.

    Two subtleties worth knowing:

    * ``occurrences`` counts *violations* and ``scans_affected`` counts *products*.
      They differ because one declaration can fail several checks on the same label
      (net quantity can be absent, malformed, *and* under the Rule 7(2) minimum
      height at once). ``scans_affected`` is the honest headline — "how many
      products breached this rule" — so it drives the ordering.
    * Severity is aggregated by *semantic* worst, not by SQL ``MAX``. Severities are
      stored as text, and alphabetically ``'minor' > 'major' > 'critical'``, so a
      naive MAX() would report the least severe of a mixed group — precisely
      inverted for an enforcement tool. The CASE ranking below fixes the ordering
      to critical < major < minor and maps the winner back to its label.
    """
    join = ""
    params: dict[str, Any] = {"limit": max(1, min(int(limit), 100))}
    if user_id is not None:
        join = " JOIN scans s ON s.id = v.scan_id AND s.user_id = :user_id"
        params["user_id"] = user_id

    severity_rank = (
        "CASE v.severity WHEN 'critical' THEN 1 WHEN 'major' THEN 2"
        " WHEN 'minor' THEN 3 ELSE 4 END"
    )

    rows = db.query(
        f"""
        SELECT v.declaration_id                AS declaration_id,
               MAX(v.declaration_label)        AS declaration_label,
               MAX(v.legal_reference)          AS legal_reference,
               CASE MIN({severity_rank})
                    WHEN 1 THEN 'critical'
                    WHEN 2 THEN 'major'
                    WHEN 3 THEN 'minor'
                    ELSE 'unknown'
               END                             AS severity,
               COUNT(*)                        AS occurrences,
               COUNT(DISTINCT v.scan_id)       AS scans_affected
          FROM scan_violations v{join}
         GROUP BY v.declaration_id
         ORDER BY scans_affected DESC, occurrences DESC, declaration_id ASC
         LIMIT :limit
        """,
        params,
    )
    return [dict(r) for r in rows]


def aggregate_stats(*, user_id: Optional[str] = None) -> dict:
    """Dashboard aggregates, computed in SQL over the denormalised columns.

    Mirrors the Flutter ``ScanStore`` maths exactly — including excluding
    ``no_label_detected`` reads from score and rate averages — so the server-backed
    dashboard shows the same numbers the in-memory one did.
    """
    scope = " WHERE user_id = :user_id" if user_id is not None else ""
    params: dict[str, Any] = {"user_id": user_id} if user_id is not None else {}

    total = _as_int(db.query_one(f"SELECT COUNT(*) AS n FROM scans{scope}", params)["n"])

    by_verdict = {
        r["verdict"]: _as_int(r["n"])
        for r in db.query(
            f"SELECT verdict, COUNT(*) AS n FROM scans{scope} GROUP BY verdict", params
        )
    }

    by_category = [
        {"category": r["category"], "scans": _as_int(r["n"]),
         "average_score": round(_as_float(r["avg"]), 1)}
        for r in db.query(
            f"""
            SELECT category, COUNT(*) AS n, AVG(score) AS avg
              FROM scans{scope}
             GROUP BY category ORDER BY n DESC, category ASC LIMIT 20
            """,
            params,
        )
    ]

    # Scored-only aggregates (exclude no_label_detected).
    placeholders = ", ".join(f":v{i}" for i in range(len(SCORED_VERDICTS)))
    scored_params = dict(params)
    for i, v in enumerate(SCORED_VERDICTS):
        scored_params[f"v{i}"] = v
    scored_where = f"{scope} AND" if scope else " WHERE"
    scored = db.query_one(
        f"""
        SELECT COUNT(*) AS n,
               AVG(score) AS avg_score,
               SUM(critical) AS critical,
               SUM(major)    AS major,
               SUM(minor)    AS minor
          FROM scans{scored_where} verdict IN ({placeholders})
        """,
        scored_params,
    )
    scored_n = _as_int(scored["n"])
    compliant = _as_int(by_verdict.get("compliant"))

    # Severity totals across *all* scans, matching the client's violationTotals.
    sev = db.query_one(
        f"""
        SELECT COALESCE(SUM(critical),0) AS critical,
               COALESCE(SUM(major),0)    AS major,
               COALESCE(SUM(minor),0)    AS minor
          FROM scans{scope}
        """,
        params,
    )

    return {
        "total_scans": total,
        "scored_scans": scored_n,
        "by_verdict": {
            "compliant": compliant,
            "needs_review": _as_int(by_verdict.get("needs_review")),
            "non_compliant": _as_int(by_verdict.get("non_compliant")),
            "no_label_detected": _as_int(by_verdict.get("no_label_detected")),
        },
        "average_score": round(_as_float(scored["avg_score"]), 1) if scored_n else 0.0,
        "compliance_rate": round(100.0 * compliant / scored_n, 1) if scored_n else 0.0,
        "violations_by_severity": {
            "critical": _as_int(sev["critical"]),
            "major": _as_int(sev["major"]),
            "minor": _as_int(sev["minor"]),
        },
        "violations_total": (
            _as_int(sev["critical"]) + _as_int(sev["major"]) + _as_int(sev["minor"])
        ),
        "by_category": by_category,
        "top_violations": top_violations(user_id=user_id, limit=10),
    }
