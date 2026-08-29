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
    POST /reload              re-read the rulepacks from disk (admin)

    POST /auth/register       create an account (consumer, or officer + code)
    POST /auth/login          credentials -> bearer token
    POST /auth/refresh        slide an active session forward
    GET  /auth/me             the signed-in account
    GET  /auth/config         which sign-up options this server offers

    GET  /scans               inspection history (own, or all for an officer)
    GET  /scans/{id}          one stored inspection + its verbatim report
    DELETE /scans/{id}        delete a stored inspection
    POST /scans/{id}/share    short-lived link that opens the report without a login
    GET  /scans/{id}/report.html   print-ready report (Print -> Save as PDF)
    GET  /stats               dashboard aggregates over the accessible corpus

Anonymous use is a supported mode, not a degraded one: every scanning endpoint works
with no token at all and simply does not record the scan. Signing in adds history,
export and aggregates on top. See :mod:`app.deps` for the access model.

Run it (from the backend/ directory):

    uvicorn app.main:app --reload

Interactive docs are then at  http://127.0.0.1:8000/docs
"""
from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
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

from pipeline import (  # noqa: E402
    extract_and_evaluate,
    extract_scan_input,
    resolve_mock_mode,
)

import store  # noqa: E402
from auth.roles import Permission  # noqa: E402
from store.users import User  # noqa: E402

from . import deps  # noqa: E402
from .routers import auth_routes, history  # noqa: E402

from .schemas import (  # noqa: E402
    PackInfo,
    SavedReportOut,
    ScanRequest,
)

__version__ = "0.2.0"

app = FastAPI(
    title="Label Jaano — Compliance API",
    version=__version__,
    description=(
        "Checks packaged-commodity labels against the Legal Metrology (Packaged "
        "Commodities) Rules, 2011 and stacked category packs (e.g. FSSAI food). "
        "Rules are versioned JSON — update the packs and call POST /reload; no "
        "code change or restart needed.\n\n"
        "Scanning needs no account. Sign in to keep a history, export a print-ready "
        "inspection report, and (as an officer) see aggregates across every scan."
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

app.include_router(auth_routes.router)
app.include_router(history.router)


@app.on_event("startup")
def _startup() -> None:
    """Open the history database. Never fatal — see :func:`deps.init_persistence`."""
    deps.init_persistence()


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
            # Lets a client decide up front whether to offer sign-in and history at
            # all, instead of discovering it from a 503 mid-flow.
            "history_available": deps.persistence_ready(),
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


@app.post("/scan", response_model=SavedReportOut, tags=["scan"],
          summary="Check one label for compliance")
def scan(
    req: ScanRequest,
    user: Optional[User] = Depends(deps.optional_user),
    save: bool = True,
    product_name: Optional[str] = None,
    note: Optional[str] = None,
    location: Optional[str] = None,
) -> dict:
    """Judge one normalized label (OCR + vision-LLM output) and return a full report.

    The engine auto-selects the applicable packs from ``category``: the Legal
    Metrology base pack always applies, and category packs (e.g. FSSAI food) stack
    on top. Every violation cites the exact rule it breaks.

    Works with no token. When you are signed in the report is filed and
    ``scan_id`` comes back populated, so it can be fetched or exported later; pass
    ``save=false`` to get the verdict without recording it.
    """
    scan_input = ScanInput.from_dict(req.model_dump())
    try:
        ruleset = build_ruleset(scan_input.category, packs=get_packs())
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"could not build ruleset: {exc}")
    report = evaluate(scan_input, ruleset)
    payload = report.to_dict()
    return _persist(
        payload, user=user, save=save, source="json", mock=False,
        product_name=product_name, note=note, location=location,
        scan_input=req.model_dump(),
    )


# --------------------------------------------------------------------------- #
# Persistence hook shared by the scanning endpoints
# --------------------------------------------------------------------------- #
def _persist(
    payload: dict,
    *,
    user: Optional[User],
    save: bool,
    source: str,
    mock: bool,
    product_name: Optional[str] = None,
    note: Optional[str] = None,
    location: Optional[str] = None,
    scan_input: Optional[dict] = None,
) -> dict:
    """File a report if we can, and annotate the response with whether we did.

    Two properties this deliberately guarantees:

    * **Anonymous scanning still works.** No user, no database, or ``save=false``
      simply returns the report with ``saved: false``. Requiring an account to get a
      verdict would break consumer mode, which is half the product.
    * **A storage failure never costs the caller their verdict.** The scan already
      succeeded; the report is in hand. Turning a disk-full error into a 500 would
      throw away good work to report a bookkeeping problem, so the exception is
      swallowed, logged, and reported honestly as ``saved: false``.
    """
    out = dict(payload)
    out["scan_id"] = None
    out["saved"] = False

    if not (save and user is not None and deps.persistence_ready()):
        return out

    try:
        row = store.save_scan(
            payload,
            user_id=user.id,
            source=source,
            mock=mock,
            product_name=product_name,
            note=note,
            location=location,
            scan_input=scan_input,
        )
        out["scan_id"] = row.id
        out["saved"] = True
    except Exception as exc:  # noqa: BLE001 - never fail a good scan over history
        # stderr so it lands in the log next to the other diagnostics rather than in
        # whatever is parsing stdout.
        print(f"[label-jaano] WARNING: could not record scan: {exc}", file=sys.stderr)
    return out


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


def _parse_bool_form(raw: str | None):
    """Tri-state boolean from a form string: True / False / None (not supplied).

    The three states matter. ``None`` is not the same as ``False``: for ``mock`` it
    means "auto-detect", and for ``save`` it means "use the default", so collapsing
    them would either force live extraction on a machine that cannot do it, or stop
    recording history for every client that omits the field.
    """
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
            mock=_parse_bool_form(mock), category_hint=category,
        )
    except RuntimeError as exc:  # missing optional dep / no API key
        raise HTTPException(status_code=503, detail=str(exc))


@app.post("/scan/image", response_model=SavedReportOut, tags=["scan"],
          summary="Label photo(s) -> compliance report (extract + judge)")
async def scan_image(
    images: list[UploadFile] = File(..., description="one or more label photos (front, back, ...)"),
    reference: str | None = Form(None, description='calibration reference JSON for Rule 8 font-height'),
    context: str | None = Form(None, description="officer context-override JSON"),
    category: str | None = Form(None, description="force the product category"),
    mock: str | None = Form(None, description="true = offline mock; omit to auto-detect"),
    product_name: str | None = Form(None, description="label this inspection in history"),
    note: str | None = Form(None, description="officer's note, stored with the scan"),
    location: str | None = Form(None, description="place of inspection"),
    save: str | None = Form(None, description="false = return the verdict without recording it"),
    user: Optional[User] = Depends(deps.optional_user),
) -> dict:
    """One-shot: extract the label photo(s) and score them against the rule packs.

    Equivalent to ``POST /extract`` piped into ``POST /scan`` — returns the full
    ComplianceReport with every violation citing the rule it breaks.

    Works with no token. When you are signed in the report is filed against your
    account and ``scan_id`` comes back populated.
    """
    imgs = await _read_images(images)
    ref = _parse_json_form(reference, "reference")
    ctx = _parse_json_form(context, "context")
    requested_mock = _parse_bool_form(mock)

    # Ask *before* extracting what path this run will take, so the stored report
    # records how its values were obtained rather than what we hoped for. On a box
    # with no Gemini key this comes back mock=True, and the printed report says so.
    provenance = resolve_mock_mode(requested_mock)

    try:
        scan_input, report = await run_in_threadpool(
            extract_and_evaluate, imgs,
            reference=ref, context_overrides=ctx,
            mock=requested_mock, category_hint=category,
        )
    except RuntimeError as exc:  # missing optional dep / no API key
        raise HTTPException(status_code=503, detail=str(exc))

    payload = report.to_dict()
    out = _persist(
        payload, user=user,
        save=_parse_bool_form(save) is not False,
        source="image", mock=bool(provenance["mock"]),
        product_name=product_name, note=note, location=location,
        scan_input=scan_input,
    )
    # Surfaced on every response, saved or not: a client showing a verdict built on
    # canned values must be able to say so without a second request.
    out["extraction"] = provenance
    return out


@app.post("/reload", tags=["meta"],
          summary="Reload rule packs from disk (admin)")
def reload_packs(
    _: User = Depends(deps.require_permission(Permission.RELOAD_RULEPACKS)),
) -> dict:
    """Drop the cached packs so the next request re-reads ``rulepacks/`` from disk.

    Lets you push a gazette update — edit or drop in a new JSON pack — and have it
    take effect without restarting the server.

    Admin-only, and it was not always so: an open endpoint that swaps out the rules
    every verdict is measured against is a bigger lever than any single scan. Note the
    consequence for a no-database deployment — with ``LABEL_JAANO_NO_DB`` set there are
    no accounts, so nobody can hold the permission and this endpoint is closed. Restart
    the server to pick up pack changes there.
    """
    get_packs.cache_clear()
    get_pack_dicts.cache_clear()
    packs = get_packs()  # eagerly reload so errors surface here, not on next scan
    return {
        "status": "reloaded",
        "packs_loaded": len(packs),
        "pack_ids": [p.pack_id for p in packs],
    }
