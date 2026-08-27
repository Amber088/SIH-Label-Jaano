"""
OCR layer — PaddleOCR wrapper producing word-level pixel boxes.

Why OCR *and* a vision-LLM? The LLM (gemini.py) is great at *what the text says* and
which declaration it is, but is unreliable at *precise pixel geometry*. Rule 8 of the
LMPC Rules mandates a minimum letter height in millimetres, so we need real pixel
boxes — that is what PaddleOCR provides here, and what calibration.py converts to mm.

Loading is lazy: ``import paddleocr`` (and numpy/PIL) happens the first time a real OCR
runs, so this module imports fine where PaddleOCR is not installed. For tests, demos,
and running before the heavy install, pass ``mock=True`` (or set env
``LABEL_JAANO_MOCK=1``) to get deterministic synthetic boxes — optionally driven by a
``<image>.mock.json`` sidecar so you can craft fixtures.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from .types import ImageInput, OcrResult, OcrWord

_PADDLE_SINGLETON = None  # PaddleOCR model is expensive; build once, reuse.


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def run_ocr(image: ImageInput, mock: Optional[bool] = None, lang: str = "en") -> OcrResult:
    """OCR one image into an :class:`OcrResult` of word boxes.

    ``image`` may be a path or raw bytes. ``mock=None`` (default) auto-detects: it uses
    the mock engine when PaddleOCR is not importable or ``LABEL_JAANO_MOCK`` is set.
    """
    if mock is None:
        mock = _mock_requested()
    if mock:
        return _mock_ocr(image)
    try:
        return _paddle_ocr(image, lang=lang)
    except (ImportError, RuntimeError):
        # The real OCR extras (paddleocr / paddlepaddle / numpy) aren't installed.
        # Degrade gracefully instead of failing the whole scan: return no word boxes
        # so the pipeline continues on Gemini alone. Without pixel geometry the Rule 8
        # font-height check simply SKIPs (see calibration.measure_height_mm) rather
        # than 500-ing the request.
        return OcrResult(words=[], image_width=None, image_height=None,
                         engine="unavailable")
    except Exception:  # noqa: BLE001 - OCR is best-effort; never let it fail the scan
        # The extras are present but OCR failed on THIS image (corrupt/unsupported
        # bytes, decode error, an engine hiccup). OCR only supplies pixel geometry
        # for the Rule 8 font check, so degrade to "no boxes" and let Gemini carry
        # the read — the font check SKIPs, everything else proceeds. Better a partial
        # report than a 500.
        return OcrResult(words=[], image_width=None, image_height=None,
                         engine="error")


def _mock_requested() -> bool:
    return os.environ.get("LABEL_JAANO_MOCK", "").strip() not in ("", "0", "false", "False")


# --------------------------------------------------------------------------- #
# Real engine (lazy)
# --------------------------------------------------------------------------- #
def _get_paddle(lang: str):
    global _PADDLE_SINGLETON
    if _PADDLE_SINGLETON is None:
        try:
            from paddleocr import PaddleOCR  # lazy: heavy import
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "PaddleOCR is not installed. Install the extraction extras "
                "(`pip install paddleocr paddlepaddle`), or call the pipeline with "
                "mock=True / LABEL_JAANO_MOCK=1 to use the dependency-free mock engine."
            ) from exc
        _PADDLE_SINGLETON = PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)
    return _PADDLE_SINGLETON


def _load_image_array(image: ImageInput):
    """Decode a path or bytes into an RGB numpy array (lazy numpy/PIL)."""
    import io

    import numpy as np  # lazy
    from PIL import Image  # lazy

    if isinstance(image, (bytes, bytearray)):
        img = Image.open(io.BytesIO(image))
    else:
        img = Image.open(image)
    img = img.convert("RGB")
    return np.array(img)


def _paddle_ocr(image: ImageInput, lang: str = "en") -> OcrResult:
    import numpy as np  # lazy

    arr = _load_image_array(image)
    h, w = arr.shape[0], arr.shape[1]
    ocr = _get_paddle(lang)
    raw = ocr.ocr(arr, cls=True)

    words: list[OcrWord] = []
    # PaddleOCR returns [[ [box(4 pts), (text, conf)], ... ]] (one list per image).
    lines = raw[0] if raw and isinstance(raw, list) and raw[0] is not None else []
    for entry in lines:
        try:
            box, (text, conf) = entry[0], entry[1]
        except (ValueError, TypeError, IndexError):
            continue
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        x, y = min(xs), min(ys)
        bw, bh = max(xs) - x, max(ys) - y
        words.append(OcrWord(text=text, bbox=(x, y, bw, bh), confidence=float(conf)))

    return OcrResult(words=words, image_width=w, image_height=h, engine="paddleocr")


# --------------------------------------------------------------------------- #
# Mock engine (no dependencies)
# --------------------------------------------------------------------------- #
def _mock_ocr(image: ImageInput) -> OcrResult:
    """Deterministic OCR for tests/demos.

    If a ``<image>.mock.json`` sidecar exists next to the image path, its
    ``ocr_words`` drive the result (each: {"text","bbox":[x,y,w,h],"confidence"}) and
    optional ``image_width``/``image_height``. Otherwise a small synthetic set of word
    boxes is returned so calibration/font-height have something to measure.
    """
    sidecar = _sidecar_for(image)
    if sidecar and sidecar.exists():
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        words = [
            OcrWord(
                text=w.get("text", ""),
                bbox=tuple(w.get("bbox", [0, 0, 0, 0])),  # type: ignore[arg-type]
                confidence=float(w.get("confidence", 0.99)),
            )
            for w in data.get("ocr_words", [])
        ]
        return OcrResult(
            words=words,
            image_width=data.get("image_width", 1000),
            image_height=data.get("image_height", 1500),
            engine="mock",
        )

    # Fallback synthetic label: a couple of measurable tokens + a reference card.
    words = [
        OcrWord("Net", (100, 200, 70, 34), 0.98),
        OcrWord("Qty", (180, 200, 70, 34), 0.98),
        OcrWord("500", (260, 198, 80, 38), 0.98),
        OcrWord("g", (350, 200, 24, 34), 0.98),
        OcrWord("MRP", (100, 300, 90, 40), 0.97),
        OcrWord("Rs", (200, 300, 55, 40), 0.97),
        OcrWord("45", (260, 300, 60, 40), 0.97),
    ]
    return OcrResult(words=words, image_width=1000, image_height=1500, engine="mock")


def _sidecar_for(image: ImageInput) -> Optional[Path]:
    if isinstance(image, (bytes, bytearray)):
        return None
    p = Path(image)
    return p.with_suffix(p.suffix + ".mock.json")
