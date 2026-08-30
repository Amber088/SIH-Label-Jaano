"""
Logging setup for the Label Jaano API.

One place decides *how* the app talks about what it is doing, so every module can just
``logging.getLogger("labeljaano.<area>")`` and trust the format, level and destination
are already set. :func:`setup_logging` is called once at import of :mod:`app.main`; it
is idempotent, which matters because the test suite imports the app inside a single
process and must not stack a fresh handler on every import.

Two knobs, both environment variables:

* ``LABEL_JAANO_LOG_LEVEL`` — ``DEBUG`` | ``INFO`` | ``WARNING`` | ``ERROR`` (default
  ``INFO``). Raise it to ``DEBUG`` when hunting a bug; the default is quiet enough to
  leave on during a demo.
* ``LABEL_JAANO_LOG_FILE`` — if set, every line is *also* appended here, so a run leaves
  a record behind after the terminal has scrolled away. An unwritable path is reported
  once and then ignored: a logging misconfiguration must never take the API down.

The logger tree is rooted at ``labeljaano`` and has ``propagate = False`` with its own
handler, so its lines are emitted exactly once and can be raised, lowered or routed as a
unit — separately from uvicorn's own access log (the ``uvicorn.*`` loggers).

The pipeline layer (:mod:`pipeline.gemini`) deliberately does *not* import this module.
It only calls ``logging.getLogger("labeljaano.gemini")`` and emits — the library gets a
logger, the application owns the handlers. That keeps the pipeline independently
importable with no dependency back on the web app.
"""
from __future__ import annotations

import logging
import os
import time

ROOT = "labeljaano"
LEVEL_ENV = "LABEL_JAANO_LOG_LEVEL"
FILE_ENV = "LABEL_JAANO_LOG_FILE"

_FORMAT = "%(asctime)s %(levelname)-5s %(name)s  %(message)s"
_DATEFMT = "%H:%M:%S"

_configured = False


def _level() -> int:
    """The configured level, defaulting to INFO for an unset or unknown value."""
    name = os.environ.get(LEVEL_ENV, "").strip().upper()
    if not name:
        return logging.INFO
    resolved = logging.getLevelName(name)
    return resolved if isinstance(resolved, int) else logging.INFO


def setup_logging(force: bool = False) -> logging.Logger:
    """Configure the ``labeljaano`` logger tree once. Returns its root logger.

    Safe to call repeatedly: without *force* the second call is a no-op, so importing
    the app many times in one process does not duplicate handlers (which would print
    every line twice, then three times...).
    """
    global _configured
    logger = logging.getLogger(ROOT)
    if _configured and not force:
        return logger

    logger.setLevel(_level())
    logger.propagate = False  # our own handler emits; don't also bubble to the root
    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    stream = logging.StreamHandler()  # stderr, next to uvicorn's own lines
    stream.setFormatter(formatter)
    logger.addHandler(stream)

    path = os.environ.get(FILE_ENV, "").strip()
    if path:
        try:
            file_handler = logging.FileHandler(path, encoding="utf-8")
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except OSError as exc:
            # A bad log path is a config mistake, not a reason to refuse to serve.
            logger.warning("could not open %s=%r for logging: %s", FILE_ENV, path, exc)

    _configured = True
    return logger


def get_logger(area: str) -> logging.Logger:
    """A child logger, e.g. ``get_logger("scan")`` -> ``labeljaano.scan``."""
    return logging.getLogger(f"{ROOT}.{area}")


class RequestLogMiddleware:
    """Emit one line per HTTP request: method, path, status, milliseconds.

    A pure-ASGI middleware rather than Starlette's ``BaseHTTPMiddleware`` on purpose:
    ``BaseHTTPMiddleware`` wraps the response body in a way that can buffer or break a
    ``StreamingResponse`` (the ``/scans.csv`` export streams), whereas this only peeks at
    the ``http.response.start`` event to read the status code and times the round trip —
    the body bytes flow straight through untouched.

    Registered *outermost* (added last), so the time and status it reports are the ones
    the client actually sees, including a 429 written by the rate limiter and any headers
    added by CORS. A client-error status is logged at WARNING and a server-error at
    ERROR, so a scroll of the log makes real failures jump out from ordinary traffic.
    """

    def __init__(self, app):
        self.app = app
        self.log = logging.getLogger(f"{ROOT}.request")

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "?")
        path = scope.get("path", "?")
        started = time.perf_counter()
        status = 500  # if the app never sends a start event, treat it as a failure

        async def send_wrapper(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            # The access line for a crash. The traceback itself is left to the app's
            # exception handler (app.main), which has the request and logs it once with
            # exc_info — logging .exception() here too would print the same stack twice.
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.log.error("%s %s -> unhandled error after %.0fms",
                           method, path, elapsed_ms)
            raise

        elapsed_ms = (time.perf_counter() - started) * 1000
        level = logging.INFO
        if status >= 500:
            level = logging.ERROR
        elif status >= 400:
            level = logging.WARNING
        self.log.log(level, "%s %s -> %d  %.0fms", method, path, status, elapsed_ms)
