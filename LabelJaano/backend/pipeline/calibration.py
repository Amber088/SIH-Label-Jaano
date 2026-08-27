"""
Font-size calibration — the pixel->millimetre bridge for Rule 8.

Rule 8 of the LMPC Rules mandates minimum letter/numeral *heights in millimetres*,
scaled by the principal-display-panel area. A photo only gives pixels, so we need a
known-size reference in the frame to recover the scale:

    mm_per_px = (physical size of the reference in mm) / (its size in pixels)

Supported references (``reference`` dict passed to :func:`estimate_scale`):
    {"type": "manual", "mm_per_px": 0.10}
    {"type": "card",   "width_mm": 85.6, "bbox": [x, y, w, h]}   # ID/credit card = 85.6mm
    {"type": "aruco",  "marker_length_mm": 50, "dict": "DICT_4X4_50"}  # detected in-image

With a scale in hand, :func:`measure_height_mm` converts a declaration's glyph pixel
height (from OCR word boxes) to mm, and :func:`pdp_area_cm2` converts the front-panel
box to cm² for the Rule 8 table lookup. The officer can always override the result.

Only the ArUco path needs OpenCV (lazy-imported); "card"/"manual" and all the
measurement math are pure stdlib, so they run and unit-test anywhere.
"""
from __future__ import annotations

import re
import statistics
from typing import Optional

from .types import CalibrationResult, ImageInput, OcrWord

# Common physical reference widths (mm) for convenience / documentation.
CREDIT_CARD_WIDTH_MM = 85.6
CREDIT_CARD_HEIGHT_MM = 53.98


# --------------------------------------------------------------------------- #
# Scale estimation
# --------------------------------------------------------------------------- #
def estimate_scale(reference: Optional[dict], image: Optional[ImageInput] = None) -> CalibrationResult:
    """Compute mm-per-pixel from a reference descriptor (see module docstring)."""
    if not reference:
        return CalibrationResult(method="none", detail="no reference object provided")

    rtype = (reference.get("type") or "").lower()

    if rtype == "manual":
        mm_per_px = reference.get("mm_per_px")
        if not mm_per_px or mm_per_px <= 0:
            return CalibrationResult(method="manual", detail="manual mm_per_px missing/invalid")
        return CalibrationResult(float(mm_per_px), "manual", 1.0, "operator-supplied scale")

    if rtype == "card":
        bbox = reference.get("bbox")
        width_mm = float(reference.get("width_mm", CREDIT_CARD_WIDTH_MM))
        if not bbox or len(bbox) < 3 or float(bbox[2]) <= 0:
            return CalibrationResult(method="card", detail="card bbox missing/invalid")
        px = float(bbox[2])  # width in px
        mm_per_px = width_mm / px
        return CalibrationResult(mm_per_px, "card", 0.9,
                                 f"{width_mm}mm card = {px:.0f}px")

    if rtype == "aruco":
        return _aruco_scale(reference, image)

    return CalibrationResult(method="none", detail=f"unknown reference type '{rtype}'")


def _aruco_scale(reference: dict, image: Optional[ImageInput]) -> CalibrationResult:
    if image is None:
        return CalibrationResult(method="aruco", detail="aruco needs the image")
    try:
        import cv2  # lazy: optional dep
        import numpy as np  # lazy
    except ImportError as exc:  # pragma: no cover - environment dependent
        return CalibrationResult(method="aruco",
                                 detail=f"OpenCV not installed for ArUco ({exc})")

    arr = _load_bgr(image, cv2, np)
    if arr is None:
        return CalibrationResult(method="aruco", detail="could not read image")
    gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)

    dict_name = reference.get("dict", "DICT_4X4_50")
    marker_len_mm = float(reference.get("marker_length_mm", 50.0))
    corners = _detect_aruco(cv2, gray, dict_name)
    if not corners:
        return CalibrationResult(method="aruco", confidence=0.0,
                                 detail="no ArUco marker detected")

    # Average the four side lengths of the first detected marker (pixels).
    pts = corners[0].reshape(-1, 2)
    sides = [
        _dist(pts[i], pts[(i + 1) % 4]) for i in range(4)
    ]
    side_px = sum(sides) / len(sides)
    if side_px <= 0:
        return CalibrationResult(method="aruco", detail="degenerate marker")
    mm_per_px = marker_len_mm / side_px
    return CalibrationResult(mm_per_px, "aruco", 0.95,
                             f"{marker_len_mm}mm marker = {side_px:.0f}px")


def _detect_aruco(cv2, gray, dict_name: str):
    """Detect ArUco markers across OpenCV API versions; return list of corner arrays."""
    aruco = cv2.aruco
    dict_id = getattr(aruco, dict_name, getattr(aruco, "DICT_4X4_50"))
    try:
        adict = aruco.getPredefinedDictionary(dict_id)
    except Exception:  # older API  # noqa: BLE001
        adict = aruco.Dictionary_get(dict_id)
    # New API (>=4.7): ArucoDetector
    try:
        params = aruco.DetectorParameters()
        detector = aruco.ArucoDetector(adict, params)
        corners, ids, _ = detector.detectMarkers(gray)
    except Exception:  # older API  # noqa: BLE001
        params = aruco.DetectorParameters_create()
        corners, ids, _ = aruco.detectMarkers(gray, adict, parameters=params)
    return list(corners) if corners is not None else []


def _load_bgr(image: ImageInput, cv2, np):
    if isinstance(image, (bytes, bytearray)):
        buf = np.frombuffer(image, dtype=np.uint8)
        return cv2.imdecode(buf, cv2.IMREAD_COLOR)
    return cv2.imread(str(image))


def _dist(a, b) -> float:
    return ((float(a[0]) - float(b[0])) ** 2 + (float(a[1]) - float(b[1])) ** 2) ** 0.5


# --------------------------------------------------------------------------- #
# Measurement (pure stdlib — unit-testable everywhere)
# --------------------------------------------------------------------------- #
_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall((text or "").lower()))


def measure_height_mm(
    field_value: str,
    ocr_words: list[OcrWord],
    mm_per_px: Optional[float],
) -> Optional[float]:
    """Median glyph height (mm) of the OCR words that make up ``field_value``.

    Matches OCR words whose text is a token of the declaration value, takes the median
    of their pixel heights (robust to a stray tall/short box), and scales to mm. Returns
    ``None`` if there is no scale or no matching words — the engine then SKIPs the
    font-size check rather than guessing.
    """
    if not mm_per_px or mm_per_px <= 0 or not ocr_words:
        return None
    wanted = _tokens(field_value)
    if not wanted:
        return None

    heights = [w.height for w in ocr_words
               if len(w.text.strip()) >= 2 and _tokens(w.text) & wanted and w.height > 0]
    if not heights:  # fall back to single-char tokens (e.g. unit "g")
        heights = [w.height for w in ocr_words
                   if _tokens(w.text) & wanted and w.height > 0]
    if not heights:
        return None
    return round(statistics.median(heights) * mm_per_px, 2)


def pdp_area_cm2(pdp_bbox_px, mm_per_px: Optional[float]) -> Optional[float]:
    """Front-panel area in cm² from its pixel box + scale (for the Rule 8 table)."""
    if not mm_per_px or mm_per_px <= 0 or not pdp_bbox_px or len(pdp_bbox_px) < 4:
        return None
    w_mm = float(pdp_bbox_px[2]) * mm_per_px
    h_mm = float(pdp_bbox_px[3]) * mm_per_px
    return round((w_mm * h_mm) / 100.0, 1)  # mm² -> cm²
