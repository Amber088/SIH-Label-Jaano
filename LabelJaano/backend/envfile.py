"""Load ``backend/.env`` into the process environment at startup.

Why this exists
---------------
Every real-mode secret this project needs — ``GEMINI_API_KEY``,
``LABEL_JAANO_SECRET``, ``LABEL_JAANO_OFFICER_CODE`` — used to live only in the
shell that happened to launch ``uvicorn``. That made real mode non-portable: a
container started without ``-e``, a CI job, or a teammate's clone would silently
fall back to the mock pipeline and hand back a confident, meaningless verdict.
Reading a gitignored ``.env`` fixes that without putting a live credential into
a tracked file.

Deliberate behaviours, each one earned from a real mistake
---------------------------------------------------------
* **The shell always wins.** A variable already present in ``os.environ`` is
  never overwritten, so ``GEMINI_API_KEY=... uvicorn ...`` still beats the file.
* **Empty values are ignored.** ``GEMINI_API_KEY=`` in the file must not clobber
  a good value exported in the shell — that is exactly what plain
  ``uvicorn --env-file .env`` does, and it is a nasty way to lose an afternoon.
* **Surrounding angle brackets are stripped.** Docs write keys as ``<your-key>``
  and the brackets get pasted along with them, producing a key that is wrong in
  a way no error message explains.
* **No third-party import.** ``python-dotenv`` arrives only as a transitive
  dependency of ``uvicorn[standard]``, and the rest of this backend is stdlib by
  design; a 40-line parser is cheaper than a dependency that might not be there.
* **Names are logged, values never are.**
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

DEFAULT_ENV_PATH = Path(__file__).resolve().parent / ".env"

# Values that mean "this was never filled in" rather than "this is the value".
_PLACEHOLDERS = {
    "",
    "changeme",
    "your-key-here",
    "your-key",
    "paste-your-key-here",
    "aiza...",
    "aiza",
}


def _clean(raw: str) -> str:
    """Strip quoting, angle brackets and stray whitespace from one value."""
    value = raw.strip()
    # Matched quotes, e.g. KEY="abc" or KEY='abc'.
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1].strip()
    # The copy-paste trap: KEY=<abc>.
    while len(value) >= 2 and value.startswith("<") and value.endswith(">"):
        value = value[1:-1].strip()
    return value


def load_env_file(path: Optional[Path] = None) -> List[str]:
    """Apply ``path`` (default ``backend/.env``) to ``os.environ``.

    Returns the names of the variables actually set, in file order — never the
    values, so the return value is safe to log. A missing file is not an error;
    it just returns an empty list.
    """
    env_path = Path(path) if path is not None else DEFAULT_ENV_PATH
    if not env_path.is_file():
        return []

    applied: List[str] = []
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].lstrip()
        if "=" not in stripped:
            continue
        name, _, raw_value = stripped.partition("=")
        name = name.strip()
        if not name or not name.replace("_", "").isalnum():
            continue
        value = _clean(raw_value)
        if value.lower() in _PLACEHOLDERS:
            continue          # never clobber a live shell value with a stub
        if os.environ.get(name, "").strip():
            continue          # the shell wins
        os.environ[name] = value
        applied.append(name)

    return applied
