"""
Label Jaano — Compliance API.

A thin FastAPI layer over the pure-Python rule engine. The engine does all the
judging; this module just exposes it over HTTP so the React dashboard, the Flutter
app, and the consumer mode can all call one endpoint.

Endpoints
---------
    GET  /health              liveness + how many packs are loaded
    GET  /rulepacks           summary of every loaded rule pack
    GET  /rulepacks/{id}      the full raw JSON of one pack (audit the gov rules)
    POST /scan                judge one normalized label -> ComplianceReport
    POST /extract             label photo(s) -> normalized scan-input JSON (OCR+Gemini)
    POST /scan/image          label photo(s) -> ComplianceReport (extract + judge)
    POST /reload              re-read the rulepacks from disk (hot-update gov rules)

Run it (from the backend/ directory):

    uvicorn app.main:app --reload

Interactive docs are then at  http://127.0.0.1:8000/docs
"""
from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware

# Make the sibling ``rule_engine`` package importable whether the app is started
# from backend/ (``uvicorn app.main:app``) or as a module — parents[1] == backend/.
_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from rule_engine import (  # noqa: E402  (import after sys.path tweak)
    ScanInput,
    build_ruleset,
    evaluate,
)
from rule_engine.loader import load_pack_dicts, load_packs  # noqa: E402

from pipeline import extract_and_evaluate, extract_scan_input  # noqa: E402

from .schemas import (  # noqa: E402
    PackInfo,
    ReportOut,
    ScanRequest,
)

__version__ = "0.1.0"

app = FastAPI(
    title="Label Jaano — Compliance API",
    version=__version__,
    description=(
        "Checks packaged-commodity labels against the Legal Metrology (Packaged "
        "Commodities) Rules, 2011 and stacked category packs (e.g. FSSAI food). "
        "Rules are versioned JSON — update the packs and call POST /reload; no "
        "code change or restart needed."
    ),
)

# The dashboard / mobile app live on other origins; allow them all in dev.
# Tighten allow_origins to the deployed frontend URLs before production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Pack caches (loaded once, cleared by POST /reload)
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def get_packs():
    """Parsed Pack objects, cached. Cleared on /reload."""
    return load_packs()


@lru_cache(maxsize=1)
def get_pack_dicts():
    """Raw pack JSON keyed by pack_id, cached. Cleared on /reload."""
    return load_pack_dicts()


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@app.get("/health", tags=["meta"], summary="Liveness check")
def health() -> dict:
    try:
        packs = get_packs()
        return {
            "status": "ok",
            "version": __version__,
            "packs_loaded": len(packs),
            "pack_ids": [p.pack_id for p in packs],
        }
    except Exception as exc:  # rulepacks missing / malformed
        raise HTTPException(status_code=503, detail=f"rulepacks not loadable: {exc}")


@app.get("/rulepacks", response_model=list[PackInfo], tags=["rulepacks"],
         summary="List loaded rule packs")
def list_rulepacks() -> list[PackInfo]:
    return [
        PackInfo(
            pack_id=p.pack_id,
            label=p.label,
            authority=p.authority,
            version=p.version,
            scope=p.scope,
            applies_when=p.applies_when,
            declarations=len(p.declarations),
        )
        for p in get_packs()
    ]


@app.get("/rulepacks/{pack_id}", tags=["rulepacks"],
         summary="Full raw JSON of one rule pack")
def get_rulepack(pack_id: str) -> dict:
    packs = get_pack_dicts()
    if pack_id not in packs:
        raise HTTPException(
            status_code=404,
            detail=f"unknown pack_id '{pack_id}'. known: {sorted(packs)}",
        )
    return packs[pack_id]


@app.post("/scan", response_model=ReportOut, tags=["scan"],
          summary="Check one label for compliance")
def scan(req: ScanRequest) -> dict:
    """Judge one normalized label (OCR + vision-LLM output) and return a full report.

    The engine auto-selects the applicable packs from ``category``: the Legal
    Metrology base pack always applies, and category packs (e.g. FSSAI food) stack
    on top. Every violation cites the exact rule it breaks.
    """
    scan_input = ScanInput.from_dict(req.model_dump())
    try:
        ruleset = build_ruleset(scan_input.category, packs=get_packs())
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"could not build ruleset: {exc}")
    report = evaluate(scan_input, ruleset)
    return report.to_dict()


