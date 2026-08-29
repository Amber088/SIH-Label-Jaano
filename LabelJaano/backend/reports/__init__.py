"""
Report rendering — turning a stored assessment into a document a person can file.

Currently one renderer: :func:`render_inspection_html`, a print-ready A4 HTML
inspection report. It is pure standard library, so it runs anywhere the rule engine
runs, and it is deliberately decoupled from persistence — it takes a report dict and
metadata keywords rather than a :class:`store.ScanRow`, so it can render a scan that
was never saved (and can be unit-tested without a database).

Everything the renderer interpolates is HTML-escaped. That is not incidental
tidiness: nearly every string in an inspection report was read off a photograph by
OCR or a vision model, and is therefore untrusted input.
"""
from __future__ import annotations

from .inspection_html import VERDICT_PRESENTATION, render_inspection_report

# Exported under a clearer name at package level; the module keeps the plain name.
render_inspection_html = render_inspection_report

__all__ = [
    "render_inspection_report",
    "render_inspection_html",
    "VERDICT_PRESENTATION",
]
