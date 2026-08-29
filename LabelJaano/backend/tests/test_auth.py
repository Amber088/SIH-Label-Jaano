#!/usr/bin/env python3
"""
Tests for the authentication layer: password verifiers, signed tokens, the role
permission matrix, and how a role is chosen at sign-up.

Runs two ways:
    pytest                      # from the backend/ directory
    python3 tests/test_auth.py  # no pytest needed — self-contained runner

This module is hand-rolled crypto plumbing over ``hmac``/``hashlib``, which is only
defensible if the strictness is actually verified. So the tests are written as
attacks wherever possible, not as happy paths:

* ``test_alg_none_is_rejected`` and ``test_alg_confusion_is_rejected`` cover the
  classic JWT break — a permissive library that honours the token's own ``alg``
  header lets anyone mint an admin. The algorithm is pinned in code; these prove it.
* ``test_signature_is_checked_before_claims`` proves unsigned input never reaches
  claim parsing.
* ``test_token_without_exp_is_rejected`` proves a missing expiry is an error rather
  than an eternal session.
* ``test_role_parse_degrades_to_least_privilege`` proves a corrupted role never
  fails *open*.
* ``test_signup_can_never_produce_an_admin`` proves privilege cannot be requested.
* The ``report ticket`` block proves the two things minted by the same key cannot be
  swapped: a share link is not a login, and a login is not a share link.
"""
from __future__ import annotations

import base64
import json
import sys
import time
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import auth  # noqa: E402
from auth import passwords, registration, tickets, tokens  # noqa: E402
from auth.roles import DEFAULT_ROLE, Permission, Role  # noqa: E402

SECRET = b"a-test-secret-that-is-not-the-real-one"


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _forge(header: dict, payload: dict, signature: str = "") -> str:
    """Assemble a token by hand, so the tests can present malformed input."""
    return ".".join([
        _b64e(json.dumps(header, separators=(",", ":"), sort_keys=True).encode()),
        _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()),
        signature,
    ])


_HS256_HEADER = {"alg": "HS256", "typ": "JWT"}


def _unsigned(payload: dict, header: dict = None) -> str:
    """The ``header.payload`` signing input for *payload*."""
    head = _HS256_HEADER if header is None else header
    return ".".join([
        _b64e(json.dumps(head, separators=(",", ":"), sort_keys=True).encode()),
        _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()),
    ])


def _properly_signed(payload: dict, header: dict = None) -> str:
    """A token signed correctly with SECRET, so only its *claims* are under test."""
    unsigned = _unsigned(payload, header)
    return f"{unsigned}.{tokens._sign(unsigned.encode('ascii'), SECRET)}"


def _rejects(token, *, expect=tokens.TokenInvalid, **kw) -> None:
    try:
        tokens.decode(token, secret=SECRET, **kw)
    except expect:
        return
    except tokens.TokenError as exc:
        raise AssertionError(
            f"expected {expect.__name__}, got {type(exc).__name__}: {exc}")
    raise AssertionError(f"token was accepted but should have been rejected: {token!r}")


# --------------------------------------------------------------------------- #
# Passwords
# --------------------------------------------------------------------------- #
def test_hash_and_verify_round_trip():
    encoded = passwords.hash_password("correct-horse-battery")
    assert passwords.verify_password("correct-horse-battery", encoded) is True
    assert passwords.verify_password("wrong", encoded) is False


def test_verifier_is_salted():
    """Two accounts with the same password must not share a verifier, or one cracked
    hash would reveal every account that reused that password."""
    a = passwords.hash_password("same-password-123")
    b = passwords.hash_password("same-password-123")
    assert a != b, "identical passwords produced identical verifiers — salt is missing"
    assert passwords.verify_password("same-password-123", a)
    assert passwords.verify_password("same-password-123", b)


def test_verifier_stores_no_plaintext():
    encoded = passwords.hash_password("plaintext-leak-check")
    assert "plaintext-leak-check" not in encoded


