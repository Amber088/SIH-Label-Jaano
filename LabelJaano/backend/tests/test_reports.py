#!/usr/bin/env python3
"""
Tests for the inspection-report renderer.

Runs two ways:
    pytest                         # from the backend/ directory
    python3 tests/test_reports.py  # no pytest needed — self-contained runner

Two properties carry most of the weight here, and both are about the document being
trustworthy rather than about it looking nice:

* **Everything is escaped.** Nearly every string in a report was read off a
  photograph by OCR or a vision model, or typed into a note field. A label whose
  ingredients list contains ``<script>`` must not become live markup in a document
  that gets emailed to a shop owner and a magistrate. ``test_injection_*`` push markup
  through every interpolated field and assert none of it survives as a tag.

* **A mock read is never mistakable for a real finding.** ``test_mock_output_is_marked``
  is the one that matters for the demo: if a synthetic assessment could be printed
  looking identical to a live one, the printed document would be evidence of nothing.

The rest guard renderability. The renderer's contract is that only ``scan_id`` is
required and anything else missing becomes an em dash, because a report that will not
render is worse than one with a gap in it — so the degenerate inputs are tested as
first-class cases, not as edge cases.
"""
from __future__ import annotations

import html
import json
import re
import sys
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
SAMPLES = BACKEND / "samples"
sys.path.insert(0, str(BACKEND))

from reports import VERDICT_PRESENTATION, render_inspection_report  # noqa: E402
from reports.inspection_html import _humanise, _titleise  # noqa: E402
from rule_engine import ScanInput, evaluate_scan  # noqa: E402

# Markup that must never survive into the output as markup. The closing tag matters
# as much as the opening one: a bare "<script" is harmless, "</style>" mid-attribute
# is not, because it can escape the stylesheet context.
XSS = '<script>alert(1)</script>"><img src=x onerror=alert(1)>&</style>'


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _report(sample: str = "good_label.json", **overrides) -> dict:
    raw = json.loads((SAMPLES / sample).read_text(encoding="utf-8"))
    raw.update(overrides)
    return evaluate_scan(ScanInput.from_dict(raw)).to_dict()


def _render(sample: str = "good_label.json", **kw) -> str:
    kw.setdefault("scan_id", "abc123def456")
    return render_inspection_report(_report(sample), **kw)


