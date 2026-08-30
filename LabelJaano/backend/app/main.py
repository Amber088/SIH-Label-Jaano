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
import os
import sys
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
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
from . import logconfig  # noqa: E402
from . import ratelimit  # noqa: E402
from .routers import admin, auth_routes, history  # noqa: E402

from .schemas import (  # noqa: E402
    CategoryOut,
    PackInfo,
    SavedReportOut,
    ScanRequest,
)

__version__ = "0.2.0"

# Configure the ``labeljaano`` logger tree once, at import, so every module below (and
# the pipeline it calls) has a live handler whether the app is served or imported by a
# test. Idempotent — see logconfig.setup_logging.
logconfig.setup_logging()
_scan_log = logconfig.get_logger("scan")
_store_log = logconfig.get_logger("store")


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Open the history database. Never fatal — see :func:`deps.init_persistence`.

    Lifespan rather than the deprecated ``@app.on_event("startup")``: on_event is
    slated for removal, and it warns on every import.
    """
    deps.init_persistence()
    yield


app = FastAPI(
    title="Label Jaano — Compliance API",
    version=__version__,
    lifespan=_lifespan,
    description=(
        "Checks packaged-commodity labels against the Legal Metrology (Packaged "
        "Commodities) Rules, 2011 and stacked category packs (e.g. FSSAI food). "
        "Rules are versioned JSON — update the packs and call POST /reload; no "
        "code change or restart needed.\n\n"
        "Scanning needs no account. Sign in to keep a history, export a print-ready "
        "inspection report, and (as an officer) see aggregates across every scan."
    ),
)

# Registered *before* CORS so that CORS ends up wrapping it. Starlette applies
# middleware in reverse registration order — the last one added is the outermost — so
# the limiter has to go on first to sit inside. That ordering is load-bearing rather
# than stylistic: the 429 is written by the limiter and short-circuits the request, so
# if the limiter were outermost that response would never pass back out through
# CORSMiddleware, would carry no ``Access-Control-Allow-Origin``, and a browser would
# report it as a network error instead of showing the rate-limit message. Silent, and
# only reproducible once a real client is already being throttled.
app.add_middleware(ratelimit.RateLimitMiddleware)

# The Flutter app, the officer console and consumer mode all live on other origins,
# so cross-origin requests are the normal case rather than the exception.
#
# The default is ``*``, which is what a demo and a LAN-attached phone need. It is
# safe *only* because ``allow_credentials`` is False: no cookie ever rides along, and
# this API authenticates with an ``Authorization: Bearer`` header the browser will not
# attach on its own. A hostile page can therefore call these endpoints, but only with
# a token it already has — it cannot borrow the signed-in user's session the way a
# cookie-authenticated API would let it.
#
# Set ``LABEL_JAANO_CORS_ORIGINS`` to a comma-separated allow-list for a real
# deployment, e.g. ``https://console.example.gov.in,https://app.example.gov.in``.
CORS_ORIGINS_ENV = "LABEL_JAANO_CORS_ORIGINS"


def _cors_origins() -> list[str]:
    raw = os.environ.get(CORS_ORIGINS_ENV, "").strip()
    if not raw:
        return ["*"]
    origins = [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]
    return origins or ["*"]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Outermost middleware (added last, so it wraps CORS and the limiter): one access line
# per request — method, path, the status the client actually gets, and how long it took.
app.add_middleware(logconfig.RequestLogMiddleware)

app.include_router(auth_routes.router)
app.include_router(history.router)
app.include_router(admin.router)


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


# The catch-all category. Every pack whose ``applies_when`` is ``{"always": true}``
# still applies to it — a commodity we cannot classify is not a commodity exempt from
# the Legal Metrology rules — so it is a real, scoreable choice, not a null.
FALLBACK_CATEGORY = "other"

# Category ids are declared by the packs, so labels have to be derived. Title-casing
# the id is right for most of them ("packaged_food" -> "Packaged food"); this table
# covers only the ones where that reads badly or loses meaning. Deliberately not a
# field in the pack JSON: a pack is regulation-as-data and shared by every client,
# whereas a display string is a UI concern that would then need translating.
_CATEGORY_LABELS = {
    "other": "Other / not sure",
    "food_special_dietary": "Food for special dietary use",
    "food_special_medical": "Food for special medical purpose",
    "mrp": "Retail price declaration",
    "fmcg": "FMCG",
}


def _category_label(cid: str) -> str:
    if cid in _CATEGORY_LABELS:
        return _CATEGORY_LABELS[cid]
    return cid.replace("_", " ").capitalize()


@app.get("/categories", response_model=list[CategoryOut], tags=["rulepacks"],
         summary="Every product category the loaded packs can score")
def list_categories() -> list[CategoryOut]:
    """Category discovery, so clients stop hardcoding the list.

    The set is computed from the packs actually on disk — the union of every
    ``applies_when.category_in`` — rather than maintained by hand, which means
    dropping a new pack into ``rulepacks/`` and calling ``POST /reload`` makes its
    categories selectable without shipping a new mobile build. Before this endpoint
    existed the app shipped four hardcoded categories while the packs already covered
    a dozen, so most of the rule corpus was unreachable from the UI.

    ``declarations`` is how many rules apply to this category *after* merging and
    id-override — not the sum of the packs' own counts, which would double-count
    anything a category pack overrides. Read it as a measure of how much regulation the
    category attracts, useful for ordering the list; it is deliberately not a promise
    about ``summary.checks_total``, which differs in both directions because one
    declaration can raise several checks and a conditional one may raise none.

    Auto-detection is the *absence* of a category (omit it from the scan request and
    the extractor infers one); it is not listed here because it is not a category.
    """
    packs = get_packs()

    discovered: set[str] = {FALLBACK_CATEGORY}
    for pack in packs:
        discovered.update((pack.applies_when or {}).get("category_in") or [])

    by_id = {p.pack_id: p for p in packs}
    out: list[CategoryOut] = []
    for cid in discovered:
        ruleset = build_ruleset(cid, packs)
        out.append(CategoryOut(
            id=cid,
            label=_category_label(cid),
            packs=ruleset.packs_applied,
            declarations=len(ruleset.declarations),
            # Walked in packs_applied order (base packs first, per build_ruleset) and
            # de-duplicated with dict.fromkeys rather than a set, so the base regulator
            # leads and the order is stable between calls instead of hash-dependent.
            authorities=list(dict.fromkeys(
                by_id[pid].authority for pid in ruleset.packs_applied
                if pid in by_id and by_id[pid].authority
            )),
        ))

    # Richest category first — that is the one a picker should default to — with the
    # catch-all pinned last regardless of how many declarations the base pack carries.
    out.sort(key=lambda c: (c.id == FALLBACK_CATEGORY, -c.declarations, c.id))
    return out


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
    _scan_log.info("scan category=%s verdict=%s score=%.1f violations=%d",
                   scan_input.category, report.verdict.value, report.score,
                   len(report.violations))
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
        # A storage hiccup must not cost the caller their verdict, but it must not pass
        # silently either — log it so a run of "saved: false" has a discoverable cause.
        _store_log.warning("could not record scan: %s", exc)
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

    _scan_log.info("scan/image images=%d category=%s mock=%s verdict=%s score=%.1f",
                   len(imgs), scan_input.get("category"), provenance["mock"],
                   report.verdict.value, report.score)
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
    request: Request,
    admin: User = Depends(deps.require_permission(Permission.RELOAD_RULEPACKS)),
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
    # Audited because this is the highest-leverage write in the system: it changes the
    # rules every subsequent verdict is measured against, and two scans of the same
    # label either side of it can legitimately disagree. The pack ids and versions go
    # into the entry so a disputed verdict can be tied back to the corpus in force.
    deps.record_audit(
        store.audit.PACKS_RELOAD, admin, request,
        detail={"packs_loaded": len(packs),
                "packs": {p.pack_id: p.version for p in packs}},
    )
    return {
        "status": "reloaded",
        "packs_loaded": len(packs),
        "pack_ids": [p.pack_id for p in packs],
    }
