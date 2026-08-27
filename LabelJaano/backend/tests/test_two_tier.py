#!/usr/bin/env python3
"""
Tests for the two-tier regulatory expansion: the new FSSAI category packs and the
Tier-2 ``reference_standards`` model.

Runs two ways:
    pytest                          # from the backend/ directory
    python3 tests/test_two_tier.py  # no pytest needed — self-contained runner

The single most important guarantee here is ``test_reference_standards_never_affect_score``:
Tier-2 provisions (composition, additive limits, heavy-metal caps, lab-only safety
parameters) are surfaced to the officer but must NEVER move the compliance score or
verdict, because a label photograph cannot verify them. The rest confirm the new packs
load, stack, and gate correctly, and that each provision cites a real legal reference.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from rule_engine import ScanInput, Verdict, build_ruleset, evaluate, evaluate_scan, load_packs  # noqa: E402
from rule_engine.models import Outcome  # noqa: E402

# The full pack set the expanded engine ships with.
EXPECTED_PACKS = {
    "legal_metrology_2011", "fssai_food_2020", "fssai_alcoholic_2018",
    "fssai_organic_2017", "fssai_fortification_2018", "fssai_nutraceutical_2016",
    "fssai_contaminants_2011", "fssai_packaging_labelling_2011",
}

_WEIGHTS = {"critical": 3, "major": 2, "minor": 1}


def _score_from_results(report, weights=_WEIGHTS) -> float:
    """Recompute the score using ONLY the scored Tier-1 results. If this equals
    report.score, then the Tier-2 reference standards demonstrably contributed nothing."""
    passed = sum(weights[r.severity.value] for r in report.results if r.outcome is Outcome.PASS)
    scored = sum(weights[r.severity.value] for r in report.results
                 if r.outcome in (Outcome.PASS, Outcome.FAIL))
    return round(100.0 * passed / scored, 1) if scored else 100.0


# --------------------------------------------------------------------------- #
# Packs load & stack
# --------------------------------------------------------------------------- #
def test_all_eight_packs_load():
    ids = {p.pack_id for p in load_packs()}
    assert EXPECTED_PACKS <= ids, f"missing: {EXPECTED_PACKS - ids}"


def test_alcohol_selects_alcoholic_pack_not_food():
    rs = build_ruleset("alcoholic_beverage")
    assert "fssai_alcoholic_2018" in rs.packs_applied
    assert "fssai_food_2020" not in rs.packs_applied  # food & alcohol are mutually exclusive


def test_nutraceutical_stacks_its_pack_plus_contaminants_and_base():
    rs = build_ruleset("nutraceutical")
    assert "fssai_nutraceutical_2016" in rs.packs_applied
    assert "fssai_contaminants_2011" in rs.packs_applied   # reference-only pack rides along
    assert "legal_metrology_2011" in rs.packs_applied      # base always applies
    assert "fssai_food_2020" not in rs.packs_applied


def test_every_reference_standard_cites_a_legal_reference():
    # a Tier-2 provision with no citation would be worthless to an inspector
    for cat in ("nutraceutical", "wine", "packaged_food"):
        rep = evaluate_scan(ScanInput.from_dict(
            {"category": cat, "fields": {"net_quantity": {"value": "100 g"}},
             "context": {"is_wine": cat == "wine"}}))
        for ref in rep.reference_standards:
            assert ref["legal_reference"].strip(), (cat, ref["id"])


# --------------------------------------------------------------------------- #
# Nutraceutical (FSS Health Supplements/Nutraceuticals Regs, 2016)
# --------------------------------------------------------------------------- #
def _nutra(raw, **fields):
    base = {"generic_name": {"value": "X"}, "net_quantity": {"value": "60 capsules"},
            "mrp": {"value": "MRP Rs 499 inclusive of all taxes"},
            "manufacturer_details": {"value": "Nutra Labs Pvt Ltd, Pune 411001"},
            "manufacture_date": {"value": "MFG 01/2026"}}
    base.update(fields)
    return ScanInput.from_dict({"category": "nutraceutical", "raw_text": raw,
                                "fields": base, "symbols_detected": ["fssai_logo"], "context": {}})


def test_nutraceutical_good_label_passes_tier1_and_surfaces_refs():
    rep = evaluate_scan(_nutra(
        "VITA HEALTH SUPPLEMENT. Net Quantity 60 capsules. MRP Rs 499 inclusive of all taxes. "
        "MFG 01/2026. FSSAI Lic No 10012345678901. Contains Vitamin C. NOT FOR MEDICINAL USE. "
        "To be stored out of reach of children. Nutra Labs Pvt Ltd, Pune, Maharashtra 411001."))
    by = {r.declaration_id: r.outcome for r in rep.results}
    assert by.get("nutra_category_words") is Outcome.PASS
    assert by.get("nutra_medicinal_use_advisory") is Outcome.PASS
    assert by.get("nutra_no_disease_claim") is Outcome.PASS      # negate: no claim present
    assert by.get("nutra_out_of_reach_children") is Outcome.PASS
    assert len(rep.reference_standards) >= 2                     # RDA + permitted-ingredient schedules


def test_nutraceutical_disease_claim_fails_and_is_non_compliant():
    rep = evaluate_scan(_nutra(
        "MIRACLE PILLS. Cures diabetes and prevents cancer! MRP Rs 999 inclusive of all taxes. "
        "MFG 01/2026. FSSAI Lic No 10012345678901. X Labs, Pune 411001."))
    by = {r.declaration_id: r.outcome for r in rep.results}
    assert by.get("nutra_no_disease_claim") is Outcome.FAIL      # negate caught "cures diabetes"
    assert by.get("nutra_category_words") is Outcome.FAIL        # no "health supplement" wording
    assert rep.verdict is Verdict.NON_COMPLIANT


# --------------------------------------------------------------------------- #
# Alcohol / wine (FSS Alcoholic Beverages Standards, 2018)
# --------------------------------------------------------------------------- #
def test_wine_good_label_passes_and_surfaces_wine_only_reference():
    rep = evaluate_scan(ScanInput.from_dict({
        "category": "wine",
        "raw_text": ("SULA Cabernet Wine. Net Quantity 750 ml. 13% v/v ABV. Product of India, Nashik. "
                     "Dry red wine, residual sugar 4 g/l. Contains sulphur dioxide. "
                     "CONSUMPTION OF ALCOHOL IS INJURIOUS TO HEALTH. BE SAFE - DON'T DRINK AND DRIVE. "
                     "MRP Rs 950. MFG 02/2026. Bottled by Sula Vineyards, Nashik 422222."),
        "fields": {"generic_name": {"value": "Cabernet Wine"}, "net_quantity": {"value": "750 ml"},
                   "mrp": {"value": "MRP Rs 950"},
                   "manufacturer_details": {"value": "Sula Vineyards, Nashik 422222"},
                   "manufacture_date": {"value": "MFG 02/2026"},
                   "statutory_warning_size": {"value": "CONSUMPTION OF ALCOHOL IS INJURIOUS TO HEALTH", "height_mm": 3.5}},
        "context": {"is_wine": True}}))
    by = {r.declaration_id: r.outcome for r in rep.results}
    assert by.get("abv_declaration") is Outcome.PASS
    assert by.get("statutory_warning_health") is Outcome.PASS
    assert by.get("statutory_warning_drink_drive") is Outcome.PASS
    assert by.get("no_nutritional_info") is Outcome.PASS         # negate: prohibited info absent
    assert "wine_animal_fining_logo" in {r["id"] for r in rep.reference_standards}


def test_non_wine_alcohol_does_not_surface_wine_reference():
    rs = build_ruleset("beer")
    rep = evaluate(ScanInput.from_dict({
        "category": "beer",
        "raw_text": ("STRONG BEER 8% v/v ABV. Net Quantity 650 ml. "
                     "CONSUMPTION OF ALCOHOL IS INJURIOUS TO HEALTH. DON'T DRINK AND DRIVE. "
                     "MRP Rs 150. MFG 02/2026. ABC Breweries, Pune 411001."),
        "fields": {"net_quantity": {"value": "650 ml"}, "mrp": {"value": "MRP Rs 150"},
                   "manufacturer_details": {"value": "ABC Breweries, Pune 411001"},
                   "manufacture_date": {"value": "MFG 02/2026"}},
        "context": {"is_wine": False}}), rs)
    assert "wine_animal_fining_logo" not in {r["id"] for r in rep.reference_standards}
    # "Strong" is a permitted beer class under the 2018 standards — it must not be penalised
    assert rep.verdict is not Verdict.NON_COMPLIANT or \
        not any("strong" in (v.message or "").lower() for v in rep.violations)


# --------------------------------------------------------------------------- #
# The core Tier-2 guarantee
# --------------------------------------------------------------------------- #
def test_reference_standards_never_affect_score():
    # For every category that surfaces refs, the score recomputed from Tier-1 results
    # ALONE must equal the engine's score — proving refs are structurally excluded.
    cases = [
        ("nutraceutical", {}), ("wine", {"is_wine": True}),
        ("packaged_food", {}), ("health_supplement", {}),
    ]
    for cat, ctx in cases:
        rep = evaluate_scan(ScanInput.from_dict(
            {"category": cat, "raw_text": "Net Quantity 100 g MRP Rs 20 inclusive of all taxes",
             "fields": {"net_quantity": {"value": "100 g"}}, "context": ctx}))
        assert rep.reference_standards, f"{cat} surfaced no refs to test the guarantee against"
        assert _score_from_results(rep) == rep.score, \
            f"{cat}: refs leaked into score ({_score_from_results(rep)} vs {rep.score})"


def test_contaminants_pack_is_reference_only():
    rs = build_ruleset("packaged_food")
    assert "fssai_contaminants_2011" in rs.packs_applied
    contam = {"contaminants_metal_limits", "contaminants_mycotoxins",
              "contaminants_natural_toxins", "contaminants_pesticide_residues",
              "contaminants_antibiotic_residues"}
    rep = evaluate_scan(ScanInput.from_dict(
        {"category": "packaged_food", "fields": {"net_quantity": {"value": "100 g"}}}))
    surfaced = {r["id"] for r in rep.reference_standards}
    assert contam <= surfaced                                   # all five surfaced as references
    assert not any(r.declaration_id in contam for r in rep.results)   # none became a scored check


# --------------------------------------------------------------------------- #
# Conditional gating (narrow triggers only fire when flagged)
# --------------------------------------------------------------------------- #
def test_organic_and_fortification_only_fire_when_flagged():
    plain = evaluate_scan(ScanInput.from_dict(
        {"category": "packaged_food", "fields": {"net_quantity": {"value": "100 g"}}, "context": {}}))
    ran = {r.declaration_id for r in plain.results}
    assert "organic_status_info" not in ran
    assert "fortified_with_declaration" not in ran

    org = evaluate_scan(ScanInput.from_dict(
        {"category": "packaged_food", "raw_text": "Certified Organic. Jaivik Bharat. Net Quantity 100 g",
         "fields": {"net_quantity": {"value": "100 g"}},
         "symbols_detected": ["fssai_organic_logo"], "context": {"is_organic": True}}))
    assert "organic_status_info" in {r.declaration_id for r in org.results}


def test_reference_standard_dict_shape_matches_client_contract():
    # The API's ReferenceStandardOut expects exactly these keys; `condition` must not leak.
    rep = evaluate_scan(ScanInput.from_dict(
        {"category": "nutraceutical", "fields": {"net_quantity": {"value": "60 capsules"}}}))
    assert rep.reference_standards
    for ref in rep.reference_standards:
        assert {"id", "label", "legal_reference"} <= set(ref)   # required by ReferenceStandardOut
        assert set(ref) <= {"id", "label", "legal_reference", "authority", "note"}
        assert "condition" not in ref                           # internal gating, not client data


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
    print(f"Running two-tier / new-pack tests (today = {date.today().isoformat()})\n")
    raise SystemExit(_run_all())
