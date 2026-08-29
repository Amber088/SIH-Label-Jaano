"""
Pipeline orchestrator — photos in, scan-input contract (or a full report) out.

    extract_scan_input(images, reference=None, context_overrides=None, mock=None)

Steps, for one product photographed on one or more panels:
  1. OCR every image (PaddleOCR) -> word boxes.
  2. One Gemini call over ALL images -> structured fields (it de-duplicates across
     panels itself and tags each value with its source image).
  3. Calibrate the reference image -> mm-per-pixel (broadcast to the set; assumes the
     panels were shot at a similar distance).
  4. Fuse -> the scan-input dict the engine scores.

The declaration ids and symbol names asked of Gemini are pulled from the *live* rule
packs, so the extractor and the rules can never drift apart.

``mock`` controls the dependency-free path end-to-end; when ``None`` each layer
auto-detects (mock if its library/key is missing). So this runs today, offline, and
lights up the real models as soon as they're installed and keyed.
"""
from __future__ import annotations

from typing import Optional

from .calibration import estimate_scale
from .fusion import fuse
from .gemini import _mock_requested as _gemini_mock_requested
from .gemini import extract_fields
from .ocr import _mock_requested as _ocr_mock_requested
from .ocr import run_ocr
from .types import ImageInput

# Categories offered to the classifier (union of what the packs target + common ones).
_FALLBACK_CATEGORIES = [
    "packaged_food", "food", "beverage", "packaged_water",
    "cosmetics", "drugs", "electronics", "household", "other", "unknown",
]


def resolve_mock_mode(mock: Optional[bool] = None) -> dict:
    """Report which extraction path a run with this ``mock`` argument will take.

    This exists because the auto-detecting default is a genuine trap. Each layer
    independently falls back to its offline mock when its dependency is missing — and
    :mod:`pipeline.gemini` treats *no API key* as a reason to fall back. So on a
    machine without ``GEMINI_API_KEY`` the default path quietly returns canned label
    values, and the engine dutifully judges them. Every check passes, and a photo of a
    book comes back compliant.

    That is the right behaviour for a laptop with nothing installed, but a report built
    on synthetic values must never be filed as if it were a real optical read. Callers
    ask this *before* extracting, so the stored report and the printed document can
    both say plainly where their values came from, and why.

    Returns ``{"mock", "ocr_mock", "gemini_mock", "reason"}``. ``mock`` is true when
    *any* layer is offline: a partially-synthetic read is not a live one.
    """
    if mock is True:
        return {"mock": True, "ocr_mock": True, "gemini_mock": True,
                "reason": "offline mock explicitly requested"}
    if mock is False:
        return {"mock": False, "ocr_mock": False, "gemini_mock": False,
                "reason": "live extraction explicitly requested"}

    ocr_mock = _ocr_mock_requested()
    gemini_mock = _gemini_mock_requested()

    if ocr_mock:
        # OCR only ever mocks on the explicit env flag, and that flag forces every
        # layer offline, so this is the whole-pipeline-mock case.
        reason = "LABEL_JAANO_MOCK is set, so OCR and field extraction are both offline"
    elif gemini_mock:
        reason = ("no GEMINI_API_KEY / GOOGLE_API_KEY is configured, so field "
                  "extraction fell back to canned values")
    else:
        reason = "live OCR and live vision model"

    return {
        "mock": ocr_mock or gemini_mock,
        "ocr_mock": ocr_mock,
        "gemini_mock": gemini_mock,
        "reason": reason,
    }


def _rule_vocabulary():
    """(declaration_ids, symbols, categories) from the currently-loaded rule packs."""
    from rule_engine.loader import load_packs

    try:
        from rule_engine.checks import KNOWN_SYMBOLS
    except Exception:  # noqa: BLE001
        KNOWN_SYMBOLS = {"veg_nonveg_mark", "fssai_logo"}

    packs = load_packs()
    decl_ids: list[str] = []
    categories: set[str] = set(_FALLBACK_CATEGORIES)
    for p in packs:
        for d in p.declarations:
            if d.id not in decl_ids:
                decl_ids.append(d.id)
        for c in (p.applies_when or {}).get("category_in", []) or []:
            categories.add(c)
    return decl_ids, sorted(KNOWN_SYMBOLS), sorted(categories)


def _normalize_images(images) -> list[ImageInput]:
    if images is None:
        return []
    if isinstance(images, (str, bytes, bytearray)):
        return [images]
    return list(images)


def extract_scan_input(
    images,
    reference: Optional[dict] = None,
    context_overrides: Optional[dict] = None,
    mock: Optional[bool] = None,
    category_hint: Optional[str] = None,
) -> dict:
    """Run OCR + Gemini + calibration + fusion; return the scan-input contract dict."""
    imgs = _normalize_images(images)
    if not imgs:
        raise ValueError("extract_scan_input requires at least one image")

    decl_ids, symbols, categories = _rule_vocabulary()

    ocr_results = [run_ocr(img, mock=mock) for img in imgs]
    extraction = extract_fields(imgs, decl_ids, symbols, categories, mock=mock)
    if category_hint:
        extraction.category = category_hint.strip().lower()

    # One scale from the reference image, broadcast to every panel image.
    ref_idx = 0
    if reference and isinstance(reference.get("image"), int):
        ref_idx = max(0, min(reference["image"], len(imgs) - 1))
    scale = estimate_scale(reference, image=imgs[ref_idx])
    calibrations = [scale for _ in imgs]

    return fuse(extraction, ocr_results, calibrations, reference, context_overrides)


def extract_and_evaluate(
    images,
    reference: Optional[dict] = None,
    context_overrides: Optional[dict] = None,
    mock: Optional[bool] = None,
    category_hint: Optional[str] = None,
):
    """Convenience: extract, then score with the engine.

    Returns ``(scan_input_dict, ComplianceReport)``.
    """
    from rule_engine import ScanInput, evaluate_scan

    scan = extract_scan_input(images, reference=reference,
                              context_overrides=context_overrides,
                              mock=mock, category_hint=category_hint)
    report = evaluate_scan(ScanInput.from_dict(scan))
    return scan, report
