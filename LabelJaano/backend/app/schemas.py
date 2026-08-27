"""
Pydantic models for the HTTP API.

These mirror the engine's scan-input contract (request) and ComplianceReport
(response). They exist so FastAPI can validate inputs and auto-generate the Swagger
docs at ``/docs`` — the engine itself stays pure-dataclass and dependency-free.
The request model is converted to the engine's ``ScanInput`` via ``ScanInput.from_dict``.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Request
# --------------------------------------------------------------------------- #
class FieldIn(BaseModel):
    value: Optional[str] = None
    raw_text: Optional[str] = None
    panel: Optional[str] = None
    height_mm: Optional[float] = None
    confidence: Optional[float] = None


class ContextIn(BaseModel):
    # generic (base packs)
    is_imported: bool = False
    is_single_ingredient: bool = False
    has_additives: bool = False
    has_allergens: bool = False
    dimension_relevant: bool = False
    pdp_area_cm2: Optional[float] = None
    # category-specific triggers (FSSAI 2016/2017/2018 packs). Every flag a rule
    # pack references via a `condition` MUST appear here, or the request model
    # drops it and the corresponding rule silently stops firing. Keep in sync with
    # pipeline.prompts.CONTEXT_KEYS and rule_engine.models.ScanContext.
    is_organic: bool = False
    is_fortified: bool = False
    is_iron_fortified: bool = False
    is_wine: bool = False
    is_low_alcohol: bool = False
    has_artificial_sweetener: bool = False
    has_aspartame: bool = False
    has_caffeine: bool = False
    has_added_colour: bool = False
    has_added_flavour: bool = False
    has_added_msg: bool = False
    is_irradiated: bool = False
    is_pan_masala: bool = False


class ScanRequest(BaseModel):
    """Normalized OCR + vision-LLM output for one label."""
    category: str = "unknown"
    raw_text: str = ""
    fields: dict[str, FieldIn] = Field(default_factory=dict)
    symbols_detected: list[str] = Field(default_factory=list)
    context: ContextIn = Field(default_factory=ContextIn)

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "category": "packaged_food",
                    "raw_text": "Glucose Biscuits. Net Quantity 76 g. Maximum Retail "
                                "Price Rs 10 inclusive of all taxes. MFG 03/2026. "
                                "Consumer Care: care@brand.com. FSSAI Lic No "
                                "10012345678901. Use by 12/2026. Batch No L2026-045.",
                    "fields": {
                        "manufacturer_details": {"value": "Brand Foods Pvt Ltd, Pune, Maharashtra 411001"},
                        "generic_name": {"value": "Glucose Biscuits"},
                        "net_quantity": {"value": "76 g", "panel": "principal_display_panel", "height_mm": 2.6},
                        "manufacture_date": {"value": "MFG 03/2026"},
                        "mrp": {"value": "Maximum Retail Price Rs 10 inclusive of all taxes", "panel": "principal_display_panel", "height_mm": 3.1},
                        "consumer_care": {"value": "care@brand.com, 1800-123-4567"},
                        "ingredients_list": {"value": "Wheat Flour, Sugar, Oil"},
                        "nutritional_info": {"value": "Energy 450 kcal, Protein 7 g per 100 g"},
                        "fssai_license": {"value": "FSSAI Lic No 10012345678901"},
                        "date_marking": {"value": "Use by 12/2026"},
                        "lot_batch_number": {"value": "Batch No L2026-045"}
                    },
                    "symbols_detected": ["veg_nonveg_mark", "fssai_logo"],
                    "context": {"pdp_area_cm2": 300}
                }
            ]
        }
    }


# --------------------------------------------------------------------------- #
# Response
# --------------------------------------------------------------------------- #
class ViolationOut(BaseModel):
    declaration_id: str
    declaration_label: str
    legal_reference: str
    severity: str
    check_type: str
    message: str
    detail: Optional[str] = None


class CheckResultOut(BaseModel):
    declaration_id: str
    declaration_label: str
    legal_reference: str
    severity: str
    check_type: str
    outcome: str
    message: Optional[str] = None
    detail: Optional[str] = None


class ReferenceStandardOut(BaseModel):
    """A Tier-2, reference-only provision. Applies to the product but a label photo
    cannot verify it (composition, additive limits, heavy-metal caps, lab-only safety
    parameters), so it is surfaced for lab follow-up and NEVER scored — it can move
    neither the score nor the verdict. Mirrors ComplianceReport.reference_standards[]."""
    id: str
    label: str
    legal_reference: str
    authority: Optional[str] = None
    note: Optional[str] = None


class ReportOut(BaseModel):
    verdict: str
    score: float
    category: str
    packs_applied: list[str]
    summary: dict[str, Any]
    violations: list[ViolationOut]
    results: list[CheckResultOut]
    reference_standards: list[ReferenceStandardOut] = Field(default_factory=list)


class PackInfo(BaseModel):
    pack_id: str
    label: str
    authority: str
    version: str
    scope: str
    applies_when: dict[str, Any]
    declarations: int
