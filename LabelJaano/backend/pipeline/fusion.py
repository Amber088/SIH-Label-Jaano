"""
Fusion — merge OCR + Gemini + calibration into the engine's scan-input contract.

This is where the three signals become one object the rule engine can score:
  * Gemini gives the structured field values, the coarse panel, and which image each
    was read from;
  * OCR (for that image) + calibration give the glyph height in millimetres;
  * everything is normalized to the engine's vocabulary.

The critical mapping: the engine's placement check compares ``field.panel`` to the
pack's panel string exactly (e.g. ``"principal_display_panel"``), so the LLM's coarse
"front"/"back" tags MUST be translated here — otherwise a correctly-placed net-quantity
would be scored as misplaced.

Pure stdlib; unit-testable without any heavy dependency.
"""
from __future__ import annotations

from typing import Optional

from .calibration import measure_height_mm, pdp_area_cm2
from .prompts import CONTEXT_KEYS
from .types import CalibrationResult, GeminiExtraction, OcrResult

# LLM coarse panel tag -> engine panel vocabulary (must match the rule packs).
_PANEL_MAP = {
    "front": "principal_display_panel",
    "principal_display_panel": "principal_display_panel",
    "pdp": "principal_display_panel",
    "back": "back_panel",
    "back_panel": "back_panel",
    "side": "side_panel",
    "top": "top_panel",
    "bottom": "bottom_panel",
}

try:  # keep symbol names in sync with the engine; degrade gracefully if unavailable
    from rule_engine.checks import KNOWN_SYMBOLS
except Exception:  # noqa: BLE001
    KNOWN_SYMBOLS = {"veg_nonveg_mark", "fssai_logo"}


def _clamp_index(i: int, n: int) -> int:
    if n <= 0:
        return 0
    return max(0, min(int(i), n - 1))


def fuse(
    extraction: GeminiExtraction,
    ocr_results: list[OcrResult],
    calibrations: list[CalibrationResult],
    reference: Optional[dict] = None,
    context_overrides: Optional[dict] = None,
) -> dict:
    """Produce the scan-input contract dict (see rule_engine + backend/README)."""
    n_img = len(ocr_results)

    # ---- fields ----
    fields_out: dict[str, dict] = {}
    for decl_id, gf in extraction.fields.items():
        if not gf.value:
            continue
        entry: dict = {"value": gf.value}
        panel = _PANEL_MAP.get(gf.panel)
        if panel:
            entry["panel"] = panel
        if gf.confidence:
            entry["confidence"] = round(gf.confidence, 3)

        idx = _clamp_index(gf.source_image, n_img)
        if n_img and calibrations:
            cal = calibrations[idx] if idx < len(calibrations) else calibrations[0]
            h_mm = measure_height_mm(gf.value, ocr_results[idx].words, cal.mm_per_px)
            if h_mm is not None:
                entry["height_mm"] = h_mm
        fields_out[decl_id] = entry

    # ---- raw_text: union of OCR transcription + LLM transcription (max recall) ----
    ocr_text = " ".join(r.full_text for r in ocr_results if r.full_text).strip()
    raw_text = " ".join(t for t in (ocr_text, extraction.raw_text) if t).strip()

    # ---- symbols: only names the engine understands ----
    symbols = [s for s in extraction.symbols_detected if s in KNOWN_SYMBOLS]

    # ---- context: LLM booleans + calibrated PDP area, officer override wins ----
    context: dict = {k: bool(v) for k, v in extraction.context.items() if k in CONTEXT_KEYS}
    area = _pdp_area(reference, calibrations)
    if area is not None:
        context["pdp_area_cm2"] = area
    if context_overrides:
        context.update(context_overrides)

    return {
        "category": extraction.category or "unknown",
        "raw_text": raw_text,
        "fields": fields_out,
        "symbols_detected": symbols,
        "context": context,
    }


def _pdp_area(reference: Optional[dict],
              calibrations: list[CalibrationResult]) -> Optional[float]:
    """cm² of the principal display panel, if a pdp_bbox + a calibrated scale exist."""
    if not reference or not reference.get("pdp_bbox") or not calibrations:
        return None
    ref_idx = _clamp_index(reference.get("image", 0), len(calibrations))
    cal = calibrations[ref_idx]
    if not cal.calibrated:
        return None
    return pdp_area_cm2(reference["pdp_bbox"], cal.mm_per_px)
