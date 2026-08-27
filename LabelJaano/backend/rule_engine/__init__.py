"""
Label Jaano rule engine — config-driven compliance checking.

Quick start:

    from rule_engine import ScanInput, evaluate_scan

    scan = ScanInput.from_dict({...})     # normalized OCR + vision-LLM output
    report = evaluate_scan(scan)          # picks the right packs by category
    print(report.verdict, report.score)

The rules themselves live in ``rulepacks/*.json`` — see ``rulepacks/README.md``.
"""
from .engine import evaluate, evaluate_scan
from .loader import build_ruleset, load_packs
from .models import (
    ComplianceReport,
    Field,
    Outcome,
    RuleSet,
    ScanContext,
    ScanInput,
    Severity,
    Verdict,
    Violation,
)

__all__ = [
    "evaluate",
    "evaluate_scan",
    "build_ruleset",
    "load_packs",
    "ScanInput",
    "ScanContext",
    "Field",
    "RuleSet",
    "ComplianceReport",
    "Violation",
    "Severity",
    "Outcome",
    "Verdict",
]

__version__ = "0.1.0"