class _TagCollector(HTMLParser):
    """Collect the tags a real HTML parser sees, which is the only honest test.

    Substring checks are not enough: ``&lt;script&gt;`` contains neither ``<script``
    nor a tag, but a naive ``"<script" not in html`` assertion would also pass on
    ``<SCRIPT >`` or an attribute-borne ``onerror``. Parsing tells us what a browser
    would actually build.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[str] = []
        self.attrs: list[tuple[str, str]] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        for k, v in attrs:
            self.attrs.append((k, v or ""))

    handle_startendtag = handle_starttag


def _parse(html_text: str) -> _TagCollector:
    collector = _TagCollector()
    collector.feed(html_text)
    return collector


def _assert_no_injection(html_text: str, where: str) -> None:
    """Assert the XSS payload produced no executable markup anywhere in *html_text*.

    Rendered without the print bar on purpose: that bar carries the one legitimate
    ``onclick`` in the document (its Print button), and allowing it here by name would
    blunt the check. Excluding it instead keeps the rule absolute — *no* event handler
    may come from data.
    """
    parsed = _parse(html_text)
    assert "script" not in parsed.tags, f"{where}: a <script> tag reached the document"
    assert "img" not in parsed.tags, f"{where}: an <img> tag reached the document"
    for key, _value in parsed.attrs:
        assert not key.lower().startswith("on"), (
            f"{where}: event-handler attribute {key!r} reached the document")
    # The payload's own text should still be *visible* — escaping, not stripping. An
    # officer needs to see what the label actually said, verbatim.
    assert "alert(1)" in html_text, (
        f"{where}: the payload vanished entirely; it should be escaped and shown, "
        "not silently dropped — the officer needs to see what the label said")


# --------------------------------------------------------------------------- #
# Structure
# --------------------------------------------------------------------------- #
def test_renders_a_complete_standalone_document():
    """Standalone matters: the API serves this as a whole page and a browser must be
    able to print it with no external stylesheet, script or font to fetch."""
    html_text = _render()
    assert html_text.startswith("<!DOCTYPE html>")
    assert html_text.rstrip().endswith("</html>")
    for required in ("<html", "<head", "<title", "<style", "<body", "</body>"):
        assert required in html_text, f"missing {required}"
    assert "<link" not in html_text, "no external stylesheet may be referenced"
    assert 'src="http' not in html_text, "no remote asset may be referenced"


def test_document_parses_cleanly():
    parsed = _parse(_render("bad_label.json"))
    for tag in ("html", "head", "body", "table", "section", "footer"):
        assert tag in parsed.tags, f"parser never saw <{tag}>"


def test_print_stylesheet_is_present():
    """These four rules are what make the browser's "Save as PDF" look like a filed
    document rather than a screenshot of a web page."""
    html_text = _render()
    assert "@page" in html_text and "A4" in html_text
    assert "table-header-group" in html_text, "table headings must repeat when printed"
    assert "break-inside: avoid" in html_text, "a finding must not split across pages"
    assert "@media print" in html_text


def test_print_bar_is_optional():
    """It is a screen affordance. Rendering to a file or piping to a PDF converter
    should be able to leave it out."""
    assert "window.print()" in _render()
    assert "window.print()" not in _render(include_print_bar=False)


def test_appendix_is_optional_but_findings_are_not():
    with_appendix = _render("bad_label.json")
    without = _render("bad_label.json", include_appendix=False)
    assert "full assessment log" in with_appendix
    assert "full assessment log" not in without
    # The findings section is the point of the document; it is never suppressible.
    assert "Findings" in with_appendix and "Findings" in without
    assert len(without) < len(with_appendix)


# --------------------------------------------------------------------------- #
# Content fidelity
# --------------------------------------------------------------------------- #
def test_verdict_and_score_are_shown():
    html_text = _render()
    assert "Compliant" in html_text
    assert "100.0" in html_text and "/100" in html_text


def test_every_known_verdict_has_presentation():
    """A verdict the engine can emit but the report cannot name would print as
    'Unknown' on a real inspection."""
    for verdict in ("compliant", "needs_review", "non_compliant", "no_label_detected"):
        assert verdict in VERDICT_PRESENTATION, f"{verdict} has no presentation entry"
        heading, gloss, accent, tint = VERDICT_PRESENTATION[verdict]
        assert heading and gloss
        assert re.fullmatch(r"#[0-9a-fA-F]{6}", accent), f"{verdict}: bad accent colour"
        assert re.fullmatch(r"#[0-9a-fA-F]{6}", tint), f"{verdict}: bad tint colour"


def test_unrecognised_verdict_says_so_in_verdict_terms():
    """Regression: this used to print "Uncategorised" — a statement about the commodity
    category, which tells an officer nothing about the finding."""
    html_text = render_inspection_report(
        {"verdict": "partially_assessed", "score": 40}, scan_id="x1")
    assert "Uncategorised" not in html_text.split("<table class=\"meta\"")[0], (
        "the verdict banner described the category instead of the verdict")
    assert "Partially assessed" in html_text


def test_violations_are_listed_worst_first():
    """Severity order is the officer's reading order: a critical finding must not be
    below a minor one on the page."""
    html_text = _render("bad_label.json")
    positions = [html_text.find(label) for label in ("Critical", "Major", "Minor")]
    present = [p for p in positions if p != -1]
    assert present == sorted(present), "severity pills are out of order"


def test_findings_cite_their_legal_reference():
    """A finding without a citation is an accusation. Every violation in the report
    must carry the provision it breaches.

    Compared against the *escaped* citation, because real clause references contain
    ampersands ("Food Safety & Standards…") and the renderer escapes everything. An
    assertion on the raw string would fail here for the right reason.
    """
    report = _report("bad_label.json")
    violations = report.get("violations") or []
    assert violations, "the bad sample should produce violations"
    html_text = render_inspection_report(report, scan_id="cite-check")
    for v in violations:
        reference = v.get("legal_reference")
        assert reference, f"{v.get('declaration_id')} carries no legal_reference"
        assert html.escape(str(reference), quote=True) in html_text, (
            f"citation {reference!r} missing from the report")


def test_clean_label_states_no_contraventions():
    html_text = _render("good_label.json")
    assert "No contraventions were identified" in html_text


def test_metadata_is_rendered():
    html_text = _render(
        product_name="Tata Salt 1kg", location="Shop 4, Pune", note="Routine check",
        created_at="2026-08-27T14:32:00Z",
        inspector={"name": "A. Jain", "email": "a@demo.gov.in",
                   "role_label": "Enforcement officer"},
    )
    for expected in ("Tata Salt 1kg", "Shop 4, Pune", "Routine check", "A. Jain",
                     "a@demo.gov.in", "Enforcement officer", "27 August 2026"):
        assert expected in html_text, f"{expected!r} missing from the report"


def test_missing_metadata_degrades_rather_than_raising():
    """The renderer's contract: only scan_id is required."""
    html_text = render_inspection_report({}, scan_id="bare")
    assert html_text.startswith("<!DOCTYPE html>")
    assert "Not recorded" in html_text, "absent fields should say so, not be blank"
    assert "bare" in html_text


