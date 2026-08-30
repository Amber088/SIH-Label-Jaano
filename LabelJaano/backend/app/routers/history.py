"""
History endpoints — the officer's queue, the consumer's own record, and the export.

Scoping is the whole design. Every read here passes through
:func:`app.deps.history_scope`, which returns ``None`` for a caller who may see the
whole corpus and the caller's own id for everyone else. The queries are then
identical for both roles, so there is no route that could forget its WHERE clause —
the scope is decided once, in one function, rather than re-derived per endpoint.

That also gives per-record hiding for free. A consumer requesting another user's scan
id gets a scoped lookup that finds nothing, so the answer is a plain 404. It is
indistinguishable from a nonexistent id, which means the endpoint cannot be used to
discover which ids exist.

The one endpoint that is not reached with a session token is the printable report,
which can also be opened with a short-lived share ticket so that a phone can hand a
report to a browser and print it. The ticket is scoped to a single inspection and is
rejected everywhere a login is expected; :mod:`auth.tickets` explains why that
separation is load-bearing rather than decorative.
"""
from __future__ import annotations

import csv
import io
import time
from datetime import datetime, timezone
from typing import Iterator, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse

import auth
import store
from auth.roles import Permission, Role
from reports import render_inspection_html
from store.users import User

from .. import deps
from ..schemas import (
    ReportLinkOut,
    ScanDetailOut,
    ScanListOut,
    ScanSummaryOut,
    StatsOut,
)

router = APIRouter(tags=["history"], dependencies=[Depends(deps.require_persistence)])

#: Upper bound on a share link's life. Two hours is enough to walk a report to a
#: printer or hand it to a colleague; it is not enough to be a de-facto password.
MAX_SHARE_MINUTES = 120

#: Hard ceiling on one CSV export. A district's corpus is the intended size; this
#: exists so a malformed filter cannot stream the entire table into a phone. The
#: response says so in a trailing comment row when it bites, because a truncated
#: register that looks complete is worse than a refusal.
MAX_EXPORT_ROWS = 20_000


def _scope_name(user_id: Optional[str]) -> str:
    return "own" if user_id else "all"


@router.get("/scans", response_model=ScanListOut,
            summary="Inspection history (own, or the whole corpus for an officer)")
