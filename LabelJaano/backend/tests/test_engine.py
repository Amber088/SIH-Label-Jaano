#!/usr/bin/env python3
"""
Tests for the Label Jaano rule engine.

Runs two ways:
    pytest                       # from the backend/ directory
    python3 tests/test_engine.py # no pytest needed — self-contained runner

The two end-to-end tests are the important ones for a demo: a known-good label must
come back COMPLIANT with a perfect score, and a deliberately-broken label must come
back NON-COMPLIANT with the expected violations.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
SAMPLES = BACKEND / "samples"
sys.path.insert(0, str(BACKEND))

from rule_engine import ScanInput, Verdict, build_ruleset, evaluate_scan, load_packs  # noqa: E402
from rule_engine.checks import (  # noqa: E402
    condition_met,
    lookup_min_height,
    v_fssai_license_14digit,
    v_nonempty_address,
    v_positive_currency,
    v_standard_metric_unit,
    v_valid_expiry_after_mfg,
    v_valid_month_year_not_future,
)
from rule_engine.models import Field, ScanContext  # noqa: E402


def _load(name: str) -> ScanInput:
    with open(SAMPLES / name, "r", encoding="utf-8") as fh:
        return ScanInput.from_dict(json.load(fh))


# --------------------------------------------------------------------------- #
# End-to-end
# --------------------------------------------------------------------------- #
def test_good_label_is_compliant():
    report = evaluate_scan(_load("good_label.json"))
    assert report.verdict is Verdict.COMPLIANT, report.verdict
    assert report.summary["failed"] == 0, [v.to_dict() for v in report.violations]
    assert report.score == 100.0, report.score
    # the base + food packs must both apply to a food product (other category packs
    # such as organic/fortification/packaging/contaminants legitimately stack on top)
    assert {"legal_metrology_2011", "fssai_food_2020"} <= set(report.packs_applied)


def test_bad_label_is_non_compliant():
    report = evaluate_scan(_load("bad_label.json"))
    assert report.verdict is Verdict.NON_COMPLIANT, report.verdict
    assert report.summary["violations_by_severity"]["critical"] >= 4
    failed = {(v.declaration_id, v.check_type) for v in report.violations}
    # the specific defects we baked into the sample:
    assert ("net_quantity", "value") in failed          # "30" has no unit
    assert ("net_quantity", "placement") in failed        # not on the PDP
    assert ("net_quantity", "font_height") in failed      # 0.8mm < required
    assert ("mrp", "format") in failed                    # missing 'inclusive of all taxes'
    assert ("veg_nonveg_mark", "symbol") in failed        # no veg/non-veg mark
    assert ("fssai_license", "symbol") in failed          # no FSSAI logo
    assert ("fssai_license", "value") in failed           # not 14 digits
    assert ("date_marking", "value") in failed            # expiry before mfg


# --------------------------------------------------------------------------- #
# Validators
# --------------------------------------------------------------------------- #
def test_positive_currency():
    assert v_positive_currency("Rs 10 inclusive of all taxes", None, {})[0] is True
    assert v_positive_currency("MRP", None, {})[0] is False


def test_standard_metric_unit():
    assert v_standard_metric_unit("76 g", None, {})[0] is True
    assert v_standard_metric_unit("500 ml", None, {})[0] is True
    assert v_standard_metric_unit("30", None, {})[0] is False


def test_fssai_14digit():
    assert v_fssai_license_14digit("FSSAI Lic No 10012345678901", None, {})[0] is True
    assert v_fssai_license_14digit("FSSAI Lic 12345", None, {})[0] is False


def test_nonempty_address():
    assert v_nonempty_address("Plot 5, MIDC, Pune, Maharashtra 411001", None, {})[0] is True
    assert v_nonempty_address("XYZ Snacks", None, {})[0] is False


def test_month_year_not_future():
    assert v_valid_month_year_not_future("MFG 03/2024", None, {})[0] is True
    assert v_valid_month_year_not_future("MFG 03/2099", None, {})[0] is False


def test_expiry_after_mfg():
    scan_ok = ScanInput(fields={"manufacture_date": Field(value="03/2026")})
    assert v_valid_expiry_after_mfg("Use by 12/2026", scan_ok, {})[0] is True
    scan_bad = ScanInput(fields={"manufacture_date": Field(value="08/2026")})
    assert v_valid_expiry_after_mfg("Use by 01/2026", scan_bad, {})[0] is False


# --------------------------------------------------------------------------- #
# Conditions, font table, loading/merging
# --------------------------------------------------------------------------- #
def test_condition_tokens():
    imported = ScanInput(context=ScanContext(is_imported=True))
    domestic = ScanInput(context=ScanContext(is_imported=False))
    assert condition_met("imported", imported) is True
    assert condition_met("imported", domestic) is False
    assert condition_met("always", domestic) is True
    assert condition_met("some_unknown_token", domestic) is True  # fail-safe


def test_country_of_origin_only_when_imported():
    # domestic product with no country-of-origin field -> no violation
    dom = evaluate_scan(ScanInput(category="other", context=ScanContext(is_imported=False)))
    assert not any(v.declaration_id == "country_of_origin" for v in dom.violations)
    # imported product with no country-of-origin field -> violation
    imp = evaluate_scan(ScanInput(category="other", context=ScanContext(is_imported=True)))
    assert any(v.declaration_id == "country_of_origin" for v in imp.violations)


def test_font_height_lookup():
    table = {"thresholds": [
        {"max_area_cm2": 100, "min_height_mm": 1},
        {"max_area_cm2": 500, "min_height_mm": 2},
        {"max_area_cm2": 2500, "min_height_mm": 4},
        {"max_area_cm2": None, "min_height_mm": 6},
    ]}
    assert lookup_min_height(table, 50) == 1
    assert lookup_min_height(table, 300) == 2
    assert lookup_min_height(table, 600) == 4
    assert lookup_min_height(table, 3000) == 6
    assert lookup_min_height(table, None) is None


def test_pack_loading_and_selection():
    packs = load_packs()
    assert len(packs) >= 2
    food = build_ruleset("packaged_food", packs=packs)
    assert {"legal_metrology_2011", "fssai_food_2020"} <= set(food.packs_applied)
    # a category with no dedicated pack still gets the base Legal Metrology pack
    other = build_ruleset("cosmetic", packs=packs)
    assert other.packs_applied == ["legal_metrology_2011"]


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
    print(f"Running rule-engine tests (today = {date.today().isoformat()})\n")
    raise SystemExit(_run_all())