def test_renders_from_junk_input_without_raising():
    """Defensive, because this can be fed a report row written by an older build."""
    for junk in (None, [], "a string", 42, {"violations": "not-a-list"},
                 {"results": [None, "x", {}], "summary": "nope"},
                 {"score": "not-a-number", "verdict": None}):
        html_text = render_inspection_report(junk, scan_id="junk")
        assert html_text.startswith("<!DOCTYPE html>"), f"failed to render {junk!r}"
        assert "</html>" in html_text


def test_non_string_scan_id_still_renders():
    """The reference is sliced for the masthead, so a UUID object or an int used to
    raise on the slice rather than render."""
    import uuid
    for identifier in (uuid.uuid4(), 12345):
        html_text = render_inspection_report({}, scan_id=identifier)
        assert html_text.startswith("<!DOCTYPE html>")
        assert str(identifier)[:16] in html_text


def test_odd_timestamp_falls_back_to_raw_text():
    html_text = render_inspection_report({}, scan_id="t", created_at="not-a-date")
    assert "not-a-date" in html_text, "an unparseable timestamp must still be shown"


# --------------------------------------------------------------------------- #
# Two-tier separation
# --------------------------------------------------------------------------- #
def test_reference_standards_are_not_presented_as_findings():
    """The whole two-tier design fails if the printed document blurs the line. A
    lab-only provision listed among the contraventions would be a finding the officer
    cannot substantiate."""
    report = _report("good_label.json")
    report["reference_standards"] = [{
        "id": "fssai_additive_limits",
        "label": "Permitted additive limits",
        "legal_reference": "FSS (Food Products Standards and Food Additives) "
                           "Regulations, 2011",
        "authority": "FSSAI",
        "note": "Requires laboratory assay.",
    }]
    html_text = render_inspection_report(report, scan_id="two-tier")

    assert "requiring laboratory verification" in html_text
    assert "not</b> findings" in html_text, (
        "the section must state outright that these did not affect the verdict")
    # It must sit outside the findings table, after it.
    findings_at = html_text.find(">Findings<")
    standards_at = html_text.find("requiring laboratory verification")
    assert findings_at < standards_at, "standards must follow findings, not precede them"
    assert "Permitted additive limits" in html_text


def test_reference_standards_section_is_omitted_when_empty():
    report = _report("good_label.json")
    report["reference_standards"] = []
    assert "requiring laboratory verification" not in render_inspection_report(
        report, scan_id="none")


