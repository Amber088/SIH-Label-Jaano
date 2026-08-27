"""
The engine: judge a scan against a rule set and produce a ComplianceReport.

    report = evaluate(scan, ruleset)
    report = evaluate_scan(scan)            # convenience: builds the ruleset for you

Verdict rule (matches rulepacks/README.md):
    any failed CRITICAL check  -> non_compliant
    else any failed MAJOR      -> needs_review
    else                       -> compliant   (minor-only failures don't downgrade)

Score:
    100 * (weighted PASS) / (weighted PASS + weighted FAIL),  weights by severity.
    SKIP outcomes are excluded from the score.
"""
from __future__ import annotations

import re
from typing import Optional

from .checks import condition_met, evaluate_check
from .loader import build_ruleset
from .models import (
    CheckResult,
    ComplianceReport,
    Outcome,
    RuleSet,
    ScanInput,
    Severity,
    Verdict,
    Violation,
)


def evaluate(scan: ScanInput, ruleset: RuleSet) -> ComplianceReport:
    # Guard: if the image doesn't look like a packaged-commodity label at all (a book
    # cover, a face, a random object), say so plainly rather than scoring it as a
    # catastrophically non-compliant *product*.
    if not _looks_like_label(scan):
        return _no_label_report(scan, ruleset)

    results: list[CheckResult] = []
    violations: list[Violation] = []

    for decl in ruleset.declarations:
        if not condition_met(decl.condition, scan):
            continue
        # Optional declaration that simply wasn't present -> not a violation; skip it
        # entirely (matches rulepacks/README: advisory checks only count when present).
        field = scan.fields.get(decl.id)
        present = field.present if field else False
        if not decl.required and not present:
            continue
        for check in decl.checks:
            outcome, detail = evaluate_check(check, decl, scan, ruleset)
            results.append(CheckResult(
                declaration_id=decl.id,
                declaration_label=decl.label,
                legal_reference=decl.legal_reference,
                severity=decl.severity,
                check_type=check.type,
                outcome=outcome,
                message=check.message,
                detail=detail,
            ))
            if outcome is Outcome.FAIL:
                violations.append(Violation(
                    declaration_id=decl.id,
                    declaration_label=decl.label,
                    legal_reference=decl.legal_reference,
                    severity=decl.severity,
                    check_type=check.type,
                    message=check.message or f"{decl.label}: {check.type} check failed",
                    detail=detail,
                ))

    verdict = _verdict(violations)
    score = _score(results, ruleset.weights)
    summary = _summary(results, violations)
    return ComplianceReport(
        verdict=verdict,
        score=score,
        category=scan.category,
        packs_applied=ruleset.packs_applied,
        violations=violations,
        results=results,
        summary=summary,
        reference_standards=_collect_reference_standards(ruleset, scan),
    )


def _collect_reference_standards(ruleset: RuleSet, scan: ScanInput) -> list[dict]:
    """Tier-2 provisions that apply to this product but a photo cannot verify.

    Gated by the same condition tokens as declarations, but they are NEVER run as
    checks — so they carry no outcome and cannot touch the score or verdict. They
    ride along in the report purely as "applicable standard; verify in lab" context.
    """
    out: list[dict] = []
    for ref in ruleset.reference_standards:
        if condition_met(ref.condition, scan):
            out.append(ref.to_dict())
    return out


def evaluate_scan(scan: ScanInput,
                  rulepacks_dir_path: Optional[str] = None) -> ComplianceReport:
    """Convenience wrapper: pick + merge the packs for the scan's category, then evaluate."""
    ruleset = build_ruleset(scan.category, rulepacks_dir_path=rulepacks_dir_path)
    return evaluate(scan, ruleset)


# --------------------------------------------------------------------------- #
# "Is this even a label?" guard
# --------------------------------------------------------------------------- #
# A packaged-commodity label leaves an unmistakable textual fingerprint even when the
# structured extractor pulls nothing. If NOTHING was extracted AND none of these tokens
# appear in the raw text, the image almost certainly is not a product label.
_LABEL_SIGNAL_RE = re.compile(
    r"(net\s*(qty|quantity|wt|weight)"
    r"|m\.?\s*r\.?\s*p\.?"
    r"|maximum retail price"
    r"|mfg|mfd|manufactured|packed on|best before|use by|expiry|exp\b"
    r"|fssai|batch\s*no|lot\s*no"
    r"|\b\d+(\.\d+)?\s*(g|kg|mg|ml|l|ltr|litre|liter|gm|gms)\b)",
    re.IGNORECASE,
)


def _looks_like_label(scan: ScanInput) -> bool:
    """True if the scan plausibly came from a packaged-commodity label.

    Any one of these is decisive, because each only appears when a real product
    was seen: an extracted declaration value, a detected regulatory symbol
    (FSSAI logo, veg/non-veg mark, …), or a set context flag (imported,
    has_allergens, …). Failing all of those, accept it as a label only when the
    raw text still carries a clear commodity signal (net quantity, MRP, FSSAI, a
    weight/volume). Otherwise it's 'no label'.
    """
    if any(f.present for f in scan.fields.values()):
        return True
    if scan.symbols_detected:
        return True
    ctx = scan.context
    if ctx and (ctx.is_imported or ctx.is_single_ingredient or ctx.has_additives
                or ctx.has_allergens or ctx.dimension_relevant):
        return True
    return bool(_LABEL_SIGNAL_RE.search(scan.raw_text or ""))


def _no_label_report(scan: ScanInput, ruleset: RuleSet) -> ComplianceReport:
    """A clean report for an image that carries no readable packaged-commodity label."""
    return ComplianceReport(
        verdict=Verdict.NO_LABEL,
        score=0.0,
        category=scan.category or "unknown",
        packs_applied=ruleset.packs_applied,
        violations=[],
        results=[],
        summary={
            "checks_total": 0,
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "violations_total": 0,
            "violations_by_severity": {"critical": 0, "major": 0, "minor": 0},
            "note": "No packaged-commodity label detected in the image.",
        },
    )


# --------------------------------------------------------------------------- #
# Verdict / score / summary
# --------------------------------------------------------------------------- #
def _verdict(violations: list[Violation]) -> Verdict:
    severities = {v.severity for v in violations}
    if Severity.CRITICAL in severities:
        return Verdict.NON_COMPLIANT
    if Severity.MAJOR in severities:
        return Verdict.NEEDS_REVIEW
    return Verdict.COMPLIANT


def _score(results: list[CheckResult], weights: dict) -> float:
    def w(sev: Severity) -> int:
        return weights.get(sev.value, sev.weight)

    passed = sum(w(r.severity) for r in results if r.outcome is Outcome.PASS)
    scored = sum(w(r.severity) for r in results
                 if r.outcome in (Outcome.PASS, Outcome.FAIL))
    return round(100.0 * passed / scored, 1) if scored else 100.0


def _summary(results: list[CheckResult], violations: list[Violation]) -> dict:
    def count(outcome: Outcome) -> int:
        return sum(1 for r in results if r.outcome is outcome)

    return {
        "checks_total": len(results),
        "passed": count(Outcome.PASS),
        "failed": count(Outcome.FAIL),
        "skipped": count(Outcome.SKIP),
        "violations_total": len(violations),
        "violations_by_severity": {
            "critical": sum(1 for v in violations if v.severity is Severity.CRITICAL),
            "major": sum(1 for v in violations if v.severity is Severity.MAJOR),
            "minor": sum(1 for v in violations if v.severity is Severity.MINOR),
        },
    }
