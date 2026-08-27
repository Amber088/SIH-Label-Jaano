#!/usr/bin/env python3
"""
CLI demo for the Label Jaano rule engine.

    python run_scan.py samples/good_label.json
    python run_scan.py samples/bad_label.json
    python run_scan.py samples/bad_label.json --json      # machine-readable output

Loads a normalized scan (the kind of JSON the OCR + vision-LLM pipeline will produce),
runs the compliance engine, and prints the verdict, score, and every violation with
its exact legal reference.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# make `rule_engine` importable no matter where this is run from
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rule_engine import ScanInput, evaluate_scan  # noqa: E402
from rule_engine.models import Outcome, Verdict     # noqa: E402

_VERDICT_ICON = {
    Verdict.COMPLIANT: "✅ COMPLIANT",
    Verdict.NEEDS_REVIEW: "⚠️  NEEDS REVIEW",
    Verdict.NON_COMPLIANT: "❌ NON-COMPLIANT",
}
_SEV_ICON = {"critical": "🔴", "major": "🟠", "minor": "🟡"}


def render(report) -> str:
    lines = []
    lines.append("=" * 64)
    lines.append(f"  {_VERDICT_ICON[report.verdict]}      score: {report.score}/100")
    lines.append(f"  category: {report.category}   packs: {', '.join(report.packs_applied)}")
    s = report.summary
    lines.append(
        f"  checks: {s['passed']} passed / {s['failed']} failed / "
        f"{s['skipped']} skipped   "
        f"(violations — {s['violations_by_severity']['critical']} critical, "
        f"{s['violations_by_severity']['major']} major, "
        f"{s['violations_by_severity']['minor']} minor)"
    )
    lines.append("=" * 64)

    if report.violations:
        lines.append("\nVIOLATIONS")
        for v in report.violations:
            icon = _SEV_ICON.get(v.severity.value, "•")
            lines.append(f"  {icon} [{v.severity.value.upper()}] {v.declaration_label} "
                         f"({v.legal_reference})")
            lines.append(f"      → {v.message}")
            if v.detail:
                lines.append(f"      · {v.detail}")
    else:
        lines.append("\nNo violations. 🎉")

    # show what was skipped so nothing looks silently ignored
    skipped = [r for r in report.results if r.outcome is Outcome.SKIP]
    if skipped:
        lines.append("\nNOT EVALUATED (needs a human / better capture)")
        for r in skipped:
            lines.append(f"  · {r.declaration_label} [{r.check_type}] — {r.detail}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the Label Jaano compliance engine on a scan JSON.")
    ap.add_argument("scan", help="path to a scan JSON file (see samples/)")
    ap.add_argument("--json", action="store_true", help="emit the raw report as JSON")
    ap.add_argument("--rulepacks", help="override the rulepacks directory")
    args = ap.parse_args()

    with open(args.scan, "r", encoding="utf-8") as fh:
        scan = ScanInput.from_dict(json.load(fh))

    report = evaluate_scan(scan, rulepacks_dir_path=args.rulepacks)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(render(report))

    # exit code: 0 compliant, 1 needs_review, 2 non_compliant (handy for CI/pipelines)
    return {Verdict.COMPLIANT: 0, Verdict.NEEDS_REVIEW: 1, Verdict.NON_COMPLIANT: 2}[report.verdict]


if __name__ == "__main__":
    raise SystemExit(main())
