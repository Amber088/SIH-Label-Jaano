# Regulatory Packs — Label Compliance Rules by Commodity Category

**Companion to ARCHITECTURE.md.** This is the domain reference for building Label Jaano's rule packs. It answers: *"For any packaged item — not just food — what must the label declare, and which law says so?"*

---

## How the "regulatory pack" model works

Indian packaging law is **layered**. Every packaged commodity — food or non-food — must carry the **Legal Metrology** declarations (the base layer). Specific categories then **stack additional requirements** from other regulators on top.

```
Applicable rules  =  Base pack (Legal Metrology — ALWAYS)
                   +  Category pack(s) (FSSAI / Cosmetics / Drugs / BIS / …)
```

In the engine (see ARCHITECTURE §6), each pack is a **versioned JSON rule set**. At scan time:

1. The base **Legal Metrology pack** always runs.
2. The vision-LLM **classifies the product category** (from the image + generic name, or a barcode/GTIN lookup).
3. The matching **category pack(s)** are applied.
4. Every violation cites **its own legal reference** — an LMPC violation cites "Rule 6(1)(e)", an FSSAI one cites the 2020 Regulation, a cosmetics one cites the Cosmetics Rules 2020 — so the two regimes never get conflated.

This turns Label Jaano from a single-law checker into a **multi-regulation label-compliance platform** — a much bigger, more defensible product story. Legal Metrology stays the primary, mandatory core; every other pack is a value-add.

> ⚠️ **Accuracy note:** exact thresholds, mark specifications, and especially the **BIS mandatory-product lists (Quality Control Orders)** change frequently. Treat the specifics below as the *structure* to encode; load authoritative values from the primary sources listed at the end before you ship a live checker. Because packs are config-driven, updating them is a data change, not a code change.

---

## Layer 0 — Base Pack: Legal Metrology (Packaged Commodities) Rules, 2011

**Authority:** Department of Consumer Affairs — Legal Metrology Division. **Applies to:** every pre-packaged commodity sold in India (food *and* non-food).

| # | Declaration | Requirement | Reference |
|---|---|---|---|
| 1 | Manufacturer / packer / importer | Name & **complete address**; if manufacturer ≠ packer, both; importer for imports | Rule 6(1)(a) |
| 2 | Common / generic name | Name of the commodity in the package | Rule 6(1)(b) |
| 3 | Net quantity | In standard **metric** units (g, kg, ml, l, m, or number) | Rule 6(1)(c) |
| 4 | Date of manufacture/pack/import | **Month & year** | Rule 6(1)(d) |
| 5 | Retail sale price (MRP) | "Maximum Retail Price ₹__ **inclusive of all taxes**" | Rule 6(1)(e) |
| 6 | Consumer care details | Name, address, phone, email for complaints | Rule 6(1)(f) |
| 7 | Country of origin | For **imported** packages | Rule 6 |
| 8 | Unit sale price | Per unit (g/ml/piece) — added by amendments | Rule 6 (amend.) |
| 9 | Dimensions | Where relevant to the commodity | Rule 6 |

**General rules that apply across the board:** declarations must be **legible, prominent, in a contrasting colour**; letter/numeral **minimum height scales with the Principal Display Panel area** (Rule 8 — see ARCHITECTURE §7); certain declarations (net quantity, MRP) belong on the **principal display panel**; declarations in **Hindi and/or English**.

**Package-type nuances to model:** *retail*, *wholesale*, *multi-piece*, and *combination* packages have slightly different rules; and **scheduled commodities** (e.g., baby food, biscuits, bread, butter, tea, edible oil, milk powder, cement…) must be packed in **prescribed standard quantities**.

---

## Category Packs (additional to Layer 0)

### 1. Food & Beverages
**Authority:** FSSAI — Food Safety and Standards Act, 2006 + **Labelling & Display Regulations, 2020**.

| Declaration / Mark | Requirement |
|---|---|
| List of ingredients | Descending order by weight; compound ingredients broken down; added water declared |
| Nutritional information | Energy, protein, carbohydrate (incl. total & added sugars), fat (incl. saturated & trans) — per 100 g/ml **and** per serving |
| Veg / Non-veg symbol | Green filled dot (veg) / brown filled dot (non-veg) |
| FSSAI logo + licence | 14-digit FSSAI licence/registration number |
| Food additives | Class name + **INS number** |
| Allergen declaration | Known allergens called out |
| Date marking | Date of manufacture/packing + "best before"/"use by"/expiry |
| Lot / batch / code number | Traceability |
| Storage & use | Storage conditions, instructions for use |
| Claims | Nutrition/health claims must meet defined criteria |

### 2. Cosmetics
**Authority:** Drugs and Cosmetics Act, 1940 + **Cosmetics Rules, 2020**.