def list_scans(
    request: Request,
    user: User = Depends(deps.require_user),
    verdict: Optional[str] = Query(None, description="filter: compliant | needs_review | non_compliant | no_label_detected"),
    category: Optional[str] = Query(None, description="filter by product category"),
    search: Optional[str] = Query(None, description="substring match on product name, note or place"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ScanListOut:
    scope = deps.history_scope(user)
    rows, total = store.list_scans(
        user_id=scope, verdict=verdict, category=category,
        search=search, limit=limit, offset=offset,
    )
    if scope is None:
        # Only the whole-corpus read is audited. A consumer paging their own history
        # is not an event, and logging it would bury the reads that matter under
        # ordinary traffic — see :mod:`store.audit`.
        deps.record_audit(
            store.audit.CORPUS_LIST, user, request,
            detail={"verdict": verdict, "category": category, "search": search,
                    "returned": len(rows), "total": total, "offset": offset},
        )
    return ScanListOut(
        items=[ScanSummaryOut(**r.to_dict()) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
        scope=_scope_name(scope),
    )


@router.get("/scans/{scan_id}", response_model=ScanDetailOut,
            summary="One stored inspection with its verbatim report")
def get_scan(scan_id: str, request: Request,
             user: User = Depends(deps.require_user)) -> ScanDetailOut:
    row = store.get_scan(scan_id, user_id=deps.history_scope(user))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No such inspection.")
    if row.user_id != user.id:
        # Somebody opened an inspection that is not theirs, which only a whole-corpus
        # role can do. "Who read this file" is the first question asked when a finding
        # is disputed, so it is recorded whether or not anything was changed.
        deps.record_audit(
            store.audit.CORPUS_READ, user, request,
            target=row.id,
            detail={"owner_id": row.user_id, "verdict": row.verdict},
        )
    return ScanDetailOut(**row.to_dict(include_report=True))


@router.delete("/scans/{scan_id}", status_code=status.HTTP_204_NO_CONTENT,
               summary="Delete a stored inspection")
def delete_scan(scan_id: str, request: Request,
                user: User = Depends(deps.require_user)) -> Response:
    """Delete one inspection.

    Note the scope here is *not* :func:`history_scope`. Being able to read the whole
    corpus (officer) and being able to delete from it (admin) are separate
    permissions, so an officer may delete only their own records even though they can
    see everyone's. Destroying another inspector's evidence should not be a side
    effect of being able to review it.
    """
    may_delete_any = Role.parse(user.role).can(Permission.DELETE_ANY_SCAN)
    # Read the row before destroying it: afterwards there is nothing left to describe,
    # and an audit line reading only "deleted <uuid>" is not a record of what was lost.
    doomed = store.get_scan(scan_id, user_id=None if may_delete_any else user.id)
    deleted = store.delete_scan(scan_id, user_id=None if may_delete_any else user.id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No such inspection.")
    deps.record_audit(
        store.audit.SCAN_DELETE, user, request,
        target=scan_id,
        detail={
            "owner_id": doomed.user_id if doomed else None,
            "own": bool(doomed and doomed.user_id == user.id),
            "verdict": doomed.verdict if doomed else None,
            "product_name": doomed.product_name if doomed else None,
            "created_at": doomed.created_at if doomed else None,
        },
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _readable_scan(scan_id: str, user: User):
    """The scan as *user* is allowed to see it, or the right error. Never leaks ids."""
    row = store.get_scan(scan_id, user_id=deps.history_scope(user))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No such inspection.")
    if not row.report:
        raise HTTPException(
            status_code=deps.HTTP_422,
            detail="This inspection has no stored report to render.",
        )
    return row


@router.post("/scans/{scan_id}/share", response_model=ReportLinkOut,
             summary="Mint a short-lived link to this report (no login needed to open)")
def share_scan_report(
    scan_id: str,
    request: Request,
    user: User = Depends(deps.require_user),
    minutes: int = Query(15, ge=1, le=MAX_SHARE_MINUTES,
                         description="how long the link stays valid"),
) -> ReportLinkOut:
    """Turn one stored inspection into a link any browser can open.

    This exists because a phone cannot print. The officer needs the report on paper
    or as a PDF, which means getting it into a browser, and a browser address bar
    cannot send an ``Authorization`` header. The tempting shortcut — accept the
    session token as ``?token=`` — would scatter a twelve-hour API credential through
    access logs, browser history and ``Referer`` headers. So the link carries a
    purpose-scoped ticket instead: one inspection, read-only, minutes long, and
    refused outright anywhere a session token is expected.

    Only inspections you may already read can be shared, so this widens nothing: an
    officer can share any inspection they can open, a consumer only their own.
    """
    row = _readable_scan(scan_id, user)
    ttl = int(minutes) * 60
    issued = int(time.time())
    ticket = auth.mint_report_ticket(
        scan_id=row.id, user_id=user.id, ttl_seconds=ttl, now=issued,
    )
    expires = datetime.fromtimestamp(issued + ttl, tz=timezone.utc)
    # Minting a link that opens a report with no login is a widening of access, even
    # though it grants nothing the minter did not already have. Recorded for every
    # caller, not just officers: a consumer sharing their own report is still the
    # creation of a bearer credential, and its lifetime is worth knowing.
    deps.record_audit(
        store.audit.REPORT_SHARE, user, request,
        target=row.id,
        detail={"minutes": int(minutes), "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "own": row.user_id == user.id},
    )
    return ReportLinkOut(
        scan_id=row.id,
        path=f"/scans/{quote(row.id, safe='')}/report.html?ticket={quote(ticket, safe='')}",
        ticket=ticket,
        # Second resolution and a trailing Z: an officer reads this, and a fractional
        # "+00:00" timestamp is noise on a screen that is mostly about a deadline.
        expires_at=expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
        expires_in_seconds=ttl,
    )


@router.get("/scans/{scan_id}/report.html", response_class=Response,
            responses={200: {"content": {"text/html": {}},
                             "description": "Print-ready inspection report"}},
            summary="Print-ready inspection report (browser: Print → Save as PDF)")
def scan_report_html(
    scan_id: str,
    user: Optional[User] = Depends(deps.optional_user),
    ticket: Optional[str] = Query(None, description="share ticket from POST /scans/{id}/share, for opening this one report without signing in"),
    appendix: bool = Query(True, description="include the full assessment log"),
    download: bool = Query(False, description="send as a file attachment instead of rendering inline"),
) -> Response:
    """Render one stored inspection as a self-contained, print-ready HTML document.

    Two ways in. A signed-in client sends its bearer token as usual. A browser opening
    a shared link sends nothing but a ``ticket``, which grants read access to this one
    report and nothing else — see :func:`share_scan_report`.

    The bearer token wins when both are present, and note that a *broken* bearer token
    is still an error rather than a silent fallback to the ticket: a client whose
    session lapsed should be told so, not quietly downgraded to link-holder access.
    """
    viewer = user
    if viewer is None:
        if not (ticket or "").strip():
            raise deps.unauthorised(
                "Sign in, or open this report using a share link from the app."
            )
        viewer = deps.user_from_report_ticket(ticket, scan_id=scan_id)

    row = _readable_scan(scan_id, viewer)

    # The report is attributed to whoever is reading it only when they are the one
    # who filed it. An officer reviewing someone else's inspection must not have
    # their own name printed in the signature block of that finding.
    inspector = viewer.to_dict() if row.user_id == viewer.id else None

    html = render_inspection_html(
        row.report,
        scan_id=row.id,
        created_at=row.created_at,
        product_name=row.product_name,
        note=row.note,
        location=row.location,
        source=row.source,
        mock=row.mock,
        inspector=inspector,
        include_appendix=appendix,
    )
    headers = {}
    if download:
        safe_id = "".join(c for c in row.id if c.isalnum() or c in "-_")[:32]
        headers["Content-Disposition"] = (
            f'attachment; filename="label-jaano-report-{safe_id}.html"'
        )
    return Response(content=html, media_type="text/html; charset=utf-8", headers=headers)


@router.get("/stats", response_model=StatsOut,
            summary="Dashboard aggregates over the accessible corpus")
def stats(request: Request, user: User = Depends(deps.require_user)) -> StatsOut:
    """Aggregates for the dashboard, computed in SQL.

    An officer gets the whole corpus, which is the point: "which declaration do
    sellers breach most often" is a question no single label can answer, and it is
    what turns a pile of scans into enforcement intelligence. A consumer gets the same
    shape over their own scans.
    """
    scope = deps.history_scope(user)
    payload = store.aggregate_stats(user_id=scope)
    if scope is None:
        deps.record_audit(
            store.audit.CORPUS_STATS, user, request,
            detail={"scans": payload.get("total_scans")},
        )
    return StatsOut(**payload, scope=_scope_name(scope))


# --------------------------------------------------------------------------- #
# Bulk export
# --------------------------------------------------------------------------- #
#: The export's columns, in order. Named explicitly rather than derived from
#: ``ScanRow`` so that adding a field to the row does not silently change the shape of
#: a file somebody's spreadsheet or evidence workflow already depends on.
_CSV_COLUMNS = (
    "scan_id", "created_at", "verdict", "score", "category",
    "product_name", "location", "note",
    "checks_total", "passed", "failed", "skipped",
    "critical", "major", "minor",
    "source", "mock", "packs_applied", "user_id",
)


def _csv_row(row: store.ScanRow) -> list:
    return [
        row.id,
        row.created_at,
        row.verdict,
        f"{row.score:.1f}",
        row.category,
        row.product_name or "",
        row.location or "",
        # Newlines in a free-text note are legal CSV inside quotes but they break
        # naive line-oriented tooling, and an officer's note is not worth a support
        # call. Flattened to spaces; the verbatim text is still in the report.
        " ".join((row.note or "").split()),
        row.checks_total,
        row.passed,
        row.failed,
        row.skipped,
        row.critical,
        row.major,
        row.minor,
        row.source,
        "yes" if row.mock else "no",
        " ".join(row.packs_applied or []),
        row.user_id or "",
    ]


def _csv_stream(rows: Iterator[store.ScanRow], *, max_rows: int) -> Iterator[str]:
    """Yield the CSV a chunk at a time, so nothing buffers the whole corpus in RAM.

    An ``io.StringIO`` is reused as a one-row scratch buffer rather than joining
    strings by hand, because the quoting rules for embedded commas and quotes are
    exactly the thing to not reimplement.

    A BOM leads the file. It is ugly, and it is there because Excel on Windows reads a
    BOM-less UTF-8 CSV as the system codepage — which mangles every Devanagari product
    name in the export. The officers this is for are on Windows.

    *rows* is expected to be able to yield ``max_rows + 1`` items: the extra one is
    never written and exists only to distinguish "the corpus happens to be exactly
    max_rows long" from "there was more and we stopped", so the truncation warning is
    never a false alarm at an exact boundary.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")

    def flush() -> str:
        text = buf.getvalue()
        buf.seek(0)
        buf.truncate(0)
        return text

    writer.writerow(_CSV_COLUMNS)
    yield "\ufeff" + flush()

    written = 0
    truncated = False
    for row in rows:
        if written >= max_rows:
            truncated = True
            break
        writer.writerow(_csv_row(row))
        written += 1
        if written % store.EXPORT_BATCH == 0:
            yield flush()
    tail = flush()
    if tail:
        yield tail

    if truncated:
        # Say so *in the file*. A truncated register that looks complete is the
        # failure mode worth engineering against, and a response header would be
        # dropped by every spreadsheet, so the warning goes where the reader will
        # actually see it.
        writer.writerow([f"# TRUNCATED at {max_rows} rows — narrow the filters "
                         f"(verdict, category, search) and export again"])
        yield flush()


@router.get("/scans.csv", response_class=StreamingResponse,
            responses={200: {"content": {"text/csv": {}},
                             "description": "One row per inspection"}},
            summary="Bulk CSV of the accessible corpus (officer: everything)")
def export_scans_csv(
    request: Request,
    user: User = Depends(deps.require_user),
    verdict: Optional[str] = Query(None, description="filter: compliant | needs_review | non_compliant | no_label_detected"),
    category: Optional[str] = Query(None, description="filter by product category"),
    search: Optional[str] = Query(None, description="substring match on product name, note or place"),
    max_rows: int = Query(MAX_EXPORT_ROWS, ge=1, le=MAX_EXPORT_ROWS),
) -> StreamingResponse:
    """Stream the accessible inspections as CSV, oldest first.

    This is the endpoint that makes the corpus usable as evidence rather than only as
    a screen. An enforcement action is prepared in a spreadsheet and filed as an
    attachment, so the queue has to leave the app: a JSON list an officer cannot open
    in Excel is a dashboard, not a register.

    Three properties are deliberate.

    *Same filters, same rows.* ``verdict``/``category``/``search`` mean exactly what
    they mean on ``GET /scans`` — both go through ``store``'s single WHERE builder — so
    the file cannot disagree with what the officer just read on screen.

    *Scoped, not privileged.* The rows are whatever :func:`deps.history_scope` allows,
    so a consumer exporting gets their own inspections and nothing widens. Only the
    whole-corpus export is audited.

    *Streamed, oldest first.* Rows are pulled in keyset batches (see
    :func:`store.iter_scans_for_export`), so memory is flat in the size of the corpus
    and a row saved mid-export cannot shift the window and duplicate a line. Oldest
    first because a register reads forward in time.
    """
    scope = deps.history_scope(user)
    rows = store.iter_scans_for_export(
        user_id=scope, verdict=verdict, category=category, search=search,
        # One more than we will write: see :func:`_csv_stream` — the extra row is what
        # makes the truncation warning exact rather than a guess at the boundary.
        max_rows=max_rows + 1,
    )
    if scope is None:
        deps.record_audit(
            store.audit.CORPUS_EXPORT, user, request,
            detail={"verdict": verdict, "category": category, "search": search,
                    "max_rows": max_rows},
        )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    filename = f"label-jaano-inspections-{_scope_name(scope)}-{stamp}.csv"
    return StreamingResponse(
        _csv_stream(rows, max_rows=max_rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
