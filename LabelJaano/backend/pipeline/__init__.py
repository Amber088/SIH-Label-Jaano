"""
Label Jaano extraction pipeline — turns label photos into the engine's scan-input.

    from pipeline import extract_scan_input
    from rule_engine import ScanInput, evaluate_scan

    scan_dict = extract_scan_input(["front.jpg", "back.jpg"],
                                   reference={"type": "card", "width_mm": 85.6,
                                              "bbox": [x, y, w, h], "image": 0})
    report = evaluate_scan(ScanInput.from_dict(scan_dict))

Hybrid design: PaddleOCR gives word-level pixel boxes (for Rule 8 font-size), Gemini
2.0 Flash gives the structured field extraction, a reference object gives pixel->mm
calibration, and fusion merges them (across front/back images) into the scan-input
contract. Every heavy dependency is lazy-loaded; pass ``mock=True`` to run the whole
thing with zero external deps or API keys (deterministic fixtures) for tests/demos.
"""
from .pipeline import (  # noqa: F401
    extract_and_evaluate,
    extract_scan_input,
    resolve_mock_mode,
)
from .types import (  # noqa: F401
    CalibrationResult,
    GeminiExtraction,
    GeminiField,
    OcrResult,
    OcrWord,
)

__all__ = [
    "extract_scan_input",
    "extract_and_evaluate",
    "resolve_mock_mode",
    "OcrWord",
    "OcrResult",
    "GeminiField",
    "GeminiExtraction",
    "CalibrationResult",
]
