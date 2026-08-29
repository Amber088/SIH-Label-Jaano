"""
Who is allowed to become an officer at sign-up.

The problem: the officer role can read every inspection in the corpus, so letting
a registration form choose it would make the whole access-control model decorative.
The problem in the other direction: a hackathon demo has to be able to *show* the
officer experience without a database migration or an out-of-band admin step.

The resolution is a shared enrolment code. Set ``LABEL_JAANO_OFFICER_CODE`` and
anyone who presents it at sign-up is enrolled as an officer; leave it unset and
officer sign-up is simply unavailable, with :mod:`manage.py` as the deliberate,
local path to creating staff accounts. Both directions fail closed:

* no code configured  -> officer sign-up refused outright
* wrong code presented -> refused (and compared in constant time)

This is intentionally not a real identity system. A production deployment would
enrol officers against a departmental directory; the seam for that is this one
function, which is the only place a role is chosen from untrusted input.
"""
from __future__ import annotations

import hmac
import os

from .roles import DEFAULT_ROLE, Role

__all__ = [
    "OFFICER_CODE_ENV",
    "OfficerCodeRejected",
    "officer_code_configured",
    "role_for_signup",
]

OFFICER_CODE_ENV = "LABEL_JAANO_OFFICER_CODE"


class OfficerCodeRejected(Exception):
    """The requested role needs an enrolment code that was missing or wrong."""


def officer_code_configured() -> bool:
    """True when an officer enrolment code is set in the environment."""
    return os.environ.get(OFFICER_CODE_ENV, "").strip() != ""


def role_for_signup(requested: str | Role | None, officer_code: str | None) -> Role:
    """Decide the role a self-registration may have.

    Consumer is always allowed. Officer requires the configured code. Admin can
    never be obtained through sign-up at all — it exists only via ``manage.py``.
    """
    role = Role.parse(requested, default=DEFAULT_ROLE)

    if role is Role.CONSUMER:
        return Role.CONSUMER

    if role is Role.ADMIN:
        raise OfficerCodeRejected(
            "Administrator accounts cannot be created through sign-up. "
            "Use manage.py on the server."
        )

    # role is OFFICER from here on.
    expected = os.environ.get(OFFICER_CODE_ENV, "").strip()
    if not expected:
        raise OfficerCodeRejected(
            "Officer sign-up is disabled on this server. Ask an administrator to "
            f"set {OFFICER_CODE_ENV}, or create the account with manage.py."
        )
    presented = (officer_code or "").strip()
    if not presented:
        raise OfficerCodeRejected("An officer enrolment code is required.")
    if not hmac.compare_digest(presented, expected):
        raise OfficerCodeRejected("That officer enrolment code is not valid.")
    return Role.OFFICER
