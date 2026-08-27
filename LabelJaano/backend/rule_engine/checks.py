"""
Check evaluation: validators, symbol detection, condition tokens, and the
per-check dispatcher.

This is the part the README calls the *engine contract*. When a government rule
changes, you edit a JSON pack — but when you need a **new kind of check** (a new
validator or a new symbol), you add it here, in one of the registries below.

Every validator has the signature ``fn(value, scan, params) -> (bool, detail)`` so
that cross-field validators (e.g. expiry-after-manufacture) can read other fields
off the scan. Detail is a short human string explaining the result.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Callable, Optional, Tuple

from .models import Check, Declaration, Outcome, RuleSet, ScanInput

Detail = str
ValidatorResult = Tuple[bool, Detail]


# --------------------------------------------------------------------------- #
# Validators  (value checks)
# --------------------------------------------------------------------------- #
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_dates(text: str) -> list[date]:
    """Best-effort extraction of dates from messy label text.

    Handles: dd/mm/yyyy, mm/yyyy, mm-yy, 'Mon YYYY', 'Mon-YY', bare 'MM/YY'.
    Returns every date it can parse (day defaults to 1 when absent).
    """
    if not text:
        return []
    t = text.lower()
    found: list[date] = []

    # Month-name forms: "aug 2026", "aug-26", "august 2025"
    for m in re.finditer(r"([a-z]{3,9})[\s\-/,]*'?(\d{2,4})", t):
        mon = m.group(1)[:3]
        if mon in _MONTHS:
            yr = _norm_year(m.group(2))
            if yr:
                found.append(date(yr, _MONTHS[mon], 1))

    # Numeric forms: dd/mm/yyyy, mm/yyyy, mm/yy
    for m in re.finditer(r"\b(\d{1,2})[/\-.](\d{1,4})(?:[/\-.](\d{1,4}))?\b", t):
        a, b, c = m.group(1), m.group(2), m.group(3)
        try:
            if c:  # dd/mm/yyyy
                day, mon, yr = int(a), int(b), _norm_year(c)
            else:  # mm/yyyy or mm/yy
                day, mon, yr = 1, int(a), _norm_year(b)
            if yr and 1 <= mon <= 12 and 1 <= day <= 31:
                found.append(date(yr, mon, day))
        except (ValueError, TypeError):
            continue
    return found


def _norm_year(s: str) -> Optional[int]:
    try:
        y = int(s)
    except ValueError:
        return None
    if y < 100:              # two-digit year -> 2000s
        y += 2000
    if 1990 <= y <= 2100:
        return y
    return None


def v_nonempty_address(value: str, scan: ScanInput, params: dict) -> ValidatorResult:
    v = (value or "").strip()
    if len(v) < 10:
        return False, "address too short to be a full postal address"
    has_pin = bool(re.search(r"\b\d{6}\b", v))
    has_parts = v.count(",") >= 1 and len(v) >= 15
    if has_pin or has_parts:
        return True, "looks like a full address"
    return False, "no PIN code or multi-part address detected"


def v_standard_metric_unit(value: str, scan: ScanInput, params: dict) -> ValidatorResult:
    v = (value or "").lower()
    if re.search(r"\d+(\.\d+)?\s*(kg|g|mg|l|ml|cl|litre|liter|m|cm|mm|pcs?|pieces?|nos?\.?|n\b)",
                 v):
        return True, "standard metric unit / count found"
    return False, "no standard metric unit (g, kg, ml, l, m) or count found"


def v_positive_currency(value: str, scan: ScanInput, params: dict) -> ValidatorResult:
    v = value or ""
    m = re.search(r"(?:₹|rs\.?|inr)?\s*(\d+(?:[,.]\d+)?)", v, re.I)
    if not m:
        return False, "no numeric amount found"
    amount = float(m.group(1).replace(",", ""))
    if amount > 0:
        return True, f"amount = {amount}"
    return False, "amount is not positive"


def v_valid_month_year_not_future(value: str, scan: ScanInput, params: dict) -> ValidatorResult:
    dates = _parse_dates(value or "")
    if not dates:
        return False, "could not parse a month/year"
    earliest = min(dates)
    today = date.today()
    if earliest > today:
        return False, f"date {earliest.isoformat()} is in the future"
    return True, f"manufacture date {earliest.isoformat()} is valid"


def v_valid_expiry_after_mfg(value: str, scan: ScanInput, params: dict) -> ValidatorResult:
    expiry_dates = _parse_dates(value or "")
    if not expiry_dates:
        return False, "could not parse an expiry / best-before date"
    expiry = max(expiry_dates)

    # Prefer comparing against a separately-extracted manufacture_date field.
    mfg_field = scan.fields.get("manufacture_date")
    mfg_dates = _parse_dates(mfg_field.value) if (mfg_field and mfg_field.present) else []
    if mfg_dates:
        mfg = min(mfg_dates)
        if expiry > mfg:
            return True, f"expiry {expiry.isoformat()} after mfg {mfg.isoformat()}"
        return False, f"expiry {expiry.isoformat()} is not after mfg {mfg.isoformat()}"

    # No separate mfg field: if the value itself holds two+ dates, compare within.
    if len(set(expiry_dates)) >= 2:
        ds = sorted(set(expiry_dates))
        if ds[-1] > ds[0]:
            return True, f"expiry {ds[-1].isoformat()} after mfg {ds[0].isoformat()}"
        return False, "expiry / best-before does not follow the manufacture date"

    # single date — treat as best-before; valid as long as it parsed
    return True, f"single date {expiry.isoformat()} parsed"


def v_phone_or_email_present(value: str, scan: ScanInput, params: dict) -> ValidatorResult:
    v = value or ""
    if re.search(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}", v, re.I):
        return True, "email found"
    if re.search(r"(\+?91[\-\s]?)?(1800[\-\s]?\d{3}[\-\s]?\d{3,4}|\d{10})", v):
        return True, "phone number found"
    return False, "no phone number or email found"


def v_fssai_license_14digit(value: str, scan: ScanInput, params: dict) -> ValidatorResult:
    if re.search(r"\b\d{14}\b", value or ""):
        return True, "14-digit FSSAI licence number found"
    return False, "no 14-digit FSSAI licence number found"


VALIDATORS: dict[str, Callable[[str, ScanInput, dict], ValidatorResult]] = {
    "nonempty_address": v_nonempty_address,
    "standard_metric_unit": v_standard_metric_unit,
    "positive_currency": v_positive_currency,
    "valid_month_year_not_future": v_valid_month_year_not_future,
    "valid_expiry_after_mfg": v_valid_expiry_after_mfg,
    "phone_or_email_present": v_phone_or_email_present,
    "fssai_license_14digit": v_fssai_license_14digit,
}


# --------------------------------------------------------------------------- #
# Symbols  (symbol checks)
# --------------------------------------------------------------------------- #
# For the MVP the CV layer is not wired in yet, so symbol detection reads from
# ``scan.symbols_detected`` (a list the vision pipeline fills). This registry
# documents the known symbol names; new packs just add to it. Later, swap the
# lookup for a real detector keyed by the same names — no pack changes needed.
KNOWN_SYMBOLS = {
    "veg_nonveg_mark", "fssai_logo",            # current packs
    "isi_mark", "bis_crs_mark", "bis_hallmark", # future packs
    "bee_star_label", "ewaste_bin", "ghs_pictogram",
    "toxicity_triangle", "agmark", "india_organic",
    # FSSAI scheme logos added with the regulation-derived packs
    "fortification_plus_f_logo",                # +F logo, Fortification Regs 2018 Sch. II
    "fssai_organic_logo",                       # Jaivik Bharat logo, Organic Foods Regs 2017
    "pgs_india_mark",                           # PGS-India certification mark
}


def symbol_detected(symbol: str, scan: ScanInput) -> bool:
    return symbol in set(scan.symbols_detected)


# --------------------------------------------------------------------------- #
# Condition tokens
# --------------------------------------------------------------------------- #
def condition_met(condition: str, scan: ScanInput) -> bool:
    ctx = scan.context
    table = {
        "always": True,
        "imported": ctx.is_imported,
        "dimension_relevant": ctx.dimension_relevant,
        "not_single_ingredient": not ctx.is_single_ingredient,
        "has_additives": ctx.has_additives,
        "has_allergens": ctx.has_allergens,
        # narrow product triggers (see ScanContext) — each defaults False so the
        # matching declaration SKIPs unless the pipeline positively detects the trait
        "is_organic": ctx.is_organic,
        "is_fortified": ctx.is_fortified,
        "is_iron_fortified": ctx.is_iron_fortified,
        "is_wine": ctx.is_wine,
        "is_low_alcohol": ctx.is_low_alcohol,
        "has_artificial_sweetener": ctx.has_artificial_sweetener,
        "has_aspartame": ctx.has_aspartame,
        "has_caffeine": ctx.has_caffeine,
        "has_added_colour": ctx.has_added_colour,
        "has_added_flavour": ctx.has_added_flavour,
        "has_added_msg": ctx.has_added_msg,
        "is_irradiated": ctx.is_irradiated,
        "is_pan_masala": ctx.is_pan_masala,
        # compound triggers
        "low_alcohol_non_wine": ctx.is_low_alcohol and not ctx.is_wine,
    }
    # Unknown token -> fail-safe True (check it rather than silently skip).
    return table.get(condition, True)


# --------------------------------------------------------------------------- #
# Font-height table lookup
# --------------------------------------------------------------------------- #
def lookup_min_height(table: Optional[dict], area_cm2: Optional[float]) -> Optional[float]:
    if not table or area_cm2 is None:
        return None
    thresholds = table.get("thresholds", [])
    for row in thresholds:
        mx = row.get("max_area_cm2")
        if mx is None or area_cm2 <= mx:
            return row.get("min_height_mm")
    return thresholds[-1].get("min_height_mm") if thresholds else None


# --------------------------------------------------------------------------- #
# Per-check dispatcher
# --------------------------------------------------------------------------- #
def evaluate_check(
    check: Check, decl: Declaration, scan: ScanInput, ruleset: RuleSet
) -> Tuple[Outcome, Detail]:
    """Return (Outcome, detail) for one check against one scan.

    Field-dependent checks (format[normalized], value, placement, font_height)
    return SKIP when the field is absent — the declaration's presence check already
    reports 'missing', so we don't double-penalize. Independent checks
    (symbol, format[raw_text]) always evaluate.
    """
    field = scan.fields.get(decl.id)
    present = field.present if field else False
    t = check.type

    if t == "presence":
        if present:
            return Outcome.PASS, "field extracted"
        return (Outcome.FAIL if decl.required else Outcome.SKIP), "field not extracted"

    if t == "symbol":
        if symbol_detected(check.symbol, scan):
            return Outcome.PASS, f"{check.symbol} detected"
        return Outcome.FAIL, f"{check.symbol} not detected"

    if t == "format":
        if check.target == "raw_text":
            text = scan.raw_text or ""
        else:  # normalized -> match against the extracted field value
            if not present:
                return Outcome.SKIP, "no value to match"
            text = field.value or ""
        try:
            found = bool(re.search(check.regex, text))
        except re.error as e:
            return Outcome.SKIP, f"bad regex: {e}"
        if check.negate:
            # Prohibition: the label must NOT contain this pattern.
            if found:
                return Outcome.FAIL, "prohibited pattern present"
            return Outcome.PASS, "prohibited pattern absent"
        if found:
            return Outcome.PASS, "pattern matched"
        return Outcome.FAIL, "pattern not found"

    if t == "value":
        if not present:
            return Outcome.SKIP, "no value to validate"
        fn = VALIDATORS.get(check.validator)
        if fn is None:
            return Outcome.SKIP, f"unknown validator '{check.validator}'"
        ok, detail = fn(field.value, scan, check.params)
        return (Outcome.PASS if ok else Outcome.FAIL), detail

    if t == "placement":
        if not present or not field.panel:
            return Outcome.SKIP, "panel not known"
        if field.panel == check.panel:
            return Outcome.PASS, f"on {field.panel}"
        return Outcome.FAIL, f"on '{field.panel}', expected '{check.panel}'"

    if t == "font_height":
        if not present or field.height_mm is None:
            return Outcome.SKIP, "glyph height not measured"
        if check.min_height_mm is not None:
            min_mm = check.min_height_mm
        else:
            min_mm = lookup_min_height(ruleset.font_height_table, scan.context.pdp_area_cm2)
        if min_mm is None:
            return Outcome.SKIP, "no height threshold available"
        if field.height_mm >= min_mm:
            return Outcome.PASS, f"{field.height_mm}mm >= {min_mm}mm"
        return Outcome.FAIL, f"{field.height_mm}mm < {min_mm}mm required"

    return Outcome.SKIP, f"unknown check type '{t}'"
