"""
HTTP routers, grouped by concern.

:mod:`auth_routes` owns accounts, :mod:`history` owns stored inspections and their
aggregates. The scanning endpoints stay in :mod:`app.main` because they are the
product — anything that judges a label should be visible in the file a reader opens
first, rather than buried a package deeper.

The module is named ``auth_routes`` and not ``auth`` on purpose: a sibling top-level
package is already called :mod:`auth`, and shadowing it here would make
``import auth`` resolve differently depending on where it was written from.
"""
from __future__ import annotations

from . import auth_routes, history

__all__ = ["auth_routes", "history"]