def test_standard_without_a_note_gets_an_action_anyway():
    """An empty Action cell would read as "nothing to do", which is the opposite of
    what a lab-verification row means."""
    html_text = render_inspection_report(
        {"verdict": "compliant",
         "reference_standards": [{"id": "x", "label": "Y", "legal_reference": "Z"}]},
        scan_id="no-note")
    assert "Verify by laboratory analysis." in html_text


# --------------------------------------------------------------------------- #
# Appendix disambiguation
# --------------------------------------------------------------------------- #
def test_repeated_check_types_are_numbered():
    """Two 'format' checks on one declaration, one passing and one failing, look like
    the tool contradicting itself unless they are distinguished."""
    html_text = render_inspection_report(
        {
            "verdict": "needs_review",
            "results": [
                {"declaration_id": "mrp", "declaration_label": "Retail sale price",
                 "check_type": "format", "outcome": "pass", "legal_reference": "R.6(1)(e)"},
                {"declaration_id": "mrp", "declaration_label": "Retail sale price",
                 "check_type": "format", "outcome": "fail", "legal_reference": "R.6(1)(e)",
                 "message": "MRP must be inclusive of all taxes."},
                {"declaration_id": "net_qty", "declaration_label": "Net quantity",
                 "check_type": "presence", "outcome": "pass", "legal_reference": "R.6(1)(d)"},
            ],
        },
        scan_id="dup-check")
    assert "format (1 of 2)" in html_text and "format (2 of 2)" in html_text
    assert "presence (1 of 1)" not in html_text, (
        "a check that appears once must not be numbered — the noise implies "
        "there is another one to find")


def test_pack_message_is_not_printed_on_a_passing_row():
    """Pack messages are phrased as the failure ("MRP is missing."), so printing one
    on a Pass row would read as an accusation against a compliant product."""
    html_text = render_inspection_report(
        {"verdict": "compliant",
         "results": [{"declaration_id": "mrp", "declaration_label": "Retail sale price",
                      "check_type": "presence", "outcome": "pass",
                      "message": "MRP is missing.", "detail": "Found: MRP Rs 45.00"}]},
        scan_id="pass-msg")
    assert "MRP is missing." not in html_text, "failure wording printed on a Pass row"
    assert "Found: MRP Rs 45.00" in html_text, "the evidence should still be shown"


def test_skipped_checks_are_explained_not_just_labelled():
    """"Not assessed" is the most misreadable cell in the document — it must not be
    taken for a pass or for a waiver."""
    html_text = render_inspection_report(
        {"verdict": "needs_review",
         "results": [{"declaration_id": "font_size", "declaration_label": "Glyph height",
                      "check_type": "measurement", "outcome": "skip",
                      "message": "Minimum height could not be measured."}]},
        scan_id="skip")
    assert "Not assessed" in html_text
    assert "excluded from the score" in html_text, (
        "the appendix must explain that a skip is not a pass")


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #
def test_mock_output_is_marked_unmistakably():
    """The single most important assertion in this file. A synthetic assessment that
    printed identically to a live one would make the document evidence of nothing."""
    html_text = _render(mock=True, source="image")
    assert "not an" in html_text and "enforceable finding" in html_text
    assert "mock" in html_text.lower()
    assert "demonstration" in html_text.lower()
    assert "class=\"provenance\"" in html_text, "the warning must be visually distinct"
    # It has to be above the verdict; a disclaimer under the fold is not a disclaimer.
    assert html_text.find("provenance") < html_text.find("class=\"verdict\"")


def test_real_output_carries_no_mock_warning():
    """The warning must mean something, so it must be absent on a genuine read."""
    html_text = _render(mock=False, source="image")
    assert "class=\"provenance\"" not in html_text
    assert "enforceable finding" not in html_text
    assert "Photograph, live extraction pipeline" in html_text


def test_evidence_source_is_stated():
    assert "Pre-normalised scan input" in _render(source="json")
    assert "Photograph, live extraction pipeline" in _render(source="image")
    assert "offline mock pipeline" in _render(source="image", mock=True)


