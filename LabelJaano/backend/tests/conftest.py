"""
Shared test setup.

One autouse fixture, and it is worth explaining why it is here rather than in a
helper each test module calls.

The rate limiter (:mod:`app.ratelimit`) keeps its counters in process memory, and the
whole suite shares one process and one ``TestClient`` — so without a reset the tests
would spend each other's budget and start failing in whatever order pytest happened to
pick. The tempting fix is to disable the limiter for tests, but then the middleware
would never execute during a test run and its own failure modes would be untested.
Clearing the counters between tests keeps it live and keeps the suite order-independent.

The reset itself lives in :func:`tests.apiclient.fresh_rate_limits` rather than here,
because the self-contained runners (``python3 tests/test_api.py``) have no conftest and
need the same guarantee. One definition, two callers.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _TESTS_DIR.parent
for _path in (str(_BACKEND_DIR), str(_TESTS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from apiclient import fresh_rate_limits  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    """Every test starts with an empty rate-limit table and the default budgets."""
    fresh_rate_limits()
    yield
    fresh_rate_limits()
