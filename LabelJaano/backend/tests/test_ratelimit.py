#!/usr/bin/env python3
"""
Tests for the rate limiter (:mod:`app.ratelimit`).

Runs two ways:
    pytest                           # from the backend/ directory
    python3 tests/test_ratelimit.py  # no pytest needed — self-contained runner

The limiter exists for one endpoint in particular: ``POST /auth/login`` is an online
password oracle, and without a ceiling the only thing between a weak password and an
officer account is PBKDF2's cost per guess — at a rate the attacker chooses. So the
tests are about the properties that make it a real defence rather than a decoration:

* ``test_the_login_oracle_closes`` — the budget actually stops guessing, and the refusal
  is a 429 rather than another password answer.
* ``test_locking_one_bucket_does_not_lock_the_others`` — throttling logins must not stop
  an inspector scanning. A limiter that takes the product down under attack has chosen
  the attacker's side.
* ``test_two_inspectors_behind_one_address_do_not_share_a_budget`` — an enforcement
  office is one NAT address, and per-address limiting would make one busy inspector
  throttle the room.
* ``test_the_window_slides_rather_than_resetting`` — a unit test on :class:`_Window`,
  because a fixed window lets 2N guesses through across its boundary and that difference
  is invisible from the outside without waiting a minute.
* ``test_a_throttled_browser_is_told_why`` — the 429 keeps its CORS headers. Without them
  a browser reports a network error and the user is told nothing.

Budgets are shrunk with the env vars rather than by sending twenty real requests: the
production numbers are set for a district office, and a test that spends them is slow
for no extra coverage. ``apiclient.fresh_rate_limits`` clears both the counters and
those env vars before every test, so nothing leaks between them.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import apiclient  # noqa: E402
from apiclient import PASSWORD, client, headers, reset_db  # noqa: E402
from app import ratelimit  # noqa: E402
from app.ratelimit import _Window  # noqa: E402

ORIGIN = {"Origin": "http://localhost:8080"}


def _budget(**buckets: str) -> None:
    """Shrink one or more buckets for this test, e.g. ``_budget(auth="2/60")``."""
    for bucket, spec in buckets.items():
        os.environ[ratelimit._ENV_KEYS[bucket]] = spec
    ratelimit.configure()
    ratelimit.reset()


def _bad_login(email: str = "nobody@test.in", **kw):
    return client.post("/auth/login", json={"email": email, "password": "wrong"}, **kw)


def _statuses(n: int, call) -> list[int]:
    return [call().status_code for _ in range(n)]


# --------------------------------------------------------------------------- #
# The reason the module exists
# --------------------------------------------------------------------------- #
def test_the_login_oracle_closes():
    """Guessing stops at the budget, and the refusal is not another password answer.

    The distinction matters: a 401 tells the attacker "keep going, that one was wrong",
    a 429 tells them the channel is closed. Asserted on a real account so the guesses are
    against a password that exists, which is the case the limiter is defending.
    """
    reset_db()
    apiclient.officer("cop@test.gov.in")
    _budget(auth="3/60")

    codes = _statuses(5, lambda: _bad_login("cop@test.gov.in"))
    assert codes == [401, 401, 401, 429, 429], codes

    body = _bad_login("cop@test.gov.in").json()
    assert "Too many requests" in body["detail"]
    assert "sign in" in body["detail"].lower(), body["detail"]

    # And the correct password does not buy a way past it — the budget is per identity,
    # not per outcome, or a valid-looking guess would reopen the oracle.
    r = client.post("/auth/login",
                    json={"email": "cop@test.gov.in", "password": PASSWORD})
    assert r.status_code == 429


def test_registration_shares_the_login_budget():
    """``/auth/register`` and ``/auth/refresh`` are on the same bucket as login.

    All three are cheap for the caller and expensive for the server — a PBKDF2 verify or
    derive each — and separate budgets would just move the load one path across.
    """
    reset_db()
    _budget(auth="2/60")
    assert _bad_login().status_code == 401
    r = client.post("/auth/register",
                    json={"email": "new@test.in", "password": PASSWORD, "name": "N"})
    assert r.status_code == 201, r.text
    assert _bad_login().status_code == 429
    # 429 rather than the 401 an unauthenticated refresh would otherwise get: the
    # limiter runs ahead of routing, so a closed bucket closes the path outright.
    assert client.post("/auth/refresh").status_code == 429


# --------------------------------------------------------------------------- #
# Buckets
# --------------------------------------------------------------------------- #
def test_locking_one_bucket_does_not_lock_the_others():
    """An exhausted login budget must not stop an inspector scanning a label.

    A limiter that takes the product down while it is under attack has chosen the
    attacker's side, so the buckets are asserted to be genuinely separate rather than one
    counter with three names.
    """
    reset_db()
    who = apiclient.officer()
    _budget(auth="1/60")
    assert _bad_login().status_code == 401
    assert _bad_login().status_code == 429

    scan = client.post("/scan", json=apiclient.sample("good_label.json"),
                       headers=headers(who["access_token"]))
    assert scan.status_code == 200, scan.text
    assert client.get("/scans", headers=headers(who["access_token"])).status_code == 200
    assert client.get("/health").status_code == 200


def test_the_paid_endpoints_have_their_own_smaller_budget():
    """``/scan/image`` and ``/extract`` share the vision bucket, apart from everything else.

    These two spend a paid model call each. Unlike a slow response that is self-limiting,
    the bill is incurred whether or not anyone waits for the answer — so they get a
    tighter ceiling than the general traffic, and it is theirs alone.

    The requests here are rejected at validation (no file attached) and still cost
    budget, which is deliberate: the limiter runs ahead of routing so that a flood of
    cheap malformed probes cannot be used to hunt for a path that skips the counter.
    """
    reset_db()
    who = apiclient.officer()
    _budget(vision="2/60")

    assert client.post("/extract").status_code == 422
    assert client.post("/scan/image").status_code == 422
    third = client.post("/extract")
    assert third.status_code == 429
    assert "paid model" in third.json()["detail"], third.json()

    # Neither the login path nor ordinary reads are affected.
    assert _bad_login().status_code == 401
    assert client.get("/scans", headers=headers(who["access_token"])).status_code == 200


def test_everything_else_inherits_one_loose_ceiling():
    """The default bucket is a backstop, not a per-route policy.

    It exists so an unlisted endpoint is never completely unbounded; the number is set
    for a district office rather than tuned per path, which is why it is loose.
    """
    reset_db()
    who = apiclient.officer()
    h = headers(who["access_token"])
    _budget(default="3/60")

    codes = _statuses(4, lambda: client.get("/scans", headers=h))
    assert codes == [200, 200, 200, 429], codes
    # A different path, same bucket, same spent budget.
    assert client.get("/stats", headers=h).status_code == 429
    assert ratelimit.limits()["auth"] == (20, 60), "the auth budget was not touched"


# --------------------------------------------------------------------------- #
# Whose budget is it
# --------------------------------------------------------------------------- #
def test_two_inspectors_behind_one_address_do_not_share_a_budget():
    """Signed-in requests are counted per account, not per address.

    An enforcement office is one NAT address. Counting by address there would mean the
    first inspector to work through their morning's scans throttles the whole room — the
    limiter would be rationing the wrong resource.
    """
    reset_db()
    a = headers(apiclient.consumer("a@test.in")["access_token"])
    b = headers(apiclient.consumer("b@test.in")["access_token"])
    _budget(default="2/60")

    assert _statuses(3, lambda: client.get("/scans", headers=a)) == [200, 200, 429]
    # Same client, same address, untouched budget.
    assert client.get("/scans", headers=b).status_code == 200
    assert client.get("/scans", headers=b).status_code == 200
    assert client.get("/scans", headers=b).status_code == 429


def test_a_broken_token_is_counted_as_a_stranger():
    """An unsigned or expired token falls back to the address.

    Which is the right reading of it: a caller whose token does not verify has not proved
    they are anybody, so they get the address's budget rather than a fresh one. Otherwise
    the per-account keying would itself be the bypass — invent a new junk token per
    request and every one looks like a new client.
    """
    reset_db()
    _budget(default="2/60")

    assert client.get("/health", headers=headers("garbage-one")).status_code == 200
    assert client.get("/health", headers=headers("garbage-two")).status_code == 200
    assert client.get("/health", headers=headers("garbage-three")).status_code == 429
    # An anonymous request from the same address is the same identity, so also refused.
    assert client.get("/health").status_code == 429


def test_the_forwarded_address_is_the_one_counted():
    """Behind a proxy, the client is the first hop of ``X-Forwarded-For``.

    Without this every request appears to come from the reverse proxy and the whole
    internet shares one budget — which is both a denial of service against real users and
    no defence at all against the attacker who caused it.
    """
    reset_db()
    _budget(default="1/60")

    one = {"X-Forwarded-For": "203.0.113.9, 10.0.0.1"}
    two = {"X-Forwarded-For": "203.0.113.10, 10.0.0.1"}
    assert client.get("/health", headers=one).status_code == 200
    assert client.get("/health", headers=one).status_code == 429
    # A different client through the same proxy still has its own allowance: the trailing
    # hops are ignored, so the shared 10.0.0.1 does not merge the two.
    assert client.get("/health", headers=two).status_code == 200
    assert client.get("/health", headers=two).status_code == 429


# --------------------------------------------------------------------------- #
# What the client is told
# --------------------------------------------------------------------------- #
def test_the_budget_is_reported_on_every_response():
    """``X-RateLimit-*`` on the way through, ``Retry-After`` on the refusal.

    A client that can see its remaining budget can back off before it is refused. The
    reset is a delta in seconds rather than an epoch on purpose: the client is a phone
    whose clock may be minutes out, and a delta needs no agreement about "now".
    """
    reset_db()
    _budget(default="3/60")

    remaining = []
    for _ in range(3):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.headers["X-RateLimit-Limit"] == "3"
        assert 0 < int(r.headers["X-RateLimit-Reset"]) <= 60
        assert "Retry-After" not in r.headers
        remaining.append(int(r.headers["X-RateLimit-Remaining"]))
    assert remaining == [2, 1, 0], remaining

    refused = client.get("/health")
    assert refused.status_code == 429
    assert refused.headers["X-RateLimit-Remaining"] == "0"
    assert 0 < int(refused.headers["Retry-After"]) <= 60
    assert refused.headers["Retry-After"] == refused.headers["X-RateLimit-Reset"]
    assert f"Try again in {refused.headers['Retry-After']}s" in refused.json()["detail"]


def test_a_throttled_browser_is_told_why():
    """The 429 keeps its CORS headers.

    This is why the limiter is registered *before* ``CORSMiddleware`` and therefore runs
    inside it. The 429 short-circuits the request, so an outermost limiter would return it
    without ever passing back out through CORS — the browser would then report a network
    error and the app could not show the "too many requests" message at all. The failure
    is silent and only reproducible once a real client is already being throttled, which
    is the worst time to discover it.
    """
    reset_db()
    _budget(default="1/60")

    ok = client.get("/health", headers=ORIGIN)
    assert ok.status_code == 200
    assert ok.headers["access-control-allow-origin"] == "*"

    refused = client.get("/health", headers=ORIGIN)
    assert refused.status_code == 429
    assert refused.headers.get("access-control-allow-origin") == "*", \
        "a throttled browser request would be reported as a network error"
    assert refused.headers["content-type"].startswith("application/json")

    # The preflight is handled by CORS before the limiter sees it, so asking permission
    # never spends the budget it is asking about.
    pre = client.options("/health", headers={**ORIGIN,
                                            "Access-Control-Request-Method": "GET"})
    assert pre.status_code == 200
    assert "GET" in pre.headers["access-control-allow-methods"]


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
def test_the_limiter_can_be_switched_off():
    """``LABEL_JAANO_RATELIMIT=off`` disables it entirely, headers and all.

    Needed for a load test or a demo, and read per request rather than at import so it can
    be flipped without a restart.
    """
    reset_db()
    _budget(default="1/60")
    assert client.get("/health").status_code == 200
    assert client.get("/health").status_code == 429

    for value in ("off", "0", "false", "no", "OFF"):
        os.environ[ratelimit.DISABLE_ENV] = value
        assert ratelimit.enabled() is False, value
        codes = _statuses(3, lambda: client.get("/health"))
        assert codes == [200, 200, 200], (value, codes)
        assert "X-RateLimit-Limit" not in client.get("/health").headers

    # Anything else means on — including the empty string, so an unset variable and a
    # blank one behave the same rather than one of them silently disabling the defence.
    for value in ("", "on", "1", "yes", "please"):
        os.environ[ratelimit.DISABLE_ENV] = value
        assert ratelimit.enabled() is True, value
    del os.environ[ratelimit.DISABLE_ENV]
    assert ratelimit.enabled() is True


def test_a_malformed_budget_keeps_the_default_rather_than_becoming_unlimited():
    """A typo in an env var must not stop the server booting, or remove the ceiling.

    Both failure modes are worse than the typo. Refusing to start takes a compliance
    server down over a formatting mistake; treating the unparseable value as "no limit"
    turns the same mistake into an open login oracle nobody is told about.
    """
    reset_db()
    for junk in ("banana", "", "0/60", "-5/60", "20/0", "20/-1", "/", "1.5/60",
                 "20/60/90", None):
        if junk is None:
            os.environ.pop(ratelimit._ENV_KEYS["auth"], None)
        else:
            os.environ[ratelimit._ENV_KEYS["auth"]] = junk
        assert ratelimit.configure()["auth"] == (20, 60), junk

    # A well-formed value is honoured, including a span other than a minute.
    os.environ[ratelimit._ENV_KEYS["auth"]] = " 5 / 30 "
    assert ratelimit.configure()["auth"] == (5, 30)
    os.environ[ratelimit._ENV_KEYS["auth"]] = "7"
    assert ratelimit.configure()["auth"] == (7, 60), "a bare count should mean per minute"


def test_the_defaults_are_the_documented_ones():
    """The numbers in the module docstring are the numbers in force.

    Cheap to assert and worth it: these are quoted in the deployment notes, and a limit
    that quietly differs from what an operator was told is a support call.
    """
    reset_db()
    limits = ratelimit.configure()
    assert limits == {"auth": (20, 60), "vision": (30, 60), "default": (600, 60)}
    assert limits["auth"][0] < limits["vision"][0] < limits["default"][0], \
        "the tightest budget should be on the password oracle"


# --------------------------------------------------------------------------- #
# The window itself — a unit test, because the difference needs a clock
# --------------------------------------------------------------------------- #
def test_the_window_slides_rather_than_resetting():
    """A fixed window would allow 2N requests across its boundary. This one does not.

    Driven with an explicit ``now`` rather than by sleeping: from the outside the two
    implementations are indistinguishable without waiting out a real minute, and the
    boundary is exactly where the difference lives. For a login endpoint a fixed window is
    double the guess rate the operator thought they had allowed.
    """
    w = _Window()
    limit, span = 3, 60

    for i in range(limit):
        allowed, remaining, _ = w.check(now=i, limit=limit, span=span)
        assert allowed is True, i
        assert remaining == limit - 1 - i

    allowed, remaining, reset_at = w.check(now=3, limit=limit, span=span)
    assert allowed is False and remaining == 0
    # Frees up when the *oldest* hit ages out, not on a wall-clock boundary.
    assert reset_at == 0 + span

    # One second before that hit expires: still refused.
    assert w.check(now=59.9, limit=limit, span=span)[0] is False
    # Just after: exactly one slot opens, because only one hit aged out.
    assert w.check(now=60.1, limit=limit, span=span)[0] is True
    assert w.check(now=60.2, limit=limit, span=span)[0] is False, \
        "more than one slot opened — that is a fixed window, not a sliding one"

    # A long quiet spell empties it completely rather than leaving stale hits behind.
    allowed, remaining, _ = w.check(now=1000, limit=limit, span=span)
    assert allowed is True and remaining == limit - 1
    assert len(w.hits) == 1, "expired timestamps were not discarded"


def test_a_refused_request_does_not_extend_the_penalty():
    """Hammering a closed window must not push its reset further out.

    The refused hit is not recorded, so a client that keeps retrying still gets in as soon
    as the original window expires. Recording it would turn a rate limit into an
    indefinite lockout that the client's own retry loop keeps renewing.
    """
    w = _Window()
    for i in range(2):
        assert w.check(now=i, limit=2, span=60)[0] is True
    for now in (2, 3, 4, 30, 59):
        allowed, _, reset_at = w.check(now=now, limit=2, span=60)
        assert allowed is False
        assert reset_at == 60, (now, reset_at)
    assert len(w.hits) == 2, "a refused request was counted"
    assert w.check(now=60.5, limit=2, span=60)[0] is True


# --------------------------------------------------------------------------- #
# The limiter's own failure modes
# --------------------------------------------------------------------------- #
def test_the_key_table_cannot_be_grown_without_bound():
    """Rotating source addresses evicts old keys instead of exhausting memory.

    Otherwise the defence is itself the attack: one request per forged
    ``X-Forwarded-For`` and the table grows a window per address until the process dies.
    Eviction is least-recently-seen, which is also the key most likely to have an empty
    window anyway, so the cap costs almost nothing in accuracy.
    """
    reset_db()
    _budget(default="5/60")
    real_max = ratelimit.MAX_KEYS
    ratelimit.MAX_KEYS = 8
    try:
        for n in range(40):
            r = client.get("/health", headers={"X-Forwarded-For": f"198.51.100.{n}"})
            assert r.status_code == 200, n
            assert len(ratelimit._windows) <= 8, (n, len(ratelimit._windows))
        assert len(ratelimit._windows) == 8
        # The survivors are the most recent, so an active client is not evicted by a
        # flood of one-shot strangers.
        assert "default|ip:198.51.100.39" in ratelimit._windows
        assert "default|ip:198.51.100.0" not in ratelimit._windows
    finally:
        ratelimit.MAX_KEYS = real_max


def test_the_limiter_fails_open():
    """If the bookkeeping breaks, the request goes through.

    A compliance checker that stops answering because its own rate limiter is confused has
    chosen the worse failure: the limiter protects a login endpoint and a billing account,
    neither of which is more important than the server working.
    """
    reset_db()
    who = apiclient.officer()
    _budget(default="1/60")
    real_consume = ratelimit._consume

    def broken(*_args, **_kwargs):
        raise RuntimeError("counter table is on fire")

    ratelimit._consume = broken
    try:
        for _ in range(4):
            r = client.get("/scans", headers=headers(who["access_token"]))
            assert r.status_code == 200, r.text
            # No budget headers, because there is no budget to report — the honest
            # answer rather than a made-up one.
            assert "X-RateLimit-Limit" not in r.headers
    finally:
        ratelimit._consume = real_consume

    # And it starts limiting again once the fault clears, rather than staying open.
    assert client.get("/scans", headers=headers(who["access_token"])).status_code == 200
    assert client.get("/scans", headers=headers(who["access_token"])).status_code == 429


def test_reset_forgets_every_counter():
    """``reset()`` is what makes this suite order-independent, so it is asserted too.

    The counters live in process memory and the whole suite shares one process; without
    this the tests would spend each other's budgets and fail in whatever order pytest
    happened to pick. Disabling the limiter for tests would be the easier fix and the
    wrong one — the middleware would then never run during a test and its own failure
    modes would go untested.
    """
    reset_db()
    _budget(default="1/60")
    assert client.get("/health").status_code == 200
    assert client.get("/health").status_code == 429
    assert ratelimit._windows, "nothing was counted at all"
    ratelimit.reset()
    assert not ratelimit._windows
    assert client.get("/health").status_code == 200


if __name__ == "__main__":
    raise SystemExit(apiclient.run_all(globals(), title="rate limit tests"))
