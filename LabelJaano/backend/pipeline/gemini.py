"""
Vision-LLM layer — Gemini 2.0 Flash structured extraction.

Sends the label image(s) plus the rule-driven prompt (prompts.py) to Gemini and parses
the strict-JSON reply into a :class:`GeminiExtraction`. Gemini handles *what the label
says* and *which declaration each value is*; the pixel geometry for Rule 8 comes from
OCR (ocr.py) instead.

Lazy + optional: ``import google.generativeai`` happens only on a real call, and the
API key is read from ``GEMINI_API_KEY`` (or ``GOOGLE_API_KEY``) at call time. Pass
``mock=True`` (or set ``LABEL_JAANO_MOCK=1``) to skip the network entirely and return a
deterministic extraction — from a ``<image>.mock.json`` sidecar if present, else a
canned compliant-ish read — so the pipeline is testable with no key and no install.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

from .prompts import build_extraction_prompt
from .types import GeminiExtraction, ImageInput

DEFAULT_MODEL = os.environ.get("LABEL_JAANO_GEMINI_MODEL", "gemini-2.0-flash")


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def extract_fields(
    images: list[ImageInput],
    declaration_ids: list[str],
    symbols: list[str],
    categories: list[str],
    mock: Optional[bool] = None,
    model: str = DEFAULT_MODEL,
) -> GeminiExtraction:
    """Read structured declarations from one or more images of the same product."""
    if mock is None:
        mock = _mock_requested()
    if mock:
        return _mock_extract(images)
    prompt = build_extraction_prompt(declaration_ids, symbols, categories)
    return _gemini_extract(images, prompt, model=model)


def _mock_requested() -> bool:
    if os.environ.get("LABEL_JAANO_MOCK", "").strip() not in ("", "0", "false", "False"):
        return True
    # No key configured -> fall back to mock rather than crashing.
    return not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))


# --------------------------------------------------------------------------- #
# Real call (lazy)
# --------------------------------------------------------------------------- #
def _gemini_extract(images: list[ImageInput], prompt: str, model: str) -> GeminiExtraction:
    try:
        import google.generativeai as genai  # lazy: optional dep
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "google-generativeai is not installed. `pip install google-generativeai`, "
            "or call with mock=True / LABEL_JAANO_MOCK=1 for the offline mock."
        ) from exc

    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise RuntimeError(
            "No Gemini API key. Set GEMINI_API_KEY (or GOOGLE_API_KEY), or use mock mode."
        )
    genai.configure(api_key=key)
    gm = genai.GenerativeModel(model)

    parts: list = [prompt]
    for img in images:
        parts.append(_to_genai_image(img))

    resp = gm.generate_content(
        parts,
        generation_config={"temperature": 0.0, "response_mime_type": "application/json"},
    )
    data = _parse_json(_response_text(resp))
    data.setdefault("model", model)
    return GeminiExtraction.from_dict(data)


def _to_genai_image(image: ImageInput):
    """Turn a path/bytes into a PIL image genai accepts (lazy PIL)."""
    import io

    from PIL import Image  # lazy

    if isinstance(image, (bytes, bytearray)):
        return Image.open(io.BytesIO(image))
    return Image.open(image)


def _response_text(resp) -> str:
    # google-generativeai exposes .text; fall back to candidate parts if needed.
    txt = getattr(resp, "text", None)
    if txt:
        return txt
    try:
        return resp.candidates[0].content.parts[0].text
    except Exception:  # noqa: BLE001
        return ""


def _parse_json(text: str) -> dict:
    """Parse Gemini's reply into a dict, tolerating stray code fences / prose."""
    if not text:
        return {}
    t = text.strip()
    # strip ```json ... ``` fences if the model added them despite instructions
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", t, re.DOTALL)
    if fence:
        t = fence.group(1).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        # last resort: grab the outermost {...}
        start, end = t.find("{"), t.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(t[start:end + 1])
            except json.JSONDecodeError:
                pass
    raise ValueError(f"Gemini did not return valid JSON (got {text[:200]!r}...)")


