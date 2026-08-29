"""
Inspection report renderer — a print-ready HTML document, built with stdlib only.

Why HTML and not a PDF library
------------------------------
An inspector needs a document they can file, email, and put in front of a shop
owner. HTML with a proper ``@page`` print stylesheet gets there without adding a
dependency: any browser's "Save as PDF" turns this into an A4 PDF with repeating
table headers and no orphaned rows. WeasyPrint would produce the PDF server-side
and is a clean upgrade later — :func:`render_inspection_report` returns a complete
standalone document, so pointing WeasyPrint at that same string is the whole change.

Security note — this file escapes everything, deliberately
----------------------------------------------------------
Almost every string rendered here originated as text read off a *photograph* by
OCR or a vision model, or was typed by a user into a note field. A label carrying
``<script>`` in its ingredients list, or an officer pasting markup into a note,
must never become live markup in a report that gets emailed around. So every
interpolated value goes through :func:`_e` (``html.escape`` with quotes), and the
only unescaped strings in the output are the literal template and the stylesheet.
There is no "trusted" input class in this module.
"""
from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

__all__ = ["render_inspection_report", "VERDICT_PRESENTATION"]


# --------------------------------------------------------------------------- #
# Presentation tables
# --------------------------------------------------------------------------- #
# Verdict -> (heading, one-line gloss, accent colour, tint). Kept in one place so
# the printed report and the Flutter UI can be reconciled by eye.
VERDICT_PRESENTATION: dict[str, tuple[str, str, str, str]] = {
    "compliant": (
        "Compliant",
        "All mandatory declarations verified present and well-formed.",
        "#15803d",
        "#f0fdf4",
    ),
    "needs_review": (
        "Needs review",
        "Major declaration gaps found. Manual verification recommended.",
        "#b45309",
        "#fffbeb",
    ),
    "non_compliant": (
        "Non-compliant",
        "Critical mandatory declarations are missing or invalid.",
        "#b91c1c",
        "#fef2f2",
    ),
    "no_label_detected": (
        "No label detected",
        "The image did not contain a readable packaged-commodity label.",
        "#57534e",
        "#fafaf9",
    ),
}

_SEVERITY_ORDER = {"critical": 0, "major": 1, "minor": 2}
_SEVERITY_LABEL = {"critical": "Critical", "major": "Major", "minor": "Minor"}
_SEVERITY_COLOUR = {"critical": "#b91c1c", "major": "#b45309", "minor": "#57534e"}
_OUTCOME_LABEL = {"pass": "Pass", "fail": "Fail", "skip": "Not assessed"}
_OUTCOME_COLOUR = {"pass": "#15803d", "fail": "#b91c1c", "skip": "#a8a29e"}


# --------------------------------------------------------------------------- #
# Escaping + small formatting helpers
# --------------------------------------------------------------------------- #
def _e(value: Any) -> str:
    """Escape any value for HTML text or attribute context. The only way out."""
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def _titleise(text: Any) -> str:
    """``packaged_food`` -> ``Packaged food``, **escaped** and ready to interpolate.

    Only for *commodity categories*: a missing one is meaningfully "Uncategorised".
    Verdicts use :func:`_humanise`, which has no such special case — see there.
    """
    raw = str(text or "").strip()
    if not raw or raw == "unknown":
        return "Uncategorised"
    return _e(_humanise(raw)) or "Uncategorised"


def _humanise(text: Any) -> str:
    """``no_label_detected`` -> ``No label detected``. **Not** escaped; "" if empty.

    Two deliberate differences from :func:`_titleise`. It has no "unknown" special
    case, because routing a verdict through that one would print an unrecognised
    verdict as "Uncategorised" — a statement about the *commodity category* that tells
    the officer nothing about the finding. And it returns raw text, because its one
    caller hands the result to a template slot that escapes; escaping here too would
    double-escape and print a literal ``&lt;`` at the top of the report.
    """
    words = str(text or "").strip().replace("_", " ").split()
    if not words:
        return ""
    return " ".join([words[0].capitalize()] + words[1:])


def _fmt_timestamp(value: Optional[str]) -> str:
    """Render an ISO-8601 string as ``27 August 2026, 14:32 UTC``.

    Falls back to the raw (escaped) string when it will not parse — a report must
    still render if a timestamp is odd.
    """
    if not value:
        return "—"
    text = str(value)
    try:
        cleaned = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc)
        return _e(f"{dt.day} {dt:%B %Y}, {dt:%H:%M} UTC")
    except (ValueError, TypeError):
        return _e(text)