def test_verify_password_survives_malformed_input():
    """A corrupt or truncated verifier column must return False, not raise — an
    exception here would turn a bad row into a 500 on the login path."""
    for junk in ("", "not-a-hash", "pbkdf2_sha256$", "a$b$c$d", "$$$$",
                 "pbkdf2_sha256$notanumber$salt$hash"):
        assert passwords.verify_password("anything", junk) is False, junk


def test_password_problem_enforces_a_floor():
    assert passwords.password_problem("x" * passwords.MIN_PASSWORD_LENGTH) is None
    assert passwords.password_problem("short") is not None
    assert passwords.password_problem("") is not None
    assert passwords.password_problem("   " * 4) is not None, (
        "whitespace should not count towards the minimum length")


def test_needs_rehash_tracks_the_iteration_count():
    weak = passwords.hash_password("password123", iterations=1000)
    assert passwords.needs_rehash(weak) is True, (
        "a verifier below the current cost must be flagged for upgrade")
    current = passwords.hash_password("password123")
    assert passwords.needs_rehash(current) is False


# --------------------------------------------------------------------------- #
# Tokens — happy path
# --------------------------------------------------------------------------- #
def test_encode_decode_round_trip():
    token = tokens.encode({"sub": "u1", "role": "officer"}, secret=SECRET)
    claims = tokens.decode(token, secret=SECRET)
    assert claims["sub"] == "u1" and claims["role"] == "officer"
    assert claims["iss"] == tokens.ISSUER
    assert claims["exp"] > claims["iat"], "exp must be after iat"


def test_default_ttl_is_a_shift_not_forever():
    token = tokens.encode({"sub": "u1"}, secret=SECRET)
    claims = tokens.decode(token, secret=SECRET)
    assert claims["exp"] - claims["iat"] == tokens.DEFAULT_TTL_SECONDS
    assert tokens.DEFAULT_TTL_SECONDS <= 24 * 3600, (
        "a token good for more than a day is not a session")


def test_clock_skew_leeway_is_tolerated():
    """A phone a few seconds ahead of the server must not be signed out."""
    now = int(time.time())
    token = tokens.encode({"sub": "u1"}, ttl_seconds=0, secret=SECRET, now=now)
    tokens.decode(token, secret=SECRET, now=now + 5)  # inside the leeway: no raise


# --------------------------------------------------------------------------- #
# Tokens — attacks
# --------------------------------------------------------------------------- #
def test_alg_none_is_rejected():
    """The canonical JWT break: strip the signature and claim ``alg: none``."""
    _rejects(_forge({"alg": "none", "typ": "JWT"},
                    {"sub": "u1", "role": "admin", "iss": tokens.ISSUER,
                     "exp": int(time.time()) + 3600}))


def test_alg_confusion_is_rejected():
    """A token whose header asks for a different algorithm must not talk us out of
    HS256, even when the HMAC happens to verify under our own secret."""
    _rejects(_properly_signed(
        {"sub": "u1", "role": "admin", "iss": tokens.ISSUER,
         "exp": int(time.time()) + 3600},
        header={"alg": "HS512", "typ": "JWT"},
    ))


def test_signature_is_checked_before_claims():
    """An unsigned token with a deliberately unparseable payload must fail on the
    signature, proving claim parsing never sees unverified bytes."""
    token = _b64e(b'{"alg":"HS256","typ":"JWT"}') + ".!!!not-base64-json!!!.nosignature"
    try:
        tokens.decode(token, secret=SECRET)
    except tokens.TokenInvalid as exc:
        assert "signature" in str(exc).lower(), (
            f"failed on payload parsing rather than the signature: {exc}")
        return
    raise AssertionError("malformed unsigned token was accepted")