# --------------------------------------------------------------------------- #
# Mock extraction (no dependencies, no key)
# --------------------------------------------------------------------------- #
def _mock_extract(images: list[ImageInput]) -> GeminiExtraction:
    """Deterministic structured read for tests/demos.

    Uses a ``<image>.mock.json`` sidecar's ``extraction`` block if present on ANY input
    image; otherwise returns a canned compliant packaged-food extraction so the whole
    pipeline -> engine path produces a real report offline.
    """
    for img in images:
        side = _sidecar_for(img)
        if side and side.exists():
            data = json.loads(side.read_text(encoding="utf-8"))
            if "extraction" in data:
                ex = GeminiExtraction.from_dict(data["extraction"])
                ex.model = "mock"
                return ex

    canned = {
        "category": "packaged_food",
        "fields": {
            "manufacturer_details": {"value": "Brand Foods Pvt Ltd, Plot 5, MIDC, Pune, Maharashtra 411001", "panel": "back", "source_image": 0, "confidence": 0.95},
            "generic_name": {"value": "Glucose Biscuits", "panel": "front", "source_image": 0, "confidence": 0.97},
            "net_quantity": {"value": "Net Qty 500 g", "panel": "front", "source_image": 0, "confidence": 0.95},
            "manufacture_date": {"value": "MFG 03/2026", "panel": "back", "source_image": 0, "confidence": 0.9},
            "mrp": {"value": "Maximum Retail Price Rs 45 inclusive of all taxes", "panel": "front", "source_image": 0, "confidence": 0.94},
            "consumer_care": {"value": "Consumer Care: care@brand.com, 1800-123-4567", "panel": "back", "source_image": 0, "confidence": 0.9},
            "ingredients_list": {"value": "Wheat Flour, Sugar, Edible Vegetable Oil, Milk Solids, Salt", "panel": "back", "source_image": 0, "confidence": 0.92},
            "nutritional_info": {"value": "Energy 450 kcal, Protein 7 g per 100 g", "panel": "back", "source_image": 0, "confidence": 0.9},
            "fssai_license": {"value": "FSSAI Lic No 10012345678901", "panel": "back", "source_image": 0, "confidence": 0.95},
            "date_marking": {"value": "Best before 9 months from manufacture. Use by 12/2026", "panel": "back", "source_image": 0, "confidence": 0.9},
            "lot_batch_number": {"value": "Batch No L2026-045", "panel": "back", "source_image": 0, "confidence": 0.9},
        },
        "symbols_detected": ["veg_nonveg_mark", "fssai_logo"],
        "context": {"is_imported": False, "is_single_ingredient": False,
                    "has_additives": False, "has_allergens": False,
                    "dimension_relevant": False},
        "raw_text": ("Glucose Biscuits Net Qty 500 g Maximum Retail Price MRP Rs 45 "
                     "inclusive of all taxes MFG 03/2026 "
                     "Ingredients: Wheat Flour, Sugar, Edible Vegetable Oil, Milk Solids, "
                     "Salt. Nutritional Information per 100 g: Energy 450 kcal, Protein 7 g, "
                     "Carbohydrate 70 g, Fat 15 g. Consumer Care: care@brand.com, "
                     "Toll-free 1800-123-4567. FSSAI Lic No 10012345678901. Use by 12/2026. "
                     "Batch No L2026-045. Marketed by Brand Foods Pvt Ltd, Plot 5, MIDC, "
                     "Pune, Maharashtra 411001."),
        "model": "mock",
    }
    return GeminiExtraction.from_dict(canned)


def _sidecar_for(image: ImageInput) -> Optional[Path]:
    if isinstance(image, (bytes, bytearray)):
        return None
    p = Path(image)
    return p.with_suffix(p.suffix + ".mock.json")
