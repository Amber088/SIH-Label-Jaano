"""
Short-lived, single-scan tickets for sharing a printable inspection report.

The problem this solves is mundane and, if solved carelessly, dangerous. An officer
finishes an inspection on their phone and needs the report on paper — but the phone
cannot print, and ``GET /scans/{id}/report.html`` needs an ``Authorization`` header,
which a browser address bar cannot supply. The obvious shortcut is to accept the
session token as a query parameter. That shortcut is how credentials end up in
access logs, browser history, and ``Referer`` headers, and it hands anyone who
glances at the URL a full API login for the next twelve hours.

So the phone asks the server to mint a *ticket* instead, and what comes back is
deliberately almost useless:

* **One scan.** The scan id is inside the signature, so the ticket cannot be edited
  to point at a different inspection.
* **Read-only, one route.** It carries the purpose ``report``, and
  :func:`auth.decode` refuses it everywhere the purpose is not asked for by name —
  including :func:`app.deps.optional_user`. Pasting a ticket into an
  ``Authorization`` header fails; it is not a login.
* **Minutes, not hours.** :data:`REPORT_TICKET_TTL_SECONDS` is fifteen minutes,
  enough to walk to a printer and not much more.

It is still a bearer secret and anyone holding the link can read that one report
until it lapses, which is the whole point — that is what "share" means. The design
goal is that the blast radius of a forwarded link is one PDF, not an account.
"""
from __future__ import annotations

from typing import Any, Optional

from .tokens import PURPOSE_CLAIM, TokenInvalid, decode, encode

__all__ = [
    "PURPOSE_REPORT",
    "REPORT_TICKET_TTL_SECONDS",
    "mint_report_ticket",
    "read_report_ticket",
]

PURPOSE_REPORT = "report"

#: Long enough to hand the link to a laptop and print it; short enough that a link
#: pasted into a group chat is dead before it can be passed around.
REPORT_TICKET_TTL_SECONDS = 15 * 60


def mint_report_ticket(
    *,
    scan_id: str,
    user_id: str,
    ttl_seconds: int = REPORT_TICKET_TTL_SECONDS,
    now: Optional[int] = None,
) -> str:
    """A signed ticket granting read access to exactly one stored report.

    *user_id* is recorded as the subject so the rendered report can still attribute
    the inspection correctly (an officer's name belongs on their own findings and
    nobody else's) and so a revoked or disabled account's outstanding links stop
    working — the endpoint re-resolves the subject on every use, exactly as it does
    for a bearer token.
    """
    return encode(
        {"sub": str(user_id), "scan": str(scan_id), PURPOSE_CLAIM: PURPOSE_REPORT},
        ttl_seconds=ttl_seconds,
        now=now,
    )


def read_report_ticket(
    ticket: str,
    *,
    scan_id: str,
    now: Optional[int] = None,
) -> dict[str, Any]:
    """Verify *ticket* and confirm it was minted for *scan_id*. Returns its claims.

    Raises :class:`auth.TokenExpired` if it has lapsed and
    :class:`auth.TokenInvalid` (or :class:`auth.TokenWrongPurpose`) otherwise.

    The scan id is compared here rather than being trusted from the path, because
    the two arriving separately is the entire attack: a valid ticket for a scan you
    own, replayed against a scan id you do not. Both are inside the same signature,
    so they must agree.
    """
    claims = decode(ticket, purpose=PURPOSE_REPORT, now=now)
    if claims.get("scan") != str(scan_id):
        raise TokenInvalid("this link was issued for a different inspection")
    if not claims.get("sub"):
        raise TokenInvalid("link has no subject")
    return claims
