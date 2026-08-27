"""
Data models for the Label Jaano rule engine.

Two families of models:

1. **Rule-pack models** (`Check`, `Declaration`, `Pack`, `RuleSet`) — parsed from the
   JSON files in ``rulepacks/``. These describe *what* the law requires.

2. **Scan models** (`Field`, `ScanContext`, `ScanInput`) — the normalized output of the
   OCR + vision-LLM pipeline. These describe *what a specific label actually says*.

3. **Result models** (`CheckResult`, `Violation`, `ComplianceReport`) — what the engine
   produces after judging a scan against a rule set.

The whole module is pure-stdlib (dataclasses only) so the engine runs with a bare
``python3`` — no dependencies to install before you can test it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class Severity(str, Enum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"

    @property
    def weight(self) -> int:
        return {"critical": 3, "major": 2, "minor": 1}[self.value]


class Outcome(str, Enum):
    PASS = "pass"       # counts toward score, no violation
    FAIL = "fail"       # counts toward score, emits a violation
    SKIP = "skip"       # not applicable / could not evaluate — excluded from scoring


class Verdict(str, Enum):
    COMPLIANT = "compliant"
    NEEDS_REVIEW = "needs_review"
    NON_COMPLIANT = "non_compliant"
    NO_LABEL = "no_label_detected"   # image carries no readable packaged-commodity label


# --------------------------------------------------------------------------- #
# Rule-pack models
# --------------------------------------------------------------------------- #
@dataclass
class Check:
    """A single test inside a declaration. See rulepacks/README.md for semantics."""
    type: str
    message: Optional[str] = None
    # format
    regex: Optional[str] = None
    target: str = "raw_text"          # "raw_text" | "normalized"
    negate: bool = False              # format: PASS when the pattern is ABSENT (prohibitions)
    # value
    validator: Optional[str] = None
    params: dict = field(default_factory=dict)
    # placement
    panel: Optional[str] = None
    # font_height
    source: Optional[str] = None      # name of a table, e.g. "font_height_table"
    min_height_mm: Optional[float] = None
    # symbol
    symbol: Optional[str] = None

    @staticmethod
    def from_dict(d: dict) -> "Check":
        return Check(
            type=d["type"],
            message=d.get("message"),
            regex=d.get("regex"),
            target=d.get("target", "raw_text"),
            negate=d.get("negate", False),
            validator=d.get("validator"),
            params=d.get("params", {}),
            panel=d.get("panel"),
            source=d.get("source"),
            min_height_mm=d.get("min_height_mm"),
            symbol=d.get("symbol"),
        )


@dataclass
class Declaration:
    id: str
    label: str
    legal_reference: str
    severity: Severity
    required: bool = True
    condition: str = "always"
    checks: list[Check] = field(default_factory=list)

    @staticmethod
    def from_dict(d: dict) -> "Declaration":
        return Declaration(
            id=d["id"],
            label=d["label"],
            legal_reference=d["legal_reference"],
            severity=Severity(d["severity"]),
            required=d.get("required", True),
            condition=d.get("condition", "always"),
            checks=[Check.from_dict(c) for c in d.get("checks", [])],
        )


@dataclass
class ReferenceStandard:
    """
    A Tier-2, *reference-only* provision.

    These are legal requirements that genuinely apply to the product but that a
    label-image scanner **cannot verify** — e.g. compositional minima (butter
    >= 80% milk fat), additive INS use-limits, heavy-metal caps. They are
    surfaced in the report as "applicable standard; verify in lab" and are
    NEVER turned into a Check / CheckResult / Violation, so they can never move
    the compliance score or verdict. Keeping them in a separate data path (not a
    flag on Declaration) makes a scoring leak structurally impossible.
    """
    id: str
    label: str
    legal_reference: str
    authority: str = ""
    note: str = ""
    condition: str = "always"

    @staticmethod
    def from_dict(d: dict) -> "ReferenceStandard":
        return ReferenceStandard(
            id=d["id"],
            label=d["label"],
            legal_reference=d["legal_reference"],
            authority=d.get("authority", ""),
            note=d.get("note", ""),
            condition=d.get("condition", "always"),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "legal_reference": self.legal_reference,
            "authority": self.authority,
            "note": self.note,
        }


@dataclass
class Pack:
    pack_id: str
    label: str
    authority: str
    scope: str                        # "base" | "category"
    version: str
    applies_when: dict
    declarations: list[Declaration]
    font_height_table: Optional[dict] = None
    scoring: dict = field(default_factory=dict)
    reference_standards: list[ReferenceStandard] = field(default_factory=list)

    @staticmethod
    def from_dict(d: dict) -> "Pack":
        return Pack(
            pack_id=d["pack_id"],
            label=d.get("label", d["pack_id"]),
            authority=d.get("authority", ""),
            scope=d.get("scope", "category"),
            version=d.get("version", ""),
            applies_when=d.get("applies_when", {}),
            declarations=[Declaration.from_dict(x) for x in d.get("declarations", [])],
            font_height_table=d.get("font_height_table"),
            scoring=d.get("scoring", {}),
            reference_standards=[
                ReferenceStandard.from_dict(x) for x in d.get("reference_standards", [])
            ],
        )

    def applies_to(self, category: str) -> bool:
        aw = self.applies_when or {}
        if aw.get("always"):
            return True
        return category in (aw.get("category_in") or [])


@dataclass
class RuleSet:
    """The merged set of packs that apply to one scan."""
    declarations: list[Declaration]
    font_height_table: Optional[dict]
    packs_applied: list[str]
    weights: dict = field(default_factory=lambda: {"critical": 3, "major": 2, "minor": 1})
    reference_standards: list[ReferenceStandard] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Scan models  (output of OCR + vision-LLM, normalized)
# --------------------------------------------------------------------------- #
@dataclass
class Field:
    """One extracted declaration value, keyed in ScanInput.fields by declaration id."""
    value: Optional[str] = None
    raw_text: Optional[str] = None
    panel: Optional[str] = None        # e.g. "principal_display_panel"
    height_mm: Optional[float] = None  # measured glyph height (None = not measured)
    confidence: Optional[float] = None

    @property
    def present(self) -> bool:
        return self.value is not None and str(self.value).strip() != ""

    @staticmethod
    def from_dict(d: Any) -> "Field":
        # allow a bare string as shorthand for {"value": "..."}
        if isinstance(d, str):
            return Field(value=d)
        return Field(
            value=d.get("value"),
            raw_text=d.get("raw_text"),
            panel=d.get("panel"),
            height_mm=d.get("height_mm"),
            confidence=d.get("confidence"),
        )


@dataclass
class ScanContext:
    is_imported: bool = False
    is_single_ingredient: bool = False
    has_additives: bool = False
    has_allergens: bool = False
    dimension_relevant: bool = False
    pdp_area_cm2: Optional[float] = None
    # --- narrow product triggers (each defaults False so the matching rule only
    #     fires when the pipeline positively detects the trait; see checks.condition_met.
    #     NB: an UNREGISTERED condition token evaluates True, so every trigger a pack
    #     references MUST be declared here.) ---
    is_organic: bool = False
    is_fortified: bool = False
    is_iron_fortified: bool = False
    is_wine: bool = False
    is_low_alcohol: bool = False           # alcoholic beverage < 10% abv
    has_artificial_sweetener: bool = False
    has_aspartame: bool = False
    has_caffeine: bool = False             # caffeine added as an ingredient
    has_added_colour: bool = False
    has_added_flavour: bool = False
    has_added_msg: bool = False            # monosodium glutamate added
    is_irradiated: bool = False
    is_pan_masala: bool = False

    @staticmethod
    def from_dict(d: dict) -> "ScanContext":
        d = d or {}
        return ScanContext(
            is_imported=d.get("is_imported", False),
            is_single_ingredient=d.get("is_single_ingredient", False),
            has_additives=d.get("has_additives", False),
            has_allergens=d.get("has_allergens", False),
            dimension_relevant=d.get("dimension_relevant", False),
            pdp_area_cm2=d.get("pdp_area_cm2"),
            is_organic=d.get("is_organic", False),
            is_fortified=d.get("is_fortified", False),
            is_iron_fortified=d.get("is_iron_fortified", False),
            is_wine=d.get("is_wine", False),
            is_low_alcohol=d.get("is_low_alcohol", False),
            has_artificial_sweetener=d.get("has_artificial_sweetener", False),
            has_aspartame=d.get("has_aspartame", False),
            has_caffeine=d.get("has_caffeine", False),
            has_added_colour=d.get("has_added_colour", False),
            has_added_flavour=d.get("has_added_flavour", False),
            has_added_msg=d.get("has_added_msg", False),
            is_irradiated=d.get("is_irradiated", False),
            is_pan_masala=d.get("is_pan_masala", False),
        )


@dataclass
class ScanInput:
    category: str = "unknown"
    raw_text: str = ""
    fields: dict[str, Field] = field(default_factory=dict)
    symbols_detected: list[str] = field(default_factory=list)
    context: ScanContext = field(default_factory=ScanContext)

    @staticmethod
    def from_dict(d: dict) -> "ScanInput":
        return ScanInput(
            category=d.get("category", "unknown"),
            raw_text=d.get("raw_text", ""),
            fields={k: Field.from_dict(v) for k, v in (d.get("fields") or {}).items()},
            symbols_detected=list(d.get("symbols_detected") or []),
            context=ScanContext.from_dict(d.get("context")),
        )


# --------------------------------------------------------------------------- #
# Result models
# --------------------------------------------------------------------------- #
@dataclass
class CheckResult:
    declaration_id: str
    declaration_label: str
    legal_reference: str
    severity: Severity
    check_type: str
    outcome: Outcome
    message: Optional[str] = None      # human message (from the pack) on failure
    detail: Optional[str] = None       # engine's evaluation detail (why it passed/failed)

    def to_dict(self) -> dict:
        return {
            "declaration_id": self.declaration_id,
            "declaration_label": self.declaration_label,
            "legal_reference": self.legal_reference,
            "severity": self.severity.value,
            "check_type": self.check_type,
            "outcome": self.outcome.value,
            "message": self.message,
            "detail": self.detail,
        }


@dataclass
class Violation:
    declaration_id: str
    declaration_label: str
    legal_reference: str
    severity: Severity
    check_type: str
    message: str
    detail: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "declaration_id": self.declaration_id,
            "declaration_label": self.declaration_label,
            "legal_reference": self.legal_reference,
            "severity": self.severity.value,
            "check_type": self.check_type,
            "message": self.message,
            "detail": self.detail,
        }


@dataclass
class ComplianceReport:
    verdict: Verdict
    score: float
    category: str
    packs_applied: list[str]
    violations: list[Violation]
    results: list[CheckResult]
    summary: dict
    reference_standards: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "score": self.score,
            "category": self.category,
            "packs_applied": self.packs_applied,
            "summary": self.summary,
            "violations": [v.to_dict() for v in self.violations],
            "results": [r.to_dict() for r in self.results],
            "reference_standards": self.reference_standards,
        }
