"""
Intermediate data types for the extraction pipeline.

The pipeline turns raw label photos into the engine's *scan-input contract*. Between
the photo and that contract there are a few intermediate results — OCR words with
pixel boxes, the vision-LLM's structured extraction, and the pixel->mm calibration.
Those live here.

Pure stdlib (dataclasses only) on purpose: this module must import cleanly in an
environment where PaddleOCR / Gemini / OpenCV are NOT installed, so the mock path and
the unit tests run anywhere. The heavy libraries are imported lazily, inside the
functions that actually need them (see ocr.py, gemini.py, calibration.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Union

# An image the pipeline can ingest: a filesystem path or raw bytes (e.g. an upload).
ImageInput = Union[str, bytes]


# --------------------------------------------------------------------------- #
# OCR
# --------------------------------------------------------------------------- #
@dataclass
class OcrWord:
    """One recognized text token with its pixel bounding box.

    bbox is (x, y, w, h) in pixels, origin top-left. ``height`` (glyph height in px)
    is what the Rule 8 font-size calibration multiplies by mm-per-pixel.
    """
    text: str
    bbox: tuple[float, float, float, float]  # x, y, w, h  (pixels)
    confidence: float = 1.0

    @property
    def x(self) -> float: return self.bbox[0]
    @property
    def y(self) -> float: return self.bbox[1]
    @property
    def width(self) -> float: return self.bbox[2]
    @property
    def height(self) -> float: return self.bbox[3]

    @property
    def cx(self) -> float: return self.bbox[0] + self.bbox[2] / 2.0
    @property
    def cy(self) -> float: return self.bbox[1] + self.bbox[3] / 2.0

    def to_dict(self) -> dict:
        return {"text": self.text, "bbox": list(self.bbox), "confidence": self.confidence}


@dataclass
class OcrResult:
    """All OCR output for a single image."""
    words: list[OcrWord] = field(default_factory=list)
    image_width: Optional[int] = None
    image_height: Optional[int] = None
    engine: str = "paddleocr"

    @property
    def full_text(self) -> str:
        """Reading-order-ish concatenation used as the engine's raw_text."""
        return " ".join(w.text for w in self.words if w.text and w.text.strip())


# --------------------------------------------------------------------------- #
# Vision-LLM (Gemini) structured extraction
# --------------------------------------------------------------------------- #
@dataclass
class GeminiField:
    """One declaration value as read by the vision-LLM.

    ``panel`` is a coarse location tag the LLM returns ("front" | "back" | "side" |
    "unknown"); fusion maps it to the engine's panel vocabulary
    ("principal_display_panel" | "back_panel" | ...). ``source_image`` is the index of
    the image the value was read from, so calibration can measure it against the right
    OCR word boxes.
    """
    value: str
    panel: str = "unknown"
    source_image: int = 0
    confidence: float = 0.0

    @staticmethod
    def from_dict(d: Any) -> "GeminiField":
        if isinstance(d, str):
            return GeminiField(value=d)
        d = d or {}
        return GeminiField(
            value=(d.get("value") or "").strip(),
            panel=(d.get("panel") or "unknown").strip().lower(),
            source_image=int(d.get("source_image", 0) or 0),
            confidence=float(d.get("confidence", 0.0) or 0.0),
        )


@dataclass
class GeminiExtraction:
    """The full structured read of one product (possibly across several images)."""
    category: str = "unknown"
    fields: dict[str, GeminiField] = field(default_factory=dict)
    symbols_detected: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    raw_text: str = ""
    model: str = ""

    @staticmethod
    def from_dict(d: dict) -> "GeminiExtraction":
        d = d or {}
        return GeminiExtraction(
            category=(d.get("category") or "unknown").strip().lower(),
            fields={k: GeminiField.from_dict(v) for k, v in (d.get("fields") or {}).items()},
            symbols_detected=list(d.get("symbols_detected") or []),
            context=dict(d.get("context") or {}),
            raw_text=d.get("raw_text") or "",
            model=d.get("model") or "",
        )


# --------------------------------------------------------------------------- #
# Font-size calibration
# --------------------------------------------------------------------------- #
@dataclass
class CalibrationResult:
    """Pixel->mm scale for one image, plus how we got it (for the officer UI)."""
    mm_per_px: Optional[float] = None
    method: str = "none"          # "aruco" | "card" | "manual" | "none"
    confidence: float = 0.0
    detail: str = ""

    @property
    def calibrated(self) -> bool:
        return self.mm_per_px is not None and self.mm_per_px > 0

    def to_dict(self) -> dict:
        return {
            "mm_per_px": self.mm_per_px,
            "method": self.method,
            "confidence": self.confidence,
            "detail": self.detail,
        }
