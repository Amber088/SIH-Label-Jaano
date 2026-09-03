#!/usr/bin/env python3
"""
Tests for :mod:`envfile` — the ``backend/.env`` loader.

Runs two ways:
    pytest                          # from the backend/ directory
    python3 tests/test_envfile.py   # no pytest needed — self-contained runner

Every case here corresponds to a way this project has actually lost time. The
loader's job is not just "parse KEY=VALUE": it must refuse to overwrite a live
shell export, refuse to apply an unfilled placeholder over a good value, and
tolerate the ``<angle brackets>`` that come along when a key is copied out of
documentation. A loader that silently blanks ``GEMINI_API_KEY`` puts the service
into mock mode, and mock mode answers every request with a confident, meaningless
verdict — so these are correctness tests, not cosmetics.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from envfile import load_env_file  # noqa: E402

_SENTINEL = "LABEL_JAANO_TEST_VAR_DO_NOT_USE"
_SENTINEL2 = "LABEL_JAANO_TEST_VAR2_DO_NOT_USE"


def _write(body: str) -> Path:
    fd, name = tempfile.mkstemp(suffix=".env")
    os.close(fd)
    path = Path(name)
    path.write_text(body, encoding="utf-8")
    return path


def _clear() -> None:
    for name in (_SENTINEL, _SENTINEL2):
        os.environ.pop(name, None)


def test_missing_file_is_not_an_error():
    assert load_env_file(Path("/nonexistent/nowhere/.env")) == []


def test_plain_assignment_is_applied():
    _clear()
    path = _write(f"{_SENTINEL}=abc123\n")
    try:
        assert load_env_file(path) == [_SENTINEL]
        assert os.environ[_SENTINEL] == "abc123"
    finally:
        path.unlink()
        _clear()


def test_shell_value_wins_over_file():
    """A key exported at the command line must beat the file, every time."""
    _clear()
    os.environ[_SENTINEL] = "from-the-shell"
    path = _write(f"{_SENTINEL}=from-the-file\n")
    try:
        assert load_env_file(path) == []
        assert os.environ[_SENTINEL] == "from-the-shell"
    finally:
        path.unlink()
        _clear()


def test_blank_value_cannot_clobber_a_shell_export():
    """The exact failure of plain ``uvicorn --env-file .env`` with an unfilled file."""
    _clear()
    os.environ[_SENTINEL] = "the-real-key"
    path = _write(f"{_SENTINEL}=\n")
    try:
        load_env_file(path)
        assert os.environ[_SENTINEL] == "the-real-key"
    finally:
        path.unlink()
        _clear()


def test_placeholder_value_is_ignored():
    _clear()
    path = _write(f"{_SENTINEL}=changeme\n{_SENTINEL2}=AIza...\n")
    try:
        assert load_env_file(path) == []
        assert _SENTINEL not in os.environ
        assert _SENTINEL2 not in os.environ
    finally:
        path.unlink()
        _clear()


def test_angle_brackets_are_stripped():
    """Documentation writes ``<your-key>``; the brackets get pasted with it."""
    _clear()
    path = _write(f"{_SENTINEL}=<AIzaSyExample>\n")
    try:
        load_env_file(path)
        assert os.environ[_SENTINEL] == "AIzaSyExample"
    finally:
        path.unlink()
        _clear()


def test_quotes_and_export_prefix_are_handled():
    _clear()
    path = _write(f'export {_SENTINEL}="quoted value"\n')
    try:
        load_env_file(path)
        assert os.environ[_SENTINEL] == "quoted value"
    finally:
        path.unlink()
        _clear()


def test_comments_blanks_and_junk_lines_are_skipped():
    _clear()
    path = _write(
        "# a comment\n"
        "\n"
        "   \n"
        "NOT_AN_ASSIGNMENT\n"
        "bad-name!=x\n"
        f"{_SENTINEL}=ok\n"
    )
    try:
        assert load_env_file(path) == [_SENTINEL]
        assert os.environ[_SENTINEL] == "ok"
    finally:
        path.unlink()
        _clear()


def test_value_containing_equals_is_preserved():
    """Base64-ish secrets end in '='. Only the first '=' separates name from value."""
    _clear()
    path = _write(f"{_SENTINEL}=a=b==\n")
    try:
        load_env_file(path)
        assert os.environ[_SENTINEL] == "a=b=="
    finally:
        path.unlink()
        _clear()


def test_shipped_example_file_declares_the_real_variable_names():
    """``.env.example`` is the file people copy; drift here is a silent trap."""
    example = BACKEND / ".env.example"
    assert example.is_file(), ".env.example must be committed"
    text = example.read_text(encoding="utf-8")
    for name in ("GEMINI_API_KEY", "LABEL_JAANO_SECRET", "LABEL_JAANO_OFFICER_CODE"):
        assert name in text, f"{name} missing from .env.example"
    # The name the README used to document, which the code has never read.
    assert "LABEL_JAANO_GEMINI_API_KEY" not in text


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
    print("Running .env loader tests\n")
    raise SystemExit(_run_all())