def _fmt_score(value: Any) -> str:
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return "—"


def _sev_key(item: dict) -> tuple[int, str]:
    sev = str(item.get("severity") or "").lower()
    return (_SEVERITY_ORDER.get(sev, 3), str(item.get("declaration_id") or ""))


def _as_dict(value: Any) -> dict:
    """*value* if it is a dict, else ``{}``.

    ``value or {}`` is not enough and the difference bites. It guards against ``None``
    and against an empty dict, but a value of the *wrong type* sails through — a
    ``summary`` that arrived as a string is truthy, so the next ``.get()`` raises
    ``AttributeError`` and the whole report fails to render. Reports come out of a
    ``report_json`` column that may have been written by an older build of the engine,
    so wrong-typed fields are a real case, and this module's contract is that a
    missing or malformed field becomes an em dash rather than an exception.
    """
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list:
    """*value* if it is a list or tuple, else ``[]``.

    Note that a bare string is deliberately *not* accepted even though it is iterable:
    iterating one yields characters, so a ``packs_applied`` of ``"legal_metrology"``
    would render as fifteen separate regulations.
    """
    return list(value) if isinstance(value, (list, tuple)) else []


# --------------------------------------------------------------------------- #
# Stylesheet
# --------------------------------------------------------------------------- #
# A4 because this is an Indian statutory context. The print rules matter as much
# as the screen ones: `display: table-header-group` repeats table headings on every
# printed page, and `break-inside: avoid` stops a finding being split across a page
# break — both are what make the saved PDF look like a filed document.
_STYLES = """
:root { color-scheme: light; }
@page { size: A4; margin: 16mm 14mm; }

* { box-sizing: border-box; }
body {
  margin: 0; padding: 28px 30px 40px;
  background: #ffffff; color: #1c1917;
  font: 10.5pt/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  max-width: 210mm; margin-inline: auto;
  -webkit-print-color-adjust: exact; print-color-adjust: exact;
}
h1, h2, h3, .masthead-title { font-family: Georgia, "Times New Roman", serif; font-weight: 600; }

/* ---- letterhead ---- */
.masthead { border-bottom: 2.5px solid #1c1917; padding-bottom: 10px; margin-bottom: 4px; }
.masthead-row { display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; }
.masthead-title { font-size: 19pt; letter-spacing: -0.2px; margin: 0; }
.masthead-sub { font-size: 8.5pt; color: #57534e; margin: 3px 0 0; max-width: 118mm; }
.masthead-ref { text-align: right; font-size: 8pt; color: #57534e; white-space: nowrap; }
.masthead-ref b { display: block; font-size: 10pt; color: #1c1917; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.rule-thin { height: 1px; background: #1c1917; opacity: .28; margin-bottom: 18px; }

/* ---- provenance warning ---- */
.provenance {
  border: 1.5px solid #b45309; background: #fffbeb; color: #7c2d12;
  padding: 9px 12px; margin: 0 0 18px; font-size: 9pt; border-radius: 3px;
}
.provenance b { font-size: 9.5pt; }

/* ---- verdict ---- */
.verdict { display: flex; gap: 0; border: 1px solid #d6d3d1; border-radius: 4px; overflow: hidden; margin-bottom: 18px; }
.verdict-main { flex: 1 1 auto; padding: 14px 16px; }
.verdict-name { font-size: 16pt; font-family: Georgia, serif; font-weight: 600; margin: 0 0 2px; }
.verdict-gloss { font-size: 9pt; color: #44403c; margin: 0; }
.verdict-score { flex: 0 0 118px; border-left: 1px solid #d6d3d1; padding: 14px 10px; text-align: center; }
.score-value { font-size: 25pt; font-family: Georgia, serif; font-weight: 600; line-height: 1; }
.score-max { font-size: 9pt; color: #78716c; }
.score-caption { font-size: 7.5pt; text-transform: uppercase; letter-spacing: .09em; color: #78716c; margin-top: 5px; }

/* ---- tallies ---- */
.tally { display: flex; gap: 8px; margin-bottom: 20px; flex-wrap: wrap; }
.tally-cell { flex: 1 1 0; min-width: 82px; border: 1px solid #e7e5e4; border-radius: 3px; padding: 8px 10px; }
.tally-n { font-size: 15pt; font-family: Georgia, serif; font-weight: 600; line-height: 1.1; }
.tally-k { font-size: 7.5pt; text-transform: uppercase; letter-spacing: .07em; color: #78716c; }

/* ---- meta grid ---- */
.meta { width: 100%; border-collapse: collapse; margin-bottom: 22px; font-size: 9.5pt; }
.meta th, .meta td { text-align: left; vertical-align: top; padding: 5px 8px; border-bottom: 1px solid #f5f5f4; }
.meta th { width: 33%; font-weight: 600; color: #57534e; font-size: 8.5pt;
           text-transform: uppercase; letter-spacing: .05em; }
.meta td { color: #1c1917; }

/* ---- sections ---- */
section { margin-bottom: 24px; break-inside: auto; }
h2 { font-size: 12.5pt; margin: 0 0 3px; padding-bottom: 5px; border-bottom: 1.5px solid #1c1917; }
.section-note { font-size: 8.5pt; color: #57534e; margin: 6px 0 11px; }

/* ---- data tables ---- */
table.data { width: 100%; border-collapse: collapse; font-size: 9pt; }
table.data thead { display: table-header-group; }          /* repeat header when printed */
table.data tr { break-inside: avoid; page-break-inside: avoid; }
table.data th {
  text-align: left; font-size: 7.5pt; text-transform: uppercase; letter-spacing: .06em;
  color: #57534e; border-bottom: 1px solid #a8a29e; padding: 6px 7px; font-weight: 600;
}
table.data td { padding: 7px; border-bottom: 1px solid #f5f5f4; vertical-align: top; }
table.data td.n { width: 22px; color: #a8a29e; font-variant-numeric: tabular-nums; }
.decl { font-weight: 600; }
.decl-id { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 8pt; color: #78716c; }
.cite { font-size: 8.5pt; color: #44403c; }
.observation { color: #292524; }
.detail { color: #78716c; font-size: 8.5pt; display: block; margin-top: 2px; }
.pill {
  display: inline-block; padding: 1px 7px; border-radius: 9px; font-size: 7.5pt;
  font-weight: 600; text-transform: uppercase; letter-spacing: .05em;
  border: 1px solid currentColor; white-space: nowrap;
}
.empty { padding: 14px; background: #fafaf9; border: 1px dashed #d6d3d1; border-radius: 3px;
         color: #57534e; font-size: 9pt; text-align: center; }

/* ---- signature ---- */
.sign { display: flex; gap: 26px; margin-top: 30px; break-inside: avoid; }
.sign-box { flex: 1 1 0; }
.sign-line { border-bottom: 1px solid #1c1917; height: 34px; }
.sign-label { font-size: 8pt; color: #57534e; margin-top: 5px; text-transform: uppercase; letter-spacing: .06em; }

footer { margin-top: 26px; border-top: 1px solid #d6d3d1; padding-top: 10px;
         font-size: 8pt; color: #78716c; }
footer p { margin: 0 0 4px; }

@media print {
  body { padding: 0; max-width: none; }
  .no-print { display: none !important; }
  a { color: inherit; text-decoration: none; }
}
"""

