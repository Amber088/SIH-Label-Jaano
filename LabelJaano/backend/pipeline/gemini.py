"""
Vision-LLM layer — Gemini structured extraction.

Sends the label image(s) plus the rule-driven prompt (prompts.py) to Gemini and parses
the strict-JSON reply into a :class:`GeminiExtraction`. Gemini handles *what the label
says* and *which declaration each value is*; the pixel geometry for Rule 8 comes from
OCR (ocr.py) instead.

**Two SDK backends, chosen at call time.** ``google-genai`` (the current SDK) is
preferred; ``google-generativeai`` (retired mid-2025, gRPC-based, ~4x slower on a
cold call and noisy with deprecation warnings) is the fallback so an existing install
keeps working. Neither is imported until a real call happens, so the rule engine and
the mock path stay dependency-free. Set ``LABEL_JAANO_GEMINI_SDK=legacy|new`` to pin
one explicitly — useful for reproducing a backend-specific bug.

The API key is read from ``GEMINI_API_KEY`` (or ``GOOGLE_API_KEY``) at call time. Pass
``mock=True`` (or set ``LABEL_JAANO_MOCK=1``) to skip the network entirely and return a
deterministic extraction — from a ``<image>.mock.json`` sidecar if present, else a
canned compliant-ish read — so the pipeline is testable with no key and no install.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional

from .prompts import build_extraction_prompt
from .types import GeminiExtraction, ImageInput

#: The application (app.logconfig) owns the handlers; this layer only emits. Keeping it
#: to a bare getLogger means the pipeline has no import dependency on the web app.
_log = logging.getLogger("labeljaano.gemini")

DEFAULT_MODEL = os.environ.get("LABEL_JAANO_GEMINI_MODEL", "gemini-3.6-flash")

# Which SDK to use: "new" (google-genai), "legacy" (google-generativeai), or "" = auto.
_SDK_PREFERENCE = os.environ.get("LABEL_JAANO_GEMINI_SDK", "").strip().lower()

#: How long to let one vision call run before giving up, in seconds.
#:
#: This exists because neither SDK fails fast by default: both wrap the call in a retry
#: policy with backoff, so an unroutable network, an unrecognised model name or a
#: rejected key can keep a request alive for minutes. The mobile client gives up at 90
#: seconds and shows "the server took too long" — a message that says nothing about what
#: went wrong. Bounding the call *below* that budget means the server loses the race on
#: purpose and gets to answer with the real reason instead.
TIMEOUT_ENV = "LABEL_JAANO_GEMINI_TIMEOUT"
DEFAULT_TIMEOUT_SECONDS = 60.0


def _timeout_seconds() -> float:
    """The per-call budget. An unparseable or non-positive value keeps the default."""
    raw = os.environ.get(TIMEOUT_ENV, "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        seconds = float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    return seconds if seconds > 0 else DEFAULT_TIMEOUT_SECONDS


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
# Real call (lazy, dual-SDK)
# --------------------------------------------------------------------------- #
def _api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise RuntimeError(
            "No Gemini API key. Set GEMINI_API_KEY (or GOOGLE_API_KEY), or use mock mode."
        )
    return key


def _call_with_deadline(backend, images, prompt, model, key) -> str:
    """Run *backend* on a daemon thread and abandon it if it overruns the budget.

    The per-SDK ``timeout`` arguments are set too, but they are not trusted to be the
    whole story: both SDKs layer their own retry-with-backoff underneath, and a host
    that swallows packets, a model name the endpoint does not know, or a key it rejects
    can all keep a call alive far longer than the number handed to it. Measured here on
    a blocked network, the retired SDK was still going after four minutes against a
    sixty-second budget. So the guarantee lives in the one place that does not depend on
    a library honouring anything: our own wall clock.

    A bare daemon thread rather than a ``ThreadPoolExecutor`` on purpose. The executor
    registers an ``atexit`` hook that *joins* its workers, so an abandoned call would
    still stall interpreter shutdown — measured at ten seconds for a ten-second stuck
    call, which would mean Ctrl-C on uvicorn hanging on a request the client gave up on
    long ago. A daemon thread is not joined at exit, so the overrun costs one leaked
    socket and nothing else; whatever it eventually returns is simply dropped.
    """
    budget = _timeout_seconds()
    done: list[str] = []
    failed: list[BaseException] = []

    def run() -> None:
        try:
            done.append(backend(images, prompt, model, key))
        except BaseException as exc:  # noqa: BLE001 - re-raised on the calling thread
            failed.append(exc)

    thread = threading.Thread(target=run, name="gemini-call", daemon=True)
    thread.start()
    thread.join(budget)

    if thread.is_alive():
        raise RuntimeError(
            f"The vision model did not answer within {budget:g}s. Either the network "
            f"cannot reach the API, or {model!r} is not a model this key can call. "
            f"Raise {TIMEOUT_ENV} if the call is merely slow, or use mock mode."
        )
    if failed:
        # The SDK's own error, not a wrapper: "API key not valid" is worth reading.
        raise failed[0]
    return done[0]


def _gemini_extract(images: list[ImageInput], prompt: str, model: str) -> GeminiExtraction:
    """Call whichever SDK is installed and normalise the reply.

    Both backends are asked for ``response_mime_type=application/json`` at
    ``temperature=0`` — a compliance read must be reproducible, and a label is not a
    creative writing prompt.
    """
    key = _api_key()
    backend = _select_backend()
    _log.info("gemini call model=%s sdk=%s timeout=%gs images=%d",
              model, backend.__name__, _timeout_seconds(), len(images))
    started = time.perf_counter()
    try:
        text = _call_with_deadline(backend, images, prompt, model, key)
    except Exception as exc:
        # The reason the phone never got — the timeout message or the SDK's own
        # "API key not valid" — lands in the server log at ERROR before it re-raises.
        _log.error("gemini failed model=%s after %.1fs: %s",
                   model, time.perf_counter() - started, exc)
        raise
    _log.info("gemini ok model=%s in %.1fs (%d chars)",
              model, time.perf_counter() - started, len(text))
    data = _parse_json(text)
    data.setdefault("model", model)
    return GeminiExtraction.from_dict(data)


def _select_backend():
    """Return the callable for the best available SDK, honouring the env pin.

    Auto mode prefers the current SDK. The error raised when nothing is installed
    names the package to install rather than the module that failed to import,
    because ``No module named 'google.genai'`` does not tell you what to type.
    """
    want_new = _SDK_PREFERENCE in ("", "new", "genai", "google-genai")
    want_legacy = _SDK_PREFERENCE in ("", "legacy", "old", "generativeai",
                                      "google-generativeai")

    if want_new and _module_available("google.genai"):
        return _extract_via_genai
    if want_legacy and _module_available("google.generativeai"):
        return _extract_via_generativeai

    if _SDK_PREFERENCE and not (want_new or want_legacy):
        raise RuntimeError(
            f"LABEL_JAANO_GEMINI_SDK={_SDK_PREFERENCE!r} is not a known backend. "
            "Use 'new' (google-genai) or 'legacy' (google-generativeai)."
        )
    pinned = f" (pinned to {_SDK_PREFERENCE!r} by LABEL_JAANO_GEMINI_SDK)" if _SDK_PREFERENCE else ""
    raise RuntimeError(
        f"No Gemini SDK is installed{pinned}. Run `pip install google-genai`, "
        "or call with mock=True / LABEL_JAANO_MOCK=1 for the offline mock."
    )


def _module_available(name: str) -> bool:
    """Whether *name* can be imported, without importing it.

    Checks ``sys.modules`` before probing the filesystem: an already-imported module
    is available by definition, and ``find_spec`` raises ValueError for a module whose
    ``__spec__`` is None — which is exactly the shape of a test double.
    """
    import importlib.util
    import sys

    if name in sys.modules:
        return True
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, AttributeError, ValueError):
        # A broken or namespace-less parent package raises rather than returning None.
        return False


def _extract_via_genai(images: list[ImageInput], prompt: str, model: str, key: str) -> str:
    """Current SDK (``google-genai``). Uploads raw bytes — no PIL decode needed."""
    from google import genai
    from google.genai import types

    # HttpOptions.timeout is milliseconds, unlike every other timeout in this codebase.
    client = genai.Client(
        api_key=key,
        http_options=types.HttpOptions(timeout=int(_timeout_seconds() * 1000)),
    )
    contents: list = [prompt]
    for img in images:
        raw, mime = _image_bytes_and_mime(img)
        contents.append(types.Part.from_bytes(data=raw, mime_type=mime))

    resp = client.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            temperature=0.0,
            response_mime_type="application/json",
        ),
    )
    return _response_text(resp)


def _extract_via_generativeai(images: list[ImageInput], prompt: str, model: str,
                              key: str) -> str:
    """Retired SDK (``google-generativeai``). Kept so existing installs still run."""
    import google.generativeai as genai

    genai.configure(api_key=key)
    gm = genai.GenerativeModel(model)

    parts: list = [prompt]
    for img in images:
        parts.append(_to_genai_image(img))

    resp = gm.generate_content(
        parts,
        generation_config={"temperature": 0.0, "response_mime_type": "application/json"},
        # Seconds here, and it bounds the *whole* call including the SDK's own retries.
        # Without it this backend will retry an unroutable host or an unknown model for
        # minutes — long past the point where the phone has already given up.
        request_options={"timeout": _timeout_seconds()},
    )
    return _response_text(resp)


# Magic-byte prefixes, longest first so the ISO-BMFF probe does not shadow a real match.
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
)


def _sniff_mime(raw: bytes) -> str:
    """Detect the image type from its leading bytes.

    Sniffing beats trusting the filename: the app posts camera bytes with no name at
    all, and an Android gallery pick can hand us a ``.jpg`` that is really a HEIC.
    Falls back to JPEG, which is what every phone camera actually produces.
    """
    for prefix, mime in _MAGIC:
        if raw.startswith(prefix):
            return mime
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    if raw[4:8] == b"ftyp":  # ISO-BMFF container: HEIC/HEIF/AVIF
        brand = raw[8:12]
        if brand in (b"avif", b"avis"):
            return "image/avif"
        return "image/heic"
    return "image/jpeg"


def _image_bytes_and_mime(image: ImageInput) -> tuple[bytes, str]:
    if isinstance(image, (bytes, bytearray)):
        raw = bytes(image)
    else:
        raw = Path(image).read_bytes()
    return raw, _sniff_mime(raw)


def _to_genai_image(image: ImageInput):
    """Turn a path/bytes into a PIL image the legacy SDK accepts (lazy PIL)."""
    import io

    from PIL import Image  # lazy

    if isinstance(image, (bytes, bytearray)):
        return Image.open(io.BytesIO(image))
    return Image.open(image)


def _response_text(resp) -> str:
    # Both SDKs expose .text; fall back to candidate parts if it is empty (e.g. the
    # reply was cut off by a finish_reason other than STOP).
    txt = getattr(resp, "text", None)
    if txt:
        return txt
    try:
        return resp.candidates[0].content.parts[0].text or ""
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
