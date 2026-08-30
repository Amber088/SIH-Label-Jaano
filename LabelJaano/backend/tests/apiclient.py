#!/usr/bin/env python3
"""
Shared HTTP fixtures for the API test modules.

Not a test file. It exists because four modules now drive the same app and each one
needs the same five things — a client, a fresh database, and an account at each of the
three roles — and a copy per module is how those quietly drift apart. The one that
matters is :func:`admin`: it promotes out-of-band on purpose, because no enrolment code
mints an administrator, and a local copy that took a shortcut would be testing a path
the real deployment does not have.

Importing this module has two side effects, in this order, and the order is the point:

1. ``store.configure(":memory:")`` runs *before* the app is imported. A test run that
   touched the real database file would leave rows the dev server then picks up, and
   demo history would be indistinguishable from test fixtures.
2. ``TestClient(app)`` is built without entering it as a context manager, so lifespan
   startup never fires. :func:`reset_db` therefore calls ``init_persistence`` itself
   rather than relying on it — a test that silently ran with no database would pass
   for the wrong reason.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
SAMPLES = BACKEND / "samples"
for _path in (str(BACKEND), str(Path(__file__).resolve().parent)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import store  # noqa: E402

store.configure(":memory:")

from fastapi.testclient import TestClient  # noqa: E402

from app import deps  # noqa: E402
from app import ratelimit  # noqa: E402
from app.main import app  # noqa: E402
from auth.registration import OFFICER_CODE_ENV  # noqa: E402

__all__ = [
    "BACKEND", "SAMPLES", "OFFICER_CODE", "PASSWORD", "client",
    "sample", "reset_db", "register", "headers", "auth_headers",
    "consumer", "officer", "admin", "scan", "scan_id",
    "fresh_rate_limits", "run_all",
]

client = TestClient(app)

OFFICER_CODE = "demo-enrolment-code"
PASSWORD = "password123"

#: Env vars that reconfigure the rate limiter. Cleared before each test so a value left
#: behind by one cannot change another's budget.
_RATELIMIT_ENV = (
    ratelimit.DISABLE_ENV,
    "LABEL_JAANO_RL_AUTH",
    "LABEL_JAANO_RL_VISION",
    "LABEL_JAANO_RL_DEFAULT",
)


def fresh_rate_limits() -> None:
    """Empty the rate-limit counters and restore the default budgets.

    Needed because the limiter's counters live in process memory and the whole suite
    shares one process and one client, so without this the tests spend each other's
    budget and start failing in whatever order pytest happened to pick. Disabling the
    limiter for tests would be the easier fix and the wrong one — the middleware would
    then never execute during a test run and its own failure modes would go untested.

    ``conftest`` calls this from an autouse fixture; the self-contained runners call it
    per test, so both paths behave the same.
    """
    for key in _RATELIMIT_ENV:
        os.environ.pop(key, None)
    ratelimit.configure()
    ratelimit.reset()


def sample(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


def reset_db() -> None:
    """Fresh in-memory database, persistence enabled, officer sign-up available."""
    os.environ.pop(deps.PERSISTENCE_DISABLED_ENV, None)
    os.environ[OFFICER_CODE_ENV] = OFFICER_CODE
    store.configure(":memory:")
    deps.init_persistence()
    assert deps.persistence_ready(), "test setup failed: no database"


def register(email: str, *, role: str | None = None,
             officer_code: str | None = None, name: str = "Test User") -> dict:
    body: dict = {"email": email, "password": PASSWORD, "name": name}
    if role is not None:
        body["role"] = role
    if officer_code is not None:
        body["officer_code"] = officer_code
    r = client.post("/auth/register", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def auth_headers(body: dict) -> dict:
    """Headers straight from a ``register``/``login`` payload."""
    return headers(body["access_token"])


def consumer(email: str = "consumer@test.in") -> dict:
    return register(email)


def officer(email: str = "officer@test.gov.in") -> dict:
    return register(email, role="officer", officer_code=OFFICER_CODE)


def admin(email: str = "admin@test.gov.in") -> dict:
    """An administrator, promoted out-of-band.

    Deliberately not done over HTTP: ``admin`` is not self-grantable by any enrolment
    code, so there is no endpoint that could mint one. ``manage.py role`` is the real
    path; this is the same store call it makes.
    """
    body = register(email)
    store.set_role(body["user"]["id"], "admin")
    # Re-login so the returned token carries the new role claim. Authorisation re-reads
    # the role per request either way, but the client-visible ``user`` block should not
    # still say "consumer".
    r = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["role"] == "admin"
    return r.json()


def scan(token: str, sample_name: str = "good_label.json", **params) -> dict:
    r = client.post("/scan", json=sample(sample_name), headers=headers(token),
                    params=params)
    assert r.status_code == 200, r.text
    return r.json()


def scan_id(token: str, sample_name: str = "good_label.json", **params) -> str:
    """File one inspection and return its id, asserting it was actually stored."""
    body = scan(token, sample_name, **params)
    assert body["saved"] is True and body["scan_id"], body
    return body["scan_id"]


def run_all(namespace: dict, *, title: str = "") -> int:
    """Run every ``test_*`` in *namespace*, one line each. Returns a process exit code.

    This is the "runs two ways" half that each module's docstring advertises, so a
    verdict is available on a machine without pytest. It applies
    :func:`fresh_rate_limits` per test, which is what the autouse fixture does under
    pytest: omit it and the auth bucket runs out partway through the run, after which
    every later login test fails with a 429 that has nothing to do with the behaviour
    it was checking.
    """
    tests = [v for k, v in sorted(namespace.items())
             if k.startswith("test_") and callable(v)]
    if title:
        print(f"Running {title}\n")
    passed = failed = 0
    for test in tests:
        fresh_rate_limits()
        try:
            test()
        except AssertionError as exc:
            print(f"  FAIL  {test.__name__}: {exc}")
            failed += 1
        except Exception as exc:  # noqa: BLE001 - a runner must report, not propagate
            print(f"  ERROR {test.__name__}: {type(exc).__name__}: {exc}")
            failed += 1
        else:
            print(f"  PASS  {test.__name__}")
            passed += 1
    print(f"\n{passed} passed, {failed} failed, {len(tests)} total")
    return 0 if failed == 0 else 1
