#!/usr/bin/env python3
"""
CLI demo for the Label Jaano extraction pipeline.

Turn label photo(s) into the engine's scan-input JSON — and, with --evaluate, straight
into a compliance verdict:

    # just extract the structured scan-input (front + back panels)
    python extract.py front.jpg back.jpg

    # extract AND score it in one go
    python extract.py front.jpg back.jpg --evaluate

    # supply a reference object so Rule 8 font-heights are measured in mm
    python extract.py front.jpg --evaluate \\
        --reference '{"type":"card","width_mm":85.6,"bbox":[40,900,320,200]}'

    # run with no API key / no heavy install (deterministic mock)
    python extract.py front.jpg back.jpg --evaluate --mock

Set GEMINI_API_KEY (or GOOGLE_API_KEY) for the real Gemini call; install
paddleocr/paddlepaddle for real OCR and opencv-python for ArUco calibration. Without
those, pass --mock (or set LABEL_JAANO_MOCK=1) and the pipeline runs offline.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline import extract_scan_input, extract_and_evaluate  # noqa: E402
from rule_engine.models import Verdict                          # noqa: E402
from run_scan import render                                     # noqa: E402  (reuse renderer)


def _load_json_arg(val):
    """Parse a --reference/--context arg: inline JSON, or @path to a JSON file."""
    if not val:
        return None
    if val.startswith("@"):
        return json.loads(Path(val[1:]).read_text(encoding="utf-8"))
    return json.loads(val)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Extract a scan-input (and optionally score it) from label images.")
    ap.add_argument("images", nargs="+", help="one or more label image paths (front, back, ...)")
    ap.add_argument("--reference", help='reference object for mm calibration: inline JSON '
                    'or @file, e.g. \'{"type":"card","width_mm":85.6,"bbox":[x,y,w,h]}\'')
    ap.add_argument("--context", help="context overrides (officer input): inline JSON or @file")
    ap.add_argument("--category", help="override/force the product category")
    ap.add_argument("--mock", action="store_true", help="use the offline mock (no deps/key)")
    ap.add_argument("--evaluate", action="store_true", help="also run the compliance engine")
    ap.add_argument("--json", action="store_true", help="emit JSON (scan-input, or report if --evaluate)")
    args = ap.parse_args()

    reference = _load_json_arg(args.reference)
    context = _load_json_arg(args.context)
    mock = True if args.mock else None  # None => auto-detect per layer

    if not args.evaluate:
        scan = extract_scan_input(args.images, reference=reference,
                                  context_overrides=context, mock=mock,
                                  category_hint=args.category)
        print(json.dumps(scan, indent=2, ensure_ascii=False))
        return 0

    scan, report = extract_and_evaluate(args.images, reference=reference,
                                        context_overrides=context, mock=mock,
                                        category_hint=args.category)
    if args.json:
        print(json.dumps({"scan_input": scan, "report": report.to_dict()},
                         indent=2, ensure_ascii=False))
    else:
        print(render(report))

    return {Verdict.COMPLIANT: 0, Verdict.NEEDS_REVIEW: 1, Verdict.NON_COMPLIANT: 2}[report.verdict]


if __name__ == "__main__":
    raise SystemExit(main())