| Declaration / Mark | Requirement |
|---|---|
| Name & purpose of cosmetic | Product name |
| Manufacturer / importer | Name & address |
| Net contents | Weight/volume/number |
| List of ingredients | Descending order for packs **> 10 g / 10 ml** (smaller packs: on outer/leaflet) |
| Manufacturing licence no. | "Mfg. Lic. No." |
| Batch number | "B. No." / Batch No. |
| Dates | Date of manufacture + best before / use-before (expiry) |
| Directions & warnings | Use instructions + cautions |
| "For external use only" | Where applicable |
| Imported cosmetics | Import registration certificate number + importer details |

### 3. Drugs / Pharmaceuticals
**Authority:** Drugs and Cosmetics Act, 1940 + Rules, 1945.

| Declaration / Mark | Requirement |
|---|---|
| Generic (proper) name | Prominent — at least as conspicuous as the brand name |
| Manufacturer | Name & address |
| Manufacturing licence no. | "Mfg. Lic. No." |
| Batch number | "B. No." |
| Dates | Date of manufacture + **expiry date** |
| Net quantity & MRP | Standard |
| Schedule warnings | Schedule H / H1 (Rx symbol + "to be sold on prescription of a Registered Medical Practitioner only"); Schedule X; Schedule G caution |
| Red border line | Vertical red line on the left border for Schedule H / H1 drugs |
| Storage conditions | As applicable |

### 4. Electronics & Electrical Appliances
**Authority:** BIS (Compulsory Registration Scheme) + BEE (energy) + E-Waste Management Rules, 2022.

| Declaration / Mark | Requirement |
|---|---|
| BIS CRS mark + registration no. | Standard Mark + "R-xxxxxxx" for products under Compulsory Registration (phones, chargers, LED products, laptops/tablets, batteries, CCTV, smart watches, wireless gear…) |
| BEE Star Rating label | For notified appliances (ACs, refrigerators, ceiling fans, LED bulbs, geysers…): star rating + annual energy consumption |
| E-waste marking | Crossed-out wheeled-bin symbol + e-waste info |
| RoHS | Reduction of Hazardous Substances compliance |
| Product details | Model number, voltage/power ratings, manufacturer/importer, country of origin |

### 5. BIS-Certified Goods (ISI Mark)
**Authority:** BIS Act, 2016 + product-specific **Quality Control Orders (QCOs)** issued by various ministries.

| Declaration / Mark | Requirement |
|---|---|
| ISI Standard Mark | ISI monogram + relevant **IS number** + **CM/L licence number** |
| Covered products *(illustrative — verify current QCO list)* | Cement, steel/TMT bars, helmets (IS 4151), pressure cookers, LPG cylinders & valves, packaged/mineral water, kitchen & electrical appliances, switches/cables, toys (IS 9873), footwear, milk powder, and a growing list |

> QCO coverage expands frequently (2023–2025 saw major additions like toys and footwear). Always check the current "Products under Mandatory Certification" list at bis.gov.in.

### 6. Packaged Drinking / Mineral Water
**Authority:** FSSAI **+** BIS (mandatory ISI) **+** Legal Metrology — a triple-regulated product.

| Declaration / Mark | Requirement |
|---|---|
| BIS ISI mark (mandatory) | IS 14543 (packaged drinking water) or IS 13428 (natural mineral water) + CM/L number |
| FSSAI | Logo + 14-digit licence |
| Base + dates | All LMPC declarations, batch, mfg date, best before |

### 7. Gold / Silver Jewellery
**Authority:** BIS **Hallmarking** (mandatory for gold jewellery/artefacts in notified districts).

| Declaration / Mark | Requirement |
|---|---|
| BIS Hallmark | BIS logo |
| Purity / fineness | Caratage & fineness (22K = 916, 18K = 750, 14K = 585) |
| HUID | 6-digit alphanumeric **Hallmark Unique ID** |
| Jeweller identity | Jeweller's registration/identification mark |

### 8. Tobacco Products
**Authority:** COTPA, 2003 + Packaging & Labelling Rules.

| Declaration / Mark | Requirement |
|---|---|
| Health warning | Graphic (pictorial) + textual warning covering **≥ 85%** of the principal display area, on both faces |
| Warning text | Specified (e.g., "Tobacco causes cancer" / "Tobacco causes painful death") |
| Quitline | 1800-11-2356 |
| Prohibited descriptors | No "light", "mild", or similar misleading terms |

### 9. Textiles / Garments
**Authority:** Legal Metrology + textile labelling norms (some items now under BIS).

| Declaration / Mark | Requirement |
|---|---|
| Size | Size designation |
| Fibre composition | % by weight of each fibre |
| Care instructions | Wash/care symbols |
| Origin & maker | Manufacturer/importer, country of origin |

### 10. Agrochemicals / Insecticides
**Authority:** Insecticides Act, 1968 + Insecticides Rules, 1971 (CIB&RC).

