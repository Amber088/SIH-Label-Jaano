"""
The vision-LLM extraction prompt.

The prompt is **generated from the live rule packs**, not hard-coded: it asks Gemini
for exactly the declaration ids the current packs define (via ``load_packs()``) and the
symbol names the engine knows (``KNOWN_SYMBOLS``). So when you add a rule pack or a new
declaration, the extractor automatically starts asking for it — the pitch line is
"the extractor requests precisely what the current regulations require, nothing else."

Everything here is plain strings — no dependencies — so it is unit-testable in any
environment.
"""
from __future__ import annotations

from typing import Iterable

# Coarse panel tags we ask the LLM for (fusion maps these to the engine's vocabulary).
PANELS = ["front", "back", "side", "unknown"]

# Context booleans the engine's condition tokens consume (see rule_engine/checks.py:
# condition_met). pdp_area_cm2 is filled by calibration, not the LLM.
# NB: every key a rule pack references via a `condition` MUST appear here, or fusion
# drops it and the corresponding rule silently stops firing. Keep in sync with
# rule_engine.models.ScanContext.
CONTEXT_KEYS = [
    # generic (base packs)
    "is_imported",
    "is_single_ingredient",
    "has_additives",
    "has_allergens",
    "dimension_relevant",
    # category-specific triggers (FSSAI 2016/2017/2018 packs)
    "is_organic",
    "is_fortified",
    "is_iron_fortified",
    "is_wine",
    "is_low_alcohol",
    "has_artificial_sweetener",
    "has_aspartame",
    "has_caffeine",
    "has_added_colour",
    "has_added_flavour",
    "has_added_msg",
    "is_irradiated",
    "is_pan_masala",
]


def _bullet_list(items: Iterable[str]) -> str:
    return "\n".join(f"  - {i}" for i in items)


def build_extraction_prompt(
    declaration_ids: list[str],
    symbols: list[str],
    categories: list[str],
) -> str:
    """Return the full instruction prompt for the given rule-driven vocabulary."""
    return f"""You are a meticulous compliance inspector for Indian packaged commodities.
You are given one or more photographs of the SAME product's label (e.g. the front /
principal display panel and the back panel). Read every panel and extract the legally
mandated declarations under the Legal Metrology (Packaged Commodities) Rules, 2011 and,
for food, the FSSAI Labelling Regulations, 2020.

Return a SINGLE JSON object and NOTHING else (no markdown, no code fences, no commentary).

The JSON must have exactly these top-level keys:

"category": one of {categories}. Classify the product. Use "packaged_food" for any
    packaged edible/food item, "beverage" for drinks, "packaged_water" for bottled
    water. For special regulated foods choose the most specific match:
      - "nutraceutical" / "health_supplement" — capsules, tablets, powders or
        drinks sold as supplements, nutraceuticals, "health supplement", probiotic
        or prebiotic foods, foods for special dietary or medical use.
      - "alcoholic_beverage" (or "wine" / "beer" / "spirit" / "liquor") — anything
        with an alcohol-by-volume declaration; use "wine" specifically for wine.
    Otherwise the closest match or "other".

"fields": an object. For EACH declaration you can find on the label, add an entry keyed
    by its id below. Omit declarations that are genuinely absent from the label — do not
    invent values. Each entry is an object:
        {{
          "value": "<the exact text as printed, transcribed faithfully>",
          "panel": one of {PANELS},   // where on the pack you read it
          "source_image": <0-based index of the image it appears in>,
          "confidence": <0.0-1.0 your reading confidence>
        }}
    Declaration ids to look for (these come from the active rule packs):
{_bullet_list(declaration_ids)}
    Guidance on a few:
      - net_quantity: the quantity statement, e.g. "Net Qty 500 g", "500 ml".
      - mrp: the full MRP line, INCLUDING wording like "Maximum Retail Price" and
        "inclusive of all taxes" if present — transcribe it verbatim.
      - manufacture_date / date_marking: transcribe the date wording exactly
        ("MFG 03/2026", "Use by 12/2026", "Best before ...").
      - manufacturer_details: the full name AND address of the manufacturer/packer/
        importer, including PIN code.
      - fssai_license: the line containing the FSSAI licence number (a 14-digit number).
      - consumer_care: customer-care phone and/or email.

"symbols_detected": an array listing any of these graphical marks you can SEE on the
    label (by shape/logo, not just text). Valid names:
{_bullet_list(symbols)}
    Notably: "veg_nonveg_mark" = the green (veg) or brown/red (non-veg) dot-in-a-square
    symbol; "fssai_logo" = the FSSAI logo mark (distinct from the licence number text).

"context": an object of booleans that affect which rules apply. Set a flag true ONLY
    when the label positively shows the trait; otherwise omit it or set it false.
    {{
      "is_imported": <true if it names a foreign country of origin / importer>,
      "is_single_ingredient": <true if it is a single-ingredient food, e.g. plain sugar>,
      "has_additives": <true if additives / INS numbers / "added flavour" appear>,
      "has_allergens": <true if allergens are named, e.g. milk, nuts, soy, wheat/gluten>,
      "dimension_relevant": <true if it is sold by length/area/number where size matters>,
      "is_organic": <true if presented as organic (organic, Jaivik Bharat, NPOP, PGS-India)>,
      "is_fortified": <true if it claims fortification ("fortified with ...", the +F logo)>,
      "is_iron_fortified": <true if specifically fortified with IRON>,
      "is_wine": <true if the product is a wine>,
      "is_low_alcohol": <true if an alcoholic beverage below 10% abv>,
      "has_artificial_sweetener": <true if an artificial sweetener is used (aspartame, sucralose, acesulfame-K, saccharin)>,
      "has_aspartame": <true if aspartame specifically is used>,
      "has_caffeine": <true if caffeine is added as an ingredient>,
      "has_added_colour": <true if an added colour is declared>,
      "has_added_flavour": <true if an added flavour is declared>,
      "has_added_msg": <true if monosodium glutamate / MSG / INS 621 is added>,
      "is_irradiated": <true if the food is irradiated (radura symbol / "treated with ionising radiation")>,
      "is_pan_masala": <true if the product is pan masala>
    }}

"raw_text": a faithful transcription of ALL visible text across every image,
    concatenated in reading order. This is used for text-pattern checks.

Be conservative: if you cannot read something, lower its confidence rather than
guessing. Transcribe numbers, units and dates exactly as printed.
"""