def test_tampered_payload_is_rejected():
    token = tokens.encode({"sub": "u1", "role": "consumer"}, secret=SECRET)
    header_b64, payload_b64, sig = token.split(".")
    escalated = _b64e(json.dumps(
        {**json.loads(base64.urlsafe_b64decode(payload_b64 + "==")), "role": "admin"},
        separators=(",", ":"), sort_keys=True).encode())
    _rejects(f"{header_b64}.{escalated}.{sig}")


def test_wrong_secret_is_rejected():
    token = tokens.encode({"sub": "u1"}, secret=b"attacker-secret")
    _rejects(token)


def test_expired_token_reports_expiry_not_invalidity():
    """The distinction matters to the UI: "your session ended, sign in again" is a
    different message from "that token is not ours"."""
    token = tokens.encode({"sub": "u1"}, ttl_seconds=-3600, secret=SECRET)
    _rejects(token, expect=tokens.TokenExpired)


def test_token_without_exp_is_rejected():
    """A missing expiry must be an error, never an eternal token."""
    _rejects(_properly_signed({"sub": "u1", "iss": tokens.ISSUER}))   # no exp


def test_foreign_issuer_is_rejected():
    """A token signed for a different service must not be honoured here, even when the
    signature checks out — shared secrets between deployments are a real mistake."""
    _rejects(_properly_signed(
        {"sub": "u1", "iss": "somebody-else", "exp": int(time.time()) + 3600}))


def test_structurally_broken_tokens_are_rejected():
    for junk in ("", "onlyonesegment", "two.segments", "a.b.c.d",
                 "....", "x" * (tokens._MAX_TOKEN_CHARS + 1), None, 12345, []):
        _rejects(junk)


# --------------------------------------------------------------------------- #
# Roles
# --------------------------------------------------------------------------- #
def test_default_role_is_least_privileged():
    assert DEFAULT_ROLE is Role.CONSUMER
    assert list(Role)[0] is Role.CONSUMER, (
        "declaration order matters: the first member is what a corrupted value "
        "falls back to")
    for permission in Permission:
        assert Role.CONSUMER.can(permission) is False, (
            f"a consumer must hold no permissions, but holds {permission}")


def test_officer_can_review_but_not_administer():
    assert Role.OFFICER.can(Permission.VIEW_ALL_SCANS) is True
    assert Role.OFFICER.can(Permission.VIEW_AGGREGATE_STATS) is True
    # Reading the corpus and destroying part of it are deliberately separate.
    assert Role.OFFICER.can(Permission.DELETE_ANY_SCAN) is False
    assert Role.OFFICER.can(Permission.RELOAD_RULEPACKS) is False
    assert Role.OFFICER.can(Permission.MANAGE_USERS) is False


def test_admin_holds_every_permission():
    for permission in Permission:
        assert Role.ADMIN.can(permission) is True, f"admin is missing {permission}"


def test_role_parse_degrades_to_least_privilege():
    """A corrupted role column must fail closed. Anything unrecognised is a consumer,
    never an officer and certainly never an admin.

    Note what is *not* in this list: ``"ADMIN "``. Case and surrounding whitespace are
    normalised on purpose (see ``test_role_parse_accepts_valid_values_case_insensitively``)
    — a role that stopped working because a migration wrote ``"Officer"`` would be a
    worse bug than the strictness would be worth. What must never be tolerated is a
    value that is not a role at all.
    """
    for junk in ("", "  ", "administrator", "root", "офицер", None, 0, [],
                 "officer;--", "consumer\nadmin", "admin\x00", "officer officer"):
        parsed = Role.parse(junk)
        assert parsed is Role.CONSUMER, f"{junk!r} parsed as {parsed}, expected consumer"


def test_role_parse_accepts_valid_values_case_insensitively():
    assert Role.parse("OFFICER") is Role.OFFICER
    assert Role.parse(" admin ") is Role.ADMIN
    assert Role.parse(Role.OFFICER) is Role.OFFICER