| Declaration / Mark | Requirement |
|---|---|
| Registration number | CIB&RC registration |
| Toxicity triangle | Colour-coded: **red** (extremely toxic) / **yellow** (highly) / **blue** (moderately) / **green** (slightly) |
| Safety statements | Antidote statement, symptoms of poisoning, first-aid, cautions |
| Hazard symbols | Skull-and-crossbones/"POISON" where applicable |
| Handling | Directions for use, "KEEP OUT OF REACH OF CHILDREN", batch, mfg + expiry |

### 11. Household / Hazardous Chemicals
**Authority:** GHS-aligned rules + product-specific regulations.

| Declaration / Mark | Requirement |
|---|---|
| GHS pictograms | Hazard pictograms (flame, corrosion, etc.) |
| Signal word | "Danger" / "Warning" |
| Statements | Hazard & precautionary statements, first-aid, safety warnings |

---

## Cross-cutting visual marks the vision system should detect

Beyond text, the CV layer should recognise these **symbols/logos** (each is a strong, demo-friendly detection):

- **ISI mark** (BIS) · **CRS** registration mark · **BIS Hallmark + HUID**
- **FSSAI logo + licence** · **Veg/Non-veg dot** (green/brown)
- **BEE Star Rating** label · **E-waste** crossed-bin symbol · plastic/recycling codes
- **GHS hazard pictograms** · **Insecticide toxicity triangle** · tobacco **pictorial warnings**
- **AGMARK** (graded agri produce: spices, honey, ghee, etc.) · **India Organic / Jaivik Bharat** (organic)
- **Silk Mark / Wool Mark / Handloom Mark** (textiles) · **Rx / Schedule-H red line** (drugs) · "For external use only" (cosmetics)

---

## Encoding a category pack (and how detection works)

**Category detection:** the vision-LLM returns a `category` (and confidence) alongside the extracted fields — e.g., `"category": "packaged_food"`. A simple map selects which packs to apply; the base Legal Metrology pack is always included. A barcode/GTIN lookup can confirm the category when available.

```json
{
  "pack_id": "cosmetics_2020",
  "label": "Cosmetics (D&C Act + Cosmetics Rules 2020)",
  "authority": "CDSCO",
  "applies_when": { "category_in": ["cosmetic", "personal_care"] },
  "version": "2020",
  "declarations": [
    {
      "id": "mfg_licence_no",
      "label": "Manufacturing Licence Number",
      "legal_reference": "Cosmetics Rules 2020",
      "required": true,
      "severity": "critical",
      "checks": [
        { "type": "presence" },
        { "type": "format", "regex": "(?i)(mfg\\.?\\s*lic\\.?\\s*no|manufacturing licen[cs]e)" }
      ]
    },
    {
      "id": "ingredients_over_10g",
      "label": "Ingredient list (packs > 10 g / 10 ml)",
      "legal_reference": "Cosmetics Rules 2020",
      "required": true,
      "severity": "major",
      "checks": [ { "type": "presence" } ]
    },
    {
      "id": "external_use_warning",
      "label": "'For external use only'",
      "legal_reference": "Cosmetics Rules 2020",
      "required": false,
      "severity": "minor",
      "checks": [ { "type": "presence" } ]
    }
  ]
}
```

The engine runs `Legal Metrology pack` + every pack whose `applies_when` matches the detected category, merges the results, and produces one verdict with each violation tagged to its own law.

---

## Priority order for building packs (hackathon lens)

1. **Legal Metrology** — the mandatory core (already the MVP).
2. **FSSAI (food)** — biggest category; supercharges consumer mode (veg/non-veg, allergens, licence).
3. **Cosmetics & Drugs** — common, high-impact, mostly text checks (licence no., batch, expiry, Schedule warnings).
4. **Electronics / BIS-ISI / BEE** — great for the *visual mark detection* demo (ISI, star label, e-waste).
5. The rest (hallmark, tobacco, agrochem, textiles) — breadth for the pitch / finale.

Each new pack is **just a JSON file** — so you can claim "supports N regulations" and add more without touching code.

---

## Verification & primary sources

Load exact values from these before shipping a live checker:

- **Legal Metrology:** India Code (indiacode.nic.in); Dept. of Consumer Affairs / Legal Metrology (consumeraffairs.nic.in)
- **FSSAI:** fssai.gov.in — Labelling & Display Regulations, 2020
- **Drugs & Cosmetics:** CDSCO (cdsco.gov.in) — D&C Act/Rules; Cosmetics Rules 2020
- **BIS (ISI / CRS / Hallmark):** bis.gov.in ("Products under Mandatory Certification"); crsbis.in (electronics)
- **BEE:** beestarlabel.com / beeindia.gov.in
- **E-waste:** CPCB (cpcb.nic.in) — E-Waste Management Rules, 2022
- **Tobacco:** COTPA rules (mohfw.gov.in)
- **Insecticides:** CIB&RC (ppqs.gov.in)
- Gazette notifications / QCOs: egazette.gov.in

> Because every pack is versioned by effective date, when a rule is amended you update the JSON and the audit trail still shows which version each past scan was judged against.

---

*Companion to ARCHITECTURE.md — Label Jaano regulatory reference v1.0.*

