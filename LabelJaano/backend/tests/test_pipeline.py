#!/usr/bin/env python3
"""
Tests for the Label Jaano extraction pipeline (OCR + Gemini + calibration + fusion).

Runs two ways:
    pytest                          # from the backend/ directory
    python3 tests/test_pipeline.py  # no pytest needed — self-contained runner

Everything here runs in MOCK mode, so it needs no API key and no heavy install
(paddleocr / google-generativeai / opencv). The image fixtures in samples/ carry a
``<image>.mock.json`` sidecar whose ``ocr_words`` are the ACTUAL drawn glyph boxes, so
the font-height math is exercised against real geometry — not hand-waved numbers.
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
SAMPLES = BACKEND / "samples"
sys.path.insert(0, str(BACKEND))

from pipeline import extract_and_evaluate, extract_scan_input  # noqa: E402
from pipeline.calibration import (  # noqa: E402
    estimate_scale,
    measure_height_mm,
    pdp_area_cm2,
)
from pipeline.fusion import fuse  # noqa: E402
from pipeline.types import (  # noqa: E402
    CalibrationResult,
    GeminiExtraction,
    GeminiField,
    OcrResult,
    OcrWord,
)
from rule_engine.models import Verdict  # noqa: E402

FRONT = str(SAMPLES / "label_front.png")
BACK = str(SAMPLES / "label_back.png")
# Calibration published in the fixture's sidecar hint (see samples/make_fixtures.py).
MANUAL_REF = {"type": "manual", "mm_per_px": 0.0531, "pdp_bbox": [40, 40, 820, 1220]}


# --------------------------------------------------------------------------- #
# End-to-end on the image fixtures (the demo-critical paths)
# --------------------------------------------------------------------------- #
def test_extract_shape_from_fixture():
    scan = extract_scan_input([FRONT, BACK], mock=True)
    assert set(scan) == {"category", "raw_text", "fields",
                         "symbols_detected", "context"}, set(scan)
    assert scan["category"] == "packaged_food"
    nq = scan["fields"]["net_quantity"]
    assert nq["value"] == "Net Qty 500 g"
    # front-panel tag must be translated to the engine's vocabulary
    assert nq["panel"] == "principal_display_panel"
    # OCR transcription from BOTH panels lands in raw_text (max recall)
    assert "INGREDIENTS" in scan["raw_text"] and "GLUCOSE" in scan["raw_text"]


def test_fixture_is_compliant_with_reference():
    scan, report = extract_and_evaluate([FRONT, BACK], mock=True, reference=MANUAL_REF)
    assert report.verdict is Verdict.COMPLIANT, [v.to_dict() for v in report.violations]
    assert report.score == 100.0
    assert report.summary["skipped"] == 0  # font-height now measured, nothing skipped
    assert {"legal_metrology_2011", "fssai_food_2020"} <= set(report.packs_applied)
    # the reference produced a real millimetre height (~2.5 mm) and a PDP area
    assert scan["fields"]["net_quantity"]["height_mm"] == 2.5
    assert scan["context"]["pdp_area_cm2"] == 28.2


def test_fontheight_skipped_without_reference():
    scan, report = extract_and_evaluate([FRONT, BACK], mock=True)  # no reference
    # no scale -> no height measured -> engine SKIPs Rule 8 rather than guessing
    assert "height_mm" not in scan["fields"]["net_quantity"]
    assert report.summary["skipped"] >= 1
    skips = {(r.declaration_id, r.check_type) for r in report.results
             if r.outcome.value == "skip"}
    assert ("net_quantity", "font_height") in skips
    assert report.verdict is Verdict.COMPLIANT  # a skip is not a failure


def test_card_reference_measures_height():
    # The drawn reference card is 300 px wide == 85.6 mm.
    ref = {"type": "card", "width_mm": 85.6, "bbox": [560, 1090, 300, 190]}
    scan = extract_scan_input([FRONT, BACK], reference=ref, mock=True)
    assert scan["fields"]["net_quantity"].get("height_mm", 0) > 0


def test_bytes_input_uses_canned_extraction():
    # Raw bytes (as UploadFile.read() yields) have no sidecar -> canned mock read.
    scan = extract_scan_input([b"\xff\xd8\xff\xe0fakejpeg"], mock=True)
    assert scan["category"] == "packaged_food"
    assert "net_quantity" in scan["fields"]


def test_category_hint_overrides():
    scan = extract_scan_input([FRONT], mock=True, category_hint="Cosmetics")
    assert scan["category"] == "cosmetics"  # normalized lower-case


# --------------------------------------------------------------------------- #
# Calibration unit math (pure stdlib)
# --------------------------------------------------------------------------- #
def test_estimate_scale_manual():
    cal = estimate_scale({"type": "manual", "mm_per_px": 0.1})
    assert cal.calibrated and abs(cal.mm_per_px - 0.1) < 1e-9


def test_estimate_scale_card():
    cal = estimate_scale({"type": "card", "width_mm": 85.6, "bbox": [0, 0, 856, 500]})
    assert abs(cal.mm_per_px - 0.1) < 1e-9  # 85.6 mm / 856 px


def test_estimate_scale_none_and_bad():
    assert estimate_scale(None).calibrated is False
    assert estimate_scale({"type": "manual"}).calibrated is False        # missing value
    assert estimate_scale({"type": "card", "width_mm": 85.6}).calibrated is False  # no bbox


def test_measure_height_mm_median():
    # two multi-char tokens both match -> median of their heights; tall noise ignored
    words = [OcrWord("500", (0, 0, 40, 40), 0.9),
             OcrWord("ml", (0, 0, 40, 42), 0.9),
             OcrWord("NOISE", (0, 0, 40, 999), 0.9)]  # unrelated tall box ignored
    h = measure_height_mm("500 ml", words, mm_per_px=0.05)
    assert h == 2.05, h  # median(40, 42) = 41 px * 0.05


def test_measure_height_mm_single_char_fallback():
    # no multi-char token matches -> fall back to single-char tokens (e.g. unit "g")
    words = [OcrWord("g", (0, 0, 30, 30), 0.9)]
    assert measure_height_mm("5 g", words, mm_per_px=0.05) == 1.5


def test_measure_height_mm_no_scale():
    words = [OcrWord("500", (0, 0, 40, 40), 0.9)]
    assert measure_height_mm("500", words, mm_per_px=None) is None
    assert measure_height_mm("500", [], mm_per_px=0.05) is None


def test_pdp_area_cm2():
    # 1000 px * 0.05 = 50 mm; 500 px * 0.05 = 25 mm; 50*25 = 1250 mm^2 = 12.5 cm^2
    assert pdp_area_cm2([0, 0, 1000, 500], 0.05) == 12.5
    assert pdp_area_cm2([0, 0, 1000, 500], None) is None


# --------------------------------------------------------------------------- #
# Fusion unit behaviour (pure stdlib)
# --------------------------------------------------------------------------- #
def _extraction(**fields):
    return GeminiExtraction(
        category="packaged_food",
        fields={k: (v if isinstance(v, GeminiField) else GeminiField(value=v))
                for k, v in fields.items()},
        symbols_detected=["veg_nonveg_mark", "not_a_real_symbol"],
        context={"is_imported": True, "bogus_key": True},
        raw_text="hello",
    )


def test_fuse_panel_mapping_and_symbol_filter():
    ex = _extraction(net_quantity=GeminiField(value="500 g", panel="front"))
    ocr = [OcrResult(words=[], image_width=100, image_height=100, engine="mock")]
    cal = [CalibrationResult(method="none")]
    scan = fuse(ex, ocr, cal)
    assert scan["fields"]["net_quantity"]["panel"] == "principal_display_panel"
    # only engine-known symbols survive; unknown context keys are dropped
    assert scan["symbols_detected"] == ["veg_nonveg_mark"]
    assert "bogus_key" not in scan["context"] and scan["context"]["is_imported"] is True


def test_fuse_context_override_wins():
    ex = _extraction(mrp=GeminiField(value="Rs 10"))
    ocr = [OcrResult(words=[], image_width=10, image_height=10, engine="mock")]
    cal = [CalibrationResult(method="none")]
    scan = fuse(ex, ocr, cal, context_overrides={"is_imported": False})
    assert scan["context"]["is_imported"] is False  # officer input beats the LLM


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
    print("Running extraction-pipeline tests (mock mode)\n")
    raise SystemExit(_run_all())