_PRINT_BAR = """
<div class="no-print" style="max-width:210mm;margin:0 auto 14px;padding:10px 12px;
     background:#f5f5f4;border:1px solid #d6d3d1;border-radius:4px;font-size:9.5pt;
     display:flex;justify-content:space-between;align-items:center;gap:12px;">
  <span style="color:#44403c;">Use your browser's <b>Print &rarr; Save as PDF</b> to file this report.</span>
  <button onclick="window.print()" style="font:inherit;padding:6px 14px;border-radius:4px;
          border:1px solid #1c1917;background:#1c1917;color:#fff;cursor:pointer;">Print</button>
</div>
"""


# --------------------------------------------------------------------------- #
# Section builders
# --------------------------------------------------------------------------- #
def _findings_section(violations: list[dict]) -> str:
    if not violations:
        return (
            '<section><h2>Findings</h2>'
            '<p class="section-note">Contraventions identified during automated '
            'assessment, most severe first.</p>'
            '<div class="empty">No contraventions were identified in the assessed '
            'declarations.</div></section>'
        )

    rows: list[str] = []
    for i, v in enumerate(sorted(violations, key=_sev_key), start=1):
        sev = str(v.get("severity") or "").lower()
        colour = _SEVERITY_COLOUR.get(sev, "#57534e")
        detail = v.get("detail")
        rows.append(
            f'<tr>'
            f'<td class="n">{i}</td>'
            f'<td><span class="pill" style="color:{colour}">'
            f'{_e(_SEVERITY_LABEL.get(sev, sev.title() or "—"))}</span></td>'
            f'<td><span class="decl">{_e(v.get("declaration_label"))}</span><br>'
            f'<span class="decl-id">{_e(v.get("declaration_id"))}</span></td>'
            f'<td class="cite">{_e(v.get("legal_reference"))}</td>'
            f'<td class="observation">{_e(v.get("message"))}'
            + (f'<span class="detail">{_e(detail)}</span>' if detail else "")
            + '</td></tr>'
        )

    return (
        '<section><h2>Findings</h2>'
        '<p class="section-note">Contraventions identified during automated '
        'assessment, most severe first. Each cites the provision it breaches.</p>'
        '<table class="data"><thead><tr>'
        '<th></th><th>Severity</th><th>Declaration</th>'
        '<th>Legal reference</th><th>Observation</th>'
        '</tr></thead><tbody>' + "".join(rows) + '</tbody></table></section>'
    )


