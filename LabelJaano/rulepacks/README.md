# Rule Packs — the engine contract

These JSON files are the **config-driven rule engine** for Label Jaano (see `../ARCHITECTURE.md` §6). Rules are **data, not code**: to change what the app checks, you edit a pack — you don't touch the engine. This folder is the single source of truth the backend loads at scan time.

```
rulepacks/
├── legal_metrology_2011.json   ← BASE pack — always runs, every commodity
├── fssai_food_2020.json        ← CATEGORY pack — runs when category ∈ food/beverage
└── README.md                   ← this file (the schema + what the engine must implement)
```

---

## How the engine uses these packs

At scan time the compliance service does the following:

1. Load the **base pack** (`scope: "base"`, `applies_when.always: true`) — it always applies.
2. Take the vision-LLM's detected `category` (+ confidence) and load every **category pack** whose `applies_when.category_in` contains that category.
3. Merge the applicable packs into one declaration list (dedupe by `id`; a category pack may override a base declaration by reusing its `id`).
4. For each declaration, skip it if its `condition` is not met (see *Condition tokens*); otherwise run every `check`.
5. Produce one **verdict** (compliant / needs_review / non_compliant) and a **score**, with each failed check emitted as a violation **tagged to its own `legal_reference`** so Legal Metrology and FSSAI findings never get conflated.

Because each pack is versioned (`version` + `effective_date`), the audit trail can always show *which* version a past scan was judged against.

---

## Pack-level fields

| Field | Meaning |
|---|---|
| `pack_id` | Unique id; also the filename stem. |
| `label` | Human-readable name (shown in reports). |
| `authority` | Regulator that owns these rules — printed on violations. |
| `scope` | `"base"` (always applies) or `"category"` (applies conditionally). |
| `applies_when` | `{ "always": true }` for the base pack, or `{ "category_in": [...] }` for category packs. |
| `version`, `effective_date` | Pack version + when it took effect (for the audit trail). |
| `stacks_on` | (Category packs) base packs this one is meant to run alongside. |
| `declarations[]` | The list of things the label must declare/carry (below). |
| `font_height_table` | (Base pack) the Rule 8 area→min-height lookup. |
| `scoring` | How to turn passed/failed checks into a verdict + score. |

## Declaration-level fields

| Field | Meaning |
|---|---|
| `id` | Stable key for the declaration (used to merge/override across packs). |
| `label` | Human-readable name. |
| `legal_reference` | The exact rule/regulation cited on any violation. |
| `condition` | When this declaration applies (see *Condition tokens*). Default `"always"`. |
| `required` | `true` = a missing/failed check is a violation; `false` = advisory (missing is fine, but if present it should still be well-formed). |
| `severity` | `critical` \| `major` \| `minor` — drives the verdict and the score weight. |
| `checks[]` | The individual tests run against the extracted field / image. |

---

## Check types (what the engine must implement)

Every check has a `type`. All support an optional `message` (shown to the user on failure).

| `type` | Params | What it does |
|---|---|---|
| `presence` | — | The field was extracted and is non-empty. |
| `format` | `regex`, `target` (`raw_text` \| `normalized`, default `raw_text`) | The target text matches the regex. Regexes are Python `re` syntax; `(?i)` = case-insensitive. |
| `value` | `validator` (name), optional `params` | Runs a named validator function (below) against the field's value. |
| `placement` | `panel` (e.g. `principal_display_panel`) | The field appears on the required panel (uses OCR bounding boxes / panel segmentation). |
| `font_height` | `source` (table name, e.g. `font_height_table`) or explicit `min_height_mm` | The field's measured height in mm ≥ the minimum (see §7 font-size calibration). |
| `symbol` | `symbol` (name) | A visual mark was detected by the CV layer (below). |

### Validators the engine must implement (`value` checks)

| Validator | Passes when… |
|---|---|
| `nonempty_address` | Value looks like a full postal address (has a locality/PIN-like token, not just a name). |
| `standard_metric_unit` | Net quantity uses g, kg, mg, ml, l, m, cm, or `N`/pieces — not a non-standard unit. |
| `positive_currency` | A positive numeric amount is present (optionally with ₹ / Rs). |
| `valid_month_year_not_future` | Parses to a real month+year that is **not** in the future. |
| `valid_expiry_after_mfg` | Expiry/best-before parses and is **after** the manufacture date. |
| `phone_or_email_present` | Contains a phone number or an email address. |
| `fssai_license_14digit` | Contains a 14-digit numeric licence number. |

> Keep validators pure and unit-tested — they are the part most likely to throw on messy OCR text. Return `(passed: bool, detail: str)` so the detail can flow into the violation message.

### Symbols the CV layer must detect (`symbol` checks)

`veg_nonveg_mark`, `fssai_logo` are needed for the current two packs. Future packs will add: `isi_mark`, `bis_crs_mark`, `bis_hallmark`, `bee_star_label`, `ewaste_bin`, `ghs_pictogram`, `toxicity_triangle`, `agmark`, `india_organic`. Model these as a detector registry keyed by symbol name so new packs need no engine changes.

---

## Condition tokens

A declaration runs only when its `condition` is satisfied. The engine resolves these against the scan context (image + extracted fields + user/product metadata). Current tokens:

| Token | Applies when |
|---|---|
| `always` | Always (default). |
| `imported` | The product is an import (user flag, or "made in"/importer text detected). |
| `dimension_relevant` | The commodity is one where dimensions matter (e.g. sheets, wire, cloth). |
| `not_single_ingredient` | The product has more than one ingredient (single-ingredient foods are exempt from an ingredients list). |
| `has_additives` | Additives appear to be present. |
| `has_allergens` | Known allergens appear to be present. |

Treat an **unknown token as `always`** (fail-safe: check it rather than silently skip).

**`condition` vs `required`:** use `condition` to gate *whether a declaration applies at all* (e.g. `has_allergens`, `imported`); once it applies, set `required: true` if it must then be present. Reserve `required: false` for genuinely optional declarations (e.g. unit sale price, storage instructions) — when those are absent the engine skips them entirely rather than raising a violation.

---

## Severity & scoring

Both packs use the same scheme:

- **Verdict:** any failed `critical` check → `non_compliant`; else any failed `major` → `needs_review`; else `compliant`.
- **Score:** `100 × (weight of passed checks) / (weight of all applicable checks)`, with weights `critical: 3, major: 2, minor: 1`. Advisory (`required: false`) checks only count when the field is present.

This gives the dashboard both a hard verdict (for enforcement) and a soft 0–100 score (for ranking and trend charts).

---

## Adding a new pack (cosmetics, drugs, BIS, …)

1. Copy an existing category pack, set a new `pack_id`, `authority`, `version`, and `applies_when.category_in`.
2. List its `declarations` with the right `legal_reference`, `severity`, and `checks`.
3. Add any new `validator` / `symbol` names to the engine's registries.
4. Drop the file in this folder — **no engine code changes.** That's the whole pitch: *"supporting a new regulation is a config file."*

See `../REGULATORY_PACKS.md` for the per-category requirements (cosmetics, drugs, electronics/BIS, hallmark, tobacco, agrochemicals, …) ready to encode.

---

## ⚠️ Accuracy note

The regexes, validator thresholds, and the Rule 8 font-height values here are **engineered starting points** designed to work on real labels — not verbatim legal text. Before any live/enforcement use, load authoritative values from the primary sources (indiacode.nic.in, consumeraffairs.nic.in, fssai.gov.in) and tune the regexes against a real label dataset. Because everything is config, that tuning never requires an engine change.