def test_role_labels_are_human_readable():
    assert Role.OFFICER.label == "Enforcement officer"
    for role in Role:
        assert role.label and role.label[0].isupper()


# --------------------------------------------------------------------------- #
# Sign-up role selection
# --------------------------------------------------------------------------- #
def _with_officer_code(code, fn):
    """Run *fn* with LABEL_JAANO_OFFICER_CODE set to *code* (None = unset)."""
    import os
    key = registration.OFFICER_CODE_ENV
    previous = os.environ.get(key)
    try:
        if code is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = code
        return fn()
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


def test_signup_defaults_to_consumer():
    assert _with_officer_code(None, lambda: registration.role_for_signup(None, None)) \
        is Role.CONSUMER
    assert _with_officer_code(None, lambda: registration.role_for_signup("consumer", None)) \
        is Role.CONSUMER


def test_officer_signup_requires_a_configured_code():
    def attempt():
        try:
            registration.role_for_signup("officer", "anything")
        except registration.OfficerCodeRejected:
            return "refused"
        return "allowed"
    assert _with_officer_code(None, attempt) == "refused", (
        "with no code configured, officer sign-up must be unavailable")
    assert _with_officer_code(None, lambda: registration.officer_code_configured()) is False


def test_officer_signup_accepts_the_right_code_only():
    def right():
        return registration.role_for_signup("officer", "let-me-in")

    def wrong():
        try:
            registration.role_for_signup("officer", "nope")
        except registration.OfficerCodeRejected:
            return "refused"
        return "allowed"

    assert _with_officer_code("let-me-in", right) is Role.OFFICER
    assert _with_officer_code("let-me-in", wrong) == "refused"
    assert _with_officer_code("let-me-in", lambda: registration.officer_code_configured()) is True


def test_signup_can_never_produce_an_admin():
    """Privilege must not be requestable. Even holding the officer code, and even if
    the code were reused as an 'admin code', sign-up must refuse."""
    def attempt(requested, code):
        def run():
            try:
                return registration.role_for_signup(requested, code)
            except registration.OfficerCodeRejected:
                return "refused"
        return _with_officer_code("let-me-in", run)

    for requested in ("admin", "ADMIN", " admin ", "administrator"):
        got = attempt(requested, "let-me-in")
        assert got != Role.ADMIN, f"sign-up granted admin for {requested!r}"
        assert got in ("refused", Role.CONSUMER), (
            f"{requested!r} produced {got!r}; expected a refusal or a plain consumer")


def test_secret_is_ephemeral_is_reported_honestly():
    """``/health`` and the sign-up screen surface this, so it must reflect reality."""
    assert isinstance(auth.secret_is_ephemeral(), bool)


# --------------------------------------------------------------------------- #
# Report share tickets — one key, two jobs, no crossover
# --------------------------------------------------------------------------- #
def _ticket(scan="scan-1", user="user-1", **kw):
    return tickets.mint_report_ticket(scan_id=scan, user_id=user, **kw)


def test_a_report_ticket_is_not_a_login():
    """The attack this whole design exists to stop.

    A share link is handed to browsers, pasted into chats, and left in history. If the
    same string also authenticated the API, forwarding a report would be handing over
    the account. ``auth.decode`` defaults to the API purpose, so every existing call
    site — including ``app.deps.optional_user`` — refuses it without having been
    changed.
    """
    try:
        auth.decode(_ticket())
    except tokens.TokenWrongPurpose:
        pass
    else:
        raise AssertionError("a report ticket was accepted as a session token")


def test_a_login_is_not_a_report_ticket():
    """And the converse, so the two are genuinely disjoint rather than one-way."""
    session = auth.encode({"sub": "user-1"})
    try:
        tickets.read_report_ticket(session, scan_id="scan-1")
    except tokens.TokenWrongPurpose:
        pass
    else:
        raise AssertionError("a session token was accepted as a report ticket")