def test_report_states_its_own_standing():
    """Overclaiming is the failure mode here: the document must not read as a legal
    determination or a lab certificate."""
    html_text = _render()
    assert "not, by\n  itself, a legal determination" in html_text or \
           "not, by itself, a legal determination" in " ".join(html_text.split())
    assert "decision support" in html_text
    assert "Extraction from a photograph may" in html_text


# --------------------------------------------------------------------------- #
# Escaping — the security property
# --------------------------------------------------------------------------- #
def test_injection_via_officer_metadata_is_escaped():
    _assert_no_injection(
        render_inspection_report(
            {"verdict": "compliant", "score": 90},
            scan_id=XSS, product_name=XSS, note=XSS, location=XSS, source=XSS,
            created_at=XSS,
            inspector={"name": XSS, "email": XSS, "role_label": XSS},
            include_print_bar=False,
        ),
        "officer metadata")


def test_injection_via_extracted_label_text_is_escaped():
    """This is the realistic attack: the strings came off a photograph."""
    _assert_no_injection(
        render_inspection_report(
            {
                "verdict": XSS,
                "score": 50,
                "category": XSS,
                "packs_applied": [XSS, XSS],
                "summary": {"violations_by_severity": {"critical": XSS}},
                "violations": [{
                    "declaration_id": XSS, "declaration_label": XSS,
                    "legal_reference": XSS, "severity": XSS,
                    "message": XSS, "detail": XSS,
                }],
                "results": [{
                    "declaration_id": XSS, "declaration_label": XSS,
                    "legal_reference": XSS, "check_type": XSS,
                    "outcome": XSS, "message": XSS, "detail": XSS,
                }],
                "reference_standards": [{
                    "id": XSS, "label": XSS, "legal_reference": XSS,
                    "authority": XSS, "note": XSS,
                }],
            },
            scan_id="xss-label", include_print_bar=False),
        "extracted label text")


def test_attribute_context_is_escaped():
    """Severity and outcome are interpolated into a ``style`` attribute. Unescaped,
    a crafted value could close the attribute and add an event handler."""
    html_text = render_inspection_report(
        {"verdict": "non_compliant",
         "violations": [{"declaration_id": "d", "severity": '" onmouseover="alert(1)',
                         "message": "m", "legal_reference": "r"}]},
        scan_id="attr", include_print_bar=False)
    for key, _value in _parse(html_text).attrs:
        assert not key.lower().startswith("on"), f"attribute {key!r} escaped its context"


def test_the_only_event_handler_is_the_print_button():
    """Pins down the exception the injection tests exclude, so it stays a known,
    single, literal one rather than quietly becoming a family of them."""
    handlers = [(k, v) for k, v in _parse(_render()).attrs if k.lower().startswith("on")]
    assert handlers == [("onclick", "window.print()")], (
        f"unexpected event handlers in the document: {handlers}")


def test_quotes_in_a_note_do_not_break_the_document():
    html_text = render_inspection_report(
        {"verdict": "compliant"}, scan_id="q",
        note="Owner said \"it's the printer's fault\" & left")
    assert "&quot;" in html_text or "&#x27;" in html_text
    assert "printer" in html_text
    assert "html" in _parse(html_text).tags


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #
def test_titleise_is_for_categories_and_humanise_for_verdicts():
    assert _titleise("packaged_food") == "Packaged food"
    assert _titleise("unknown") == "Uncategorised"
    assert _titleise("") == "Uncategorised"
    assert _titleise(None) == "Uncategorised"
    # A verdict must never be described in category language.
    assert _humanise("no_label_detected") == "No label detected"
    assert _humanise("unknown") == "Unknown"
    assert _humanise("") == ""


def test_humanise_preserves_interior_capitals():
    """``FSSAI`` must not become ``Fssai`` — it is a statutory body's name."""
    assert _humanise("licence FSSAI missing") == "Licence FSSAI missing"


# --------------------------------------------------------------------------- #
# Self-contained runner (no pytest required)
# --------------------------------------------------------------------------- #
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
    print(f"Running report tests (today = {date.today().isoformat()})\n")
    raise SystemExit(_run_all())
