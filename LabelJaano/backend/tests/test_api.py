#!/usr/bin/env python3
"""
Tests for the Label Jaano HTTP API (FastAPI).

Runs two ways:
    pytest                     # from the backend/ directory
    python3 tests/test_api.py  # self-contained runner (still needs fastapi + httpx)

Uses Starlette's TestClient (requires ``httpx``) so no server needs to be running.
These tests confirm the API faithfully surfaces the engine: the known-good sample
must come back COMPLIANT over HTTP, the broken sample NON-COMPLIANT, and the
rulepack / health / reload plumbing must work.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
SAMPLES = BACKEND / "samples"
sys.path.insert(0, str(BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)


def _sample(name: str) -> dict:
    with open(SAMPLES / name, "r", encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------- #
# Meta
# --------------------------------------------------------------------------- #
def test_health_ok():
    r = client.get("/health")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["packs_loaded"] >= 2
    assert "legal_metrology_2011" in body["pack_ids"]


def test_openapi_docs_served():
    # the auto-generated schema should list our /scan route
    r = client.get("/openapi.json")
    assert r.status_code == 200, r.text
    assert "/scan" in r.json()["paths"]


# --------------------------------------------------------------------------- #
# Rulepacks
# --------------------------------------------------------------------------- #
def test_list_rulepacks():
    r = client.get("/rulepacks")
    assert r.status_code == 200, r.text
    packs = r.json()
    ids = {p["pack_id"] for p in packs}
    assert {"legal_metrology_2011", "fssai_food_2020"} <= ids
    base = next(p for p in packs if p["pack_id"] == "legal_metrology_2011")
    assert base["scope"] == "base"
    assert base["declarations"] >= 9


def test_get_one_rulepack_full_json():
    r = client.get("/rulepacks/legal_metrology_2011")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pack_id"] == "legal_metrology_2011"
    # the full rule text, not just a summary
    assert isinstance(body["declarations"], list) and body["declarations"]
    assert "font_height_table" in body


def test_get_unknown_rulepack_404():
    r = client.get("/rulepacks/does_not_exist")
    assert r.status_code == 404, r.text


# --------------------------------------------------------------------------- #
# Scan (the important ones)
# --------------------------------------------------------------------------- #
def test_scan_good_label_compliant():
    r = client.post("/scan", json=_sample("good_label.json"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verdict"] == "compliant", body["violations"]
    assert body["score"] == 100.0
    assert {"legal_metrology_2011", "fssai_food_2020"} <= set(body["packs_applied"])
    assert body["summary"]["failed"] == 0


def test_scan_bad_label_non_compliant():
    r = client.post("/scan", json=_sample("bad_label.json"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verdict"] == "non_compliant", body
    assert body["summary"]["violations_by_severity"]["critical"] >= 4
    # each violation carries its legal citation
    assert all(v["legal_reference"] for v in body["violations"])


def test_scan_minimal_payload_defaults():
    # a nearly-empty body must still validate and run (category defaults to unknown
    # -> only the base pack applies) rather than 422.
    r = client.post("/scan", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["packs_applied"] == ["legal_metrology_2011"]


def test_scan_ignores_extra_comment_key():
    # samples carry a leading "_comment"; pydantic must ignore unknown top-level keys
    payload = _sample("good_label.json")
    payload["_comment"] = "this should be ignored, not 422"
    r = client.post("/scan", json=payload)
    assert r.status_code == 200, r.text


# --------------------------------------------------------------------------- #
# Tier-2 reference standards (must survive response_model=ReportOut)
# --------------------------------------------------------------------------- #
def test_scan_surfaces_reference_standards():
    # A nutraceutical scan carries Tier-2 reference standards; response_model=ReportOut
    # must pass them through to the client (this field was silently stripped before).
    r = client.post("/scan", json={
        "category": "nutraceutical",
        "raw_text": "VITA HEALTH SUPPLEMENT. NOT FOR MEDICINAL USE. Out of reach of children.",
        "fields": {"net_quantity": {"value": "60 capsules"},
                   "mrp": {"value": "MRP Rs 499 inclusive of all taxes"}},
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert "reference_standards" in body, "response_model stripped the Tier-2 block"
    refs = body["reference_standards"]
    assert isinstance(refs, list) and len(refs) >= 2, refs
    for ref in refs:
        assert ref["id"] and ref["label"] and ref["legal_reference"], ref
        assert "condition" not in ref  # internal gating token stays server-side


def test_scan_reference_standards_are_not_scored_checks():
    # refs must be disjoint from the scored results/violations in the response
    r = client.post("/scan", json={"category": "packaged_food",
                                    "fields": {"net_quantity": {"value": "100 g"}}})
    assert r.status_code == 200, r.text
    body = r.json()
    ref_ids = {ref["id"] for ref in body["reference_standards"]}
    result_ids = {res["declaration_id"] for res in body["results"]}
    assert ref_ids, "expected contaminants refs for a food product"
    assert not (ref_ids & result_ids), "a reference standard leaked into scored results"


def test_scan_accepts_new_context_flags():
    # ContextIn must model the new trigger flags: a wine scan with is_wine=true must
    # not 422 AND must actually surface the wine-only reference standard.
    r = client.post("/scan", json={
        "category": "wine",
        "raw_text": ("Wine 13% v/v ABV. CONSUMPTION OF ALCOHOL IS INJURIOUS TO HEALTH. "
                     "DON'T DRINK AND DRIVE."),
        "fields": {"net_quantity": {"value": "750 ml"}},
        "context": {"is_wine": True},
    })
    assert r.status_code == 200, r.text
    ref_ids = {ref["id"] for ref in r.json()["reference_standards"]}
    assert "wine_animal_fining_logo" in ref_ids, ref_ids


# --------------------------------------------------------------------------- #
# Reload
# --------------------------------------------------------------------------- #
def test_reload_returns_pack_count():
    r = client.post("/reload")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "reloaded"
    assert body["packs_loaded"] >= 2


# --------------------------------------------------------------------------- #
# Self-contained runner (no pytest required)
# --------------------------------------------------------------------------- #
def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed, {len(tests)} total")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    print("Running API tests\n")
    raise SystemExit(_run_all())