def test_wrong_purpose_is_a_kind_of_invalid():
    """Subclassing matters: code written before purposes existed must still reject.

    Every ``except auth.TokenError`` and ``except auth.TokenInvalid`` handler in the
    codebase predates this claim. If ``TokenWrongPurpose`` sat outside that hierarchy,
    those handlers would let it through as an unhandled success path.
    """
    assert issubclass(tokens.TokenWrongPurpose, tokens.TokenInvalid)
    assert issubclass(tokens.TokenWrongPurpose, tokens.TokenError)


def test_a_ticket_is_bound_to_one_inspection():
    """The scan id is inside the signature, so a ticket cannot be re-aimed.

    Without this, a consumer with one legitimate ticket could walk the id space and
    read every report in the corpus.
    """
    ticket = _ticket(scan="scan-1", user="user-1")
    assert tickets.read_report_ticket(ticket, scan_id="scan-1")["sub"] == "user-1"
    for other in ("scan-2", "SCAN-1", "scan-1 ", "", "scan-10"):
        try:
            tickets.read_report_ticket(ticket, scan_id=other)
        except tokens.TokenInvalid:
            continue
        raise AssertionError(f"ticket for scan-1 was accepted for {other!r}")


def test_editing_a_ticket_breaks_it():
    """Belt and braces: the claim check above is only meaningful if the MAC holds."""
    ticket = _ticket(scan="scan-1")
    tampered = ticket[:-4] + ("AAAA" if not ticket.endswith("AAAA") else "BBBB")
    try:
        tickets.read_report_ticket(tampered, scan_id="scan-1")
    except tokens.TokenInvalid:
        pass
    else:
        raise AssertionError("a ticket with a broken signature was accepted")


def test_a_ticket_expires_in_minutes_not_hours():
    """A link's whole safety argument is that it is short-lived."""
    assert tickets.REPORT_TICKET_TTL_SECONDS <= 30 * 60, (
        "a share link that outlives the walk to a printer is a password"
    )
    stale = _ticket(ttl_seconds=-60)
    try:
        tickets.read_report_ticket(stale, scan_id="scan-1")
    except tokens.TokenExpired:
        pass
    else:
        raise AssertionError("an expired ticket was accepted")


def test_a_ticket_without_a_subject_is_rejected():
    """Attribution and revocation both key off the subject, so it must be present.

    Minted by hand rather than through :func:`mint_report_ticket`, because the point
    is that the *reader* validates rather than trusting whatever minted the string.
    """
    subjectless = auth.encode({"scan": "scan-1", tokens.PURPOSE_CLAIM: "report"})
    try:
        tickets.read_report_ticket(subjectless, scan_id="scan-1")
    except tokens.TokenInvalid:
        pass
    else:
        raise AssertionError("a ticket with no subject was accepted")


def test_encode_defaults_to_the_api_purpose():
    """So ordinary token minting cannot forget the claim and lock itself out."""
    claims = auth.decode(auth.encode({"sub": "u"}))
    assert claims[tokens.PURPOSE_CLAIM] == tokens.PURPOSE_API


def test_an_unrecognised_purpose_is_refused_everywhere():
    """Fail-closed: a purpose added later is rejected by call sites that predate it."""
    exotic = auth.encode({"sub": "u", tokens.PURPOSE_CLAIM: "some-future-thing"})
    for read in (lambda: auth.decode(exotic),
                 lambda: tickets.read_report_ticket(exotic, scan_id="scan-1")):
        try:
            read()
        except tokens.TokenWrongPurpose:
            continue
        raise AssertionError("an unknown purpose was honoured")


# --------------------------------------------------------------------------- #
# Self-contained runner (no pytest required)
# --------------------------------------------------------------------------- #
def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed, {len(tests)} total")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    print(f"Running auth tests (today = {date.today().isoformat()})\n")
    raise SystemExit(_run_all())
