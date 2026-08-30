"""
Rate limiting — a sliding-window counter in front of the endpoints someone else can
make expensive.

Two endpoints justify this and the rest inherit a loose ceiling:

* ``POST /auth/login`` is an online password oracle. Without a limit, the only thing
  between a weak password and an officer account is PBKDF2's cost per guess, and the
  attacker gets to choose how many guesses per second. This is the reason the module
  exists.
* ``POST /scan/image`` and ``POST /extract`` each spend a paid Gemini call. An open
  vision endpoint is somebody else's API bill, and unlike a slow response that is not
  self-limiting — the requests cost money whether or not anyone waits for them.

Design notes
------------
**Sliding window, not fixed.** A fixed window lets 2N requests through across a
boundary — N at 11:59:59 and N at 12:00:00 — which for a login endpoint is exactly
double the guess rate you thought you allowed. A deque of timestamps per key is a few
bytes more and has no such edge.

**Keyed by account when there is one, by address otherwise.** An enforcement office
behind one NAT address must not share a single budget between its inspectors, so an
authenticated request is limited per account. The token is verified but *not* looked
up in the database: this runs on every request, including the ones that will turn out
to be anonymous, and a rate limiter that adds a query per request is its own denial of
service. An unsigned or expired token falls back to the address, which is right — a
caller with a broken token has not proved they are anybody.

**Fails open, and says so.** If the limiter itself breaks the request is allowed
through. A compliance checker that stops answering because its own bookkeeping is
confused has chosen the worse failure.

Configuration (all optional)::

    LABEL_JAANO_RATELIMIT=off       disable entirely
    LABEL_JAANO_RL_AUTH=20/60       N requests per S seconds on the auth bucket
    LABEL_JAANO_RL_VISION=30/60     ... on the image/extract bucket
    LABEL_JAANO_RL_DEFAULT=600/60   ... on everything else

The counters live in this process's memory, which is the honest scope of this
implementation: behind two uvicorn workers each gets its own allowance. That is a
factor-of-workers slack, not a hole — the budgets are set for a district office, not
tuned to a single request. A multi-process deployment that needs an exact global limit
wants Redis, and the seam for it is :class:`_Window`.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from collections import OrderedDict, deque
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

__all__ = ["RateLimitMiddleware", "reset", "configure", "enabled", "limits"]

DISABLE_ENV = "LABEL_JAANO_RATELIMIT"

#: bucket name -> "requests/seconds". Overridable per bucket by env var.
_DEFAULTS = {
    "auth": (20, 60),
    "vision": (30, 60),
    "default": (600, 60),
}
_ENV_KEYS = {
    "auth": "LABEL_JAANO_RL_AUTH",
    "vision": "LABEL_JAANO_RL_VISION",
    "default": "LABEL_JAANO_RL_DEFAULT",
}

#: Which bucket a request falls into. Matched on (method, path prefix); first hit wins,
#: so the specific vision paths are listed before the generic /scan.
_ROUTES: tuple[tuple[str, str, str], ...] = (
    ("POST", "/auth/login", "auth"),
    ("POST", "/auth/register", "auth"),
    ("POST", "/auth/refresh", "auth"),
    ("POST", "/scan/image", "vision"),
    ("POST", "/extract", "vision"),
)

#: Cap on tracked keys, so an attacker rotating source addresses cannot grow the table
#: without bound. Eviction is least-recently-seen, which is also the key most likely to
#: have an empty window anyway.
MAX_KEYS = 20_000

_lock = threading.RLock()
_windows: "OrderedDict[str, _Window]" = OrderedDict()
_limits: dict[str, tuple[int, int]] = {}


class _Window:
    """One key's request timestamps within the window. Not thread-safe on its own —
    every caller holds the module lock."""

    __slots__ = ("hits",)

    def __init__(self) -> None:
        self.hits: deque[float] = deque()

    def check(self, now: float, limit: int, span: int) -> tuple[bool, int, float]:
        """``(allowed, remaining, reset_at)``. Records the hit when allowed."""
        cutoff = now - span
        while self.hits and self.hits[0] <= cutoff:
            self.hits.popleft()
        if len(self.hits) >= limit:
            # The window frees up when its oldest hit ages out.
            return False, 0, self.hits[0] + span
        self.hits.append(now)
        return True, limit - len(self.hits), now + span


def _parse_spec(text: str, fallback: tuple[int, int]) -> tuple[int, int]:
    """``"20/60"`` -> ``(20, 60)``. A malformed value keeps the default and warns.

    Deliberately not fatal: a typo in an env var should not stop a compliance server
    from booting, but it must not silently become "unlimited" either.
    """
    try:
        count, _, span = text.partition("/")
        n, s = int(count.strip()), int((span or "60").strip())
        if n > 0 and s > 0:
            return n, s
        raise ValueError("must be positive")
    except (TypeError, ValueError) as exc:
        print(f"[label-jaano] WARNING: ignoring rate limit {text!r} ({exc}); "
              f"using {fallback[0]}/{fallback[1]}", file=sys.stderr)
        return fallback


def configure() -> dict[str, tuple[int, int]]:
    """(Re)read the limits from the environment. Called at import and by tests."""
    with _lock:
        _limits.clear()
        for bucket, default in _DEFAULTS.items():
            raw = os.environ.get(_ENV_KEYS[bucket], "").strip()
            _limits[bucket] = _parse_spec(raw, default) if raw else default
        return dict(_limits)


def limits() -> dict[str, tuple[int, int]]:
    return dict(_limits)


def enabled() -> bool:
    return os.environ.get(DISABLE_ENV, "").strip().lower() not in ("0", "off", "false", "no")


def reset() -> None:
    """Forget every counter. For tests, and for a deliberate operational clear."""
    with _lock:
        _windows.clear()


def _bucket_for(method: str, path: str) -> str:
    for m, prefix, bucket in _ROUTES:
        if method == m and path.startswith(prefix):
            return bucket
    return "default"


def _identity(request: Request) -> str:
    """``user:<id>`` when a validly signed token is presented, else ``ip:<addr>``.

    Signature and expiry are checked; the account is not. See the module docstring for
    why a database read per request is the wrong trade here.
    """
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
        if token:
            try:
                import auth
                claims = auth.decode(token)
                sub = claims.get("sub")
                if sub:
                    return f"user:{sub}"
            except Exception:  # noqa: BLE001 - any bad token just falls back to the ip
                pass
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return f"ip:{forwarded.split(',')[0].strip()}"
    return f"ip:{request.client.host if request.client else 'unknown'}"


def _consume(key: str, limit: int, span: int) -> tuple[bool, int, float]:
    with _lock:
        window = _windows.get(key)
        if window is None:
            if len(_windows) >= MAX_KEYS:
                _windows.popitem(last=False)  # least recently seen
            window = _Window()
        else:
            _windows.move_to_end(key)
        _windows[key] = window
        return window.check(time.monotonic(), limit, span)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Apply the per-bucket limits, and report the budget on every limited response."""

    async def dispatch(self, request: Request, call_next):
        if not enabled():
            return await call_next(request)
        try:
            bucket = _bucket_for(request.method, request.url.path)
            limit, span = _limits.get(bucket, _DEFAULTS["default"])
            key = f"{bucket}|{_identity(request)}"
            allowed, remaining, reset_at = _consume(key, limit, span)
        except Exception as exc:  # noqa: BLE001 - never let bookkeeping break a request
            print(f"[label-jaano] WARNING: rate limiter failed open ({exc})",
                  file=sys.stderr)
            return await call_next(request)

        retry_after = max(1, int(round(reset_at - time.monotonic())))
        if not allowed:
            detail = (
                "Too many requests. "
                + ("Wait before trying to sign in again — repeated failed attempts are "
                   "rate limited." if bucket == "auth" else
                   "This endpoint reads labels with a paid model and is rate limited."
                   if bucket == "vision" else
                   "Slow down and retry.")
                + f" Try again in {retry_after}s."
            )
            return JSONResponse(
                status_code=429,
                content={"detail": detail},
                headers=_headers(limit, 0, retry_after, retry_after=retry_after),
            )

        response: Response = await call_next(request)
        for name, value in _headers(limit, remaining, retry_after).items():
            response.headers[name] = value
        return response


def _headers(limit: int, remaining: int, reset_in: int, *,
             retry_after: Optional[int] = None) -> dict[str, str]:
    out = {
        "X-RateLimit-Limit": str(limit),
        "X-RateLimit-Remaining": str(max(0, remaining)),
        # Seconds-until-reset rather than an absolute epoch: the client is a phone whose
        # clock may be minutes off, and a delta needs no agreement about "now".
        "X-RateLimit-Reset": str(max(0, reset_in)),
    }
    if retry_after is not None:
        out["Retry-After"] = str(retry_after)
    return out


configure()