# --------------------------------------------------------------------------- #
# Image endpoints — run the OCR + Gemini extraction pipeline
# --------------------------------------------------------------------------- #
def _parse_json_form(raw: str | None, field: str):
    """Parse an optional JSON-string form field; 400 on malformed JSON."""
    if raw is None or raw.strip() == "":
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400,
                            detail=f"form field '{field}' is not valid JSON: {exc}")


def _parse_mock_form(raw: str | None):
    """Tri-state mock flag from a form string: True / False / None (auto-detect)."""
    if raw is None or raw.strip() == "":
        return None
    return raw.strip().lower() in ("1", "true", "yes", "on")


async def _read_images(files: list[UploadFile]) -> list[bytes]:
    if not files:
        raise HTTPException(status_code=400, detail="upload at least one image file")
    out: list[bytes] = []
    for f in files:
        data = await f.read()
        if not data:
            raise HTTPException(status_code=400,
                                detail=f"uploaded file '{f.filename}' is empty")
        out.append(data)
    return out


@app.post("/extract", response_model=ScanRequest, tags=["scan"],
          summary="Label photo(s) -> normalized scan-input JSON")
async def extract(
    images: list[UploadFile] = File(..., description="one or more label photos (front, back, ...)"),
    reference: str | None = Form(None, description='calibration reference JSON, e.g. {"type":"card","width_mm":85.6,"bbox":[x,y,w,h]}'),
    context: str | None = Form(None, description="officer context-override JSON (wins over auto-detected)"),
    category: str | None = Form(None, description="force the product category"),
    mock: str | None = Form(None, description="true = offline mock; omit to auto-detect from installed deps/keys"),
) -> dict:
    """Run OCR + Gemini + calibration + fusion and return the scan-input contract.

    This is the same normalized JSON that ``POST /scan`` accepts, so you can inspect or
    correct it before scoring. Needs the extraction extras installed and a Gemini key —
    otherwise pass ``mock=true`` (or set ``LABEL_JAANO_MOCK=1``) for the offline read.
    """
    imgs = await _read_images(images)
    ref = _parse_json_form(reference, "reference")
    ctx = _parse_json_form(context, "context")
    try:
        return await run_in_threadpool(
            extract_scan_input, imgs,
            reference=ref, context_overrides=ctx,
            mock=_parse_mock_form(mock), category_hint=category,
        )
    except RuntimeError as exc:  # missing optional dep / no API key
        raise HTTPException(status_code=503, detail=str(exc))


@app.post("/scan/image", response_model=ReportOut, tags=["scan"],
          summary="Label photo(s) -> compliance report (extract + judge)")
async def scan_image(
    images: list[UploadFile] = File(..., description="one or more label photos (front, back, ...)"),
    reference: str | None = Form(None, description='calibration reference JSON for Rule 8 font-height'),
    context: str | None = Form(None, description="officer context-override JSON"),
    category: str | None = Form(None, description="force the product category"),
    mock: str | None = Form(None, description="true = offline mock; omit to auto-detect"),
) -> dict:
    """One-shot: extract the label photo(s) and score them against the rule packs.

    Equivalent to ``POST /extract`` piped into ``POST /scan`` — returns the full
    ComplianceReport with every violation citing the rule it breaks.
    """
    imgs = await _read_images(images)
    ref = _parse_json_form(reference, "reference")
    ctx = _parse_json_form(context, "context")
    try:
        _scan, report = await run_in_threadpool(
            extract_and_evaluate, imgs,
            reference=ref, context_overrides=ctx,
            mock=_parse_mock_form(mock), category_hint=category,
        )
    except RuntimeError as exc:  # missing optional dep / no API key
        raise HTTPException(status_code=503, detail=str(exc))
    return report.to_dict()


@app.post("/reload", tags=["meta"],
          summary="Reload rule packs from disk (hot gov-rule update)")
def reload_packs() -> dict:
    """Drop the cached packs so the next request re-reads ``rulepacks/`` from disk.

    Lets you push a gazette update — edit or drop in a new JSON pack — and have it
    take effect without restarting the server.
    """
    get_packs.cache_clear()
    get_pack_dicts.cache_clear()
    packs = get_packs()  # eagerly reload so errors surface here, not on next scan
    return {
        "status": "reloaded",
        "packs_loaded": len(packs),
        "pack_ids": [p.pack_id for p in packs],
    }