def _reference_standards_section(standards: list[dict]) -> str:
    """Tier-2 provisions. The wording here matters: these are *not* findings."""
    if not standards:
        return ""
    rows = "".join(
        f'<tr><td class="n">{i}</td>'
        f'<td><span class="decl">{_e(s.get("label"))}</span><br>'
        f'<span class="decl-id">{_e(s.get("id"))}</span></td>'
        f'<td class="cite">{_e(s.get("legal_reference"))}'
        + (
            f'<span class="detail">{_e(s.get("authority"))}</span>'
            if s.get("authority")
            else ""
        )
        + '</td>'
        f'<td class="observation">{_e(s.get("note")) or "Verify by laboratory analysis."}'
        '</td></tr>'
        for i, s in enumerate(standards, start=1)
    )
    return (
        '<section><h2>Applicable standards requiring laboratory verification</h2>'
        '<p class="section-note">These provisions apply to this product but cannot be '
        'assessed from a photograph &mdash; they concern composition, additive limits, '
        'contaminant caps or other laboratory parameters. They are listed for '
        'follow-up and are <b>not</b> findings: they did not affect the verdict or '
        'the compliance score.</p>'
        '<table class="data"><thead><tr>'
        '<th></th><th>Standard</th><th>Legal reference</th><th>Action</th>'
        '</tr></thead><tbody>' + rows + '</tbody></table></section>'
    )


def _appendix_section(results: list[dict]) -> str:
    """Every check, including passes and skips — the audit trail.

    One wrinkle worth the code it costs. A declaration may carry several checks of
    the *same* type: MRP has two ``format`` checks, one for the price pattern and one
    for the "inclusive of all taxes" wording. Labelling both simply ``format`` makes
    a Pass row and a Fail row on the same declaration look like the tool
    contradicting itself, which is exactly the thing an opposing party would seize
    on. The pack's ``message`` cannot disambiguate them either, because it is phrased
    as the failure ("MRP is missing."), so on a passing row it would read as an
    accusation. So repeated types are numbered — ``format (1 of 2)`` — which makes it
    plain that these are two distinct requirements rather than one unstable one.
    """
    if not results:
        return ""

    # Count each declaration+type pair first so we know whether to number it at all.
    totals: dict[tuple[str, str], int] = {}
    for r in results:
        key = (str(r.get("declaration_id") or ""), str(r.get("check_type") or ""))
        totals[key] = totals.get(key, 0) + 1

    seen: dict[tuple[str, str], int] = {}
    rows: list[str] = []
    for r in results:
        key = (str(r.get("declaration_id") or ""), str(r.get("check_type") or ""))
        seen[key] = seen.get(key, 0) + 1
        check_label = _e(r.get("check_type"))
        if totals[key] > 1:
            check_label += f" ({seen[key]} of {totals[key]})"

        outcome = str(r.get("outcome") or "").lower()
        colour = _OUTCOME_COLOUR.get(outcome, "#a8a29e")
        evidence = r.get("detail") or ""
        # The pack message states what was breached, so it belongs on failures only.
        requirement = r.get("message") if outcome in ("fail", "skip") else None
        rows.append(
            f'<tr>'
            f'<td><span class="pill" style="color:{colour}">'
            f'{_e(_OUTCOME_LABEL.get(outcome, outcome.title() or "—"))}</span></td>'
            f'<td><span class="decl">{_e(r.get("declaration_label"))}</span><br>'
            f'<span class="decl-id">{_e(r.get("declaration_id"))}'
            f' &middot; {check_label}</span></td>'
            f'<td class="cite">{_e(r.get("legal_reference"))}</td>'
            f'<td class="observation">'
            + (_e(requirement) if requirement else "")
            + (
                f'<span class="detail">{_e(evidence)}</span>'
                if requirement and evidence
                else _e(evidence)
            )
            + '</td></tr>'
        )
    return (
        '<section><h2>Appendix &mdash; full assessment log</h2>'
        '<p class="section-note">Every check the engine ran, including those that '
        'passed and those it could not assess. &ldquo;Not assessed&rdquo; means the '
        'required evidence was absent from the image (for example, no measurable '
        'glyph height), not that the requirement was met or waived; such checks are '
        'excluded from the score rather than counted against the product.</p>'
        '<table class="data"><thead><tr>'
        '<th>Outcome</th><th>Declaration / check</th>'
        '<th>Legal reference</th><th>Requirement &amp; evidence</th>'
        '</tr></thead><tbody>' + "".join(rows) + '</tbody></table></section>'
    )


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def render_inspection_report(
    report: dict,
    *,
    scan_id: str,
    created_at: Optional[str] = None,
    product_name: Optional[str] = None,
    note: Optional[str] = None,
    location: Optional[str] = None,
    source: str = "json",
    mock: bool = False,
    inspector: Optional[dict] = None,
    generated_at: Optional[str] = None,
    include_appendix: bool = True,
    include_print_bar: bool = True,
) -> str:
    """Render one saved scan as a complete, standalone, print-ready HTML document.

    *report* is the engine's report dict exactly as stored. Everything else is the
    surrounding chain-of-custody metadata. Only ``scan_id`` is required — any
    missing field renders as an em dash rather than raising, because a report that
    will not render is worse than one with a gap in it.
    """
    report = report if isinstance(report, dict) else {}
    # Coerced because it is interpolated *and* sliced below; a caller passing a UUID
    # object or an int would otherwise fail on the slice rather than render.
    scan_id = str(scan_id or "")
    verdict = str(report.get("verdict") or "unknown")
    name, gloss, accent, tint = VERDICT_PRESENTATION.get(
        verdict,
        (
            _humanise(verdict) or "Unknown",
            "This verdict was not recognised by the report renderer. Treat the score "
            "and findings below as provisional and check the engine version.",
            "#57534e",
            "#fafaf9",
        ),
    )

    summary = _as_dict(report.get("summary"))
    severity = _as_dict(summary.get("violations_by_severity"))
    violations = [v for v in _as_list(report.get("violations")) if isinstance(v, dict)]
    results = [r for r in _as_list(report.get("results")) if isinstance(r, dict)]
    standards = [s for s in _as_list(report.get("reference_standards"))
                 if isinstance(s, dict)]
    packs: Iterable[Any] = _as_list(report.get("packs_applied"))

    def n(key: str) -> str:
        value = summary.get(key)
        return _e(value if isinstance(value, (int, float)) else 0)

    # --- provenance. A mock report must never be mistaken for a real finding. --
    provenance = ""
    if mock:
        provenance = (
            '<div class="provenance"><b>Demonstration output &mdash; not an '
            'enforceable finding.</b><br>This assessment was produced by the offline '
            'mock extraction pipeline, not by a live optical read of the photograph. '
            'The rule evaluation below is genuine, but the label values it judged were '
            'synthetic. Re-run with the live pipeline before relying on any finding.</div>'
        )

    source_label = {
        "image": "Photograph, live extraction pipeline",
        "json": "Pre-normalised scan input",
    }.get(str(source), _e(source))
    if mock:
        source_label = "Photograph, offline mock pipeline (demonstration)"

    pack_text = ", ".join(_e(p) for p in packs) if packs else "—"

    insp = _as_dict(inspector)
    inspector_line = "—"
    if insp.get("name") or insp.get("email"):
        who = _e(insp.get("name") or insp.get("email"))
        role = insp.get("role_label") or insp.get("role")
        inspector_line = f"{who}" + (f" &middot; {_e(role)}" if role else "")
        if insp.get("name") and insp.get("email"):
            inspector_line += f'<span class="detail">{_e(insp["email"])}</span>'

    meta_rows = [
        ("Report reference", f'<span class="decl-id">{_e(scan_id)}</span>'),
        ("Date of assessment", _fmt_timestamp(created_at)),
        ("Product", _e(product_name) or "Not recorded"),
        ("Commodity category", _titleise(report.get("category"))),
        ("Place of inspection", _e(location) or "Not recorded"),
        ("Assessed by", inspector_line),
        ("Evidence source", source_label),
        ("Regulations applied", f'<span class="cite">{pack_text}</span>'),
    ]
    if note:
        meta_rows.append(("Officer's note", _e(note)))
    meta_html = "".join(
        f"<tr><th>{label}</th><td>{value}</td></tr>" for label, value in meta_rows
    )

    tallies = [
        ("Checks run", n("checks_total"), "#1c1917"),
        ("Passed", n("passed"), "#15803d"),
        ("Failed", n("failed"), "#b91c1c"),
        ("Not assessed", n("skipped"), "#a8a29e"),
        ("Critical", _e(severity.get("critical") or 0), "#b91c1c"),
        ("Major", _e(severity.get("major") or 0), "#b45309"),
        ("Minor", _e(severity.get("minor") or 0), "#57534e"),
    ]
    tally_html = "".join(
        f'<div class="tally-cell"><div class="tally-n" style="color:{colour}">{value}</div>'
        f'<div class="tally-k">{label}</div></div>'
        for label, value, colour in tallies
    )

    generated = _fmt_timestamp(
        generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Inspection report {_e(scan_id)} &mdash; Label Jaano</title>
<style>{_STYLES}</style>
</head>
<body>
{_PRINT_BAR if include_print_bar else ""}
<header class="masthead">
  <div class="masthead-row">
    <div>
      <h1 class="masthead-title">Label Compliance Inspection Report</h1>
      <p class="masthead-sub">Assessment against the Legal Metrology (Packaged
        Commodities) Rules, 2011 and applicable Food Safety and Standards
        Authority of India labelling regulations.</p>
    </div>
    <div class="masthead-ref">
      Report reference<b>{_e(scan_id[:16])}</b>
      Generated {generated}
    </div>
  </div>
</header>
<div class="rule-thin"></div>

{provenance}

<div class="verdict" style="border-color:{accent}33;background:{tint}">
  <div class="verdict-main">
    <p class="verdict-name" style="color:{accent}">{_e(name)}</p>
    <p class="verdict-gloss">{_e(gloss)}</p>
  </div>
  <div class="verdict-score">
    <div class="score-value" style="color:{accent}">{_fmt_score(report.get("score"))}<span
       class="score-max">/100</span></div>
    <div class="score-caption">Compliance score</div>
  </div>
</div>

<div class="tally">{tally_html}</div>

<table class="meta"><tbody>{meta_html}</tbody></table>

{_findings_section(violations)}
{_reference_standards_section(standards)}
{_appendix_section(results) if include_appendix else ""}

<div class="sign">
  <div class="sign-box"><div class="sign-line"></div>
    <div class="sign-label">Signature of assessing officer</div></div>
  <div class="sign-box"><div class="sign-line"></div>
    <div class="sign-label">Name, designation &amp; date</div></div>
</div>

<footer>
  <p><b>How this assessment was produced.</b> Label values were extracted from the
  submitted photograph by automated optical character recognition and a vision
  model; the compliance determination was then made by a deterministic rule engine
  evaluating those values against versioned encodings of the cited provisions. The
  same input always yields the same verdict.</p>
  <p><b>Standing of this document.</b> Clause numbers are transcribed from the
  official gazette notifications; the detection logic that maps a label to each
  provision is this system's own implementation. Extraction from a photograph may
  err. This report is decision support for a competent officer and is not, by
  itself, a legal determination or a laboratory certificate.</p>
  <p>Label Jaano &middot; report generated {generated} &middot; reference
  {_e(scan_id)}</p>
</footer>
</body>
</html>
"""
