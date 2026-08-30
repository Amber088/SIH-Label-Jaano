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


# --------------------------------------------------------------------------- #
# Accounts
# --------------------------------------------------------------------------- #
class UserOut(BaseModel):
    """Public shape of an account. Mirrors ``store.users.User.to_dict()`` — which
    has no ``password_hash`` field at all, so a verifier cannot leak through here
    even by accident."""
    id: str
    email: str
    name: str = ""
    role: str
    role_label: str
    created_at: str
    disabled: bool = False


class SignupRequest(BaseModel):
    email: str = Field(..., description="login identifier; stored lower-cased")
    password: str = Field(..., description="minimum 8 characters")
    name: str = ""
    role: str = Field(
        "consumer",
        description="'consumer', or 'officer' together with a valid officer_code. "
                    "'admin' is never obtainable here — see manage.py.",
    )
    officer_code: Optional[str] = Field(
        None,
        description="shared enrolment code (LABEL_JAANO_OFFICER_CODE) required to "
                    "register as an officer",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"email": "inspector@lm.gov.in", "password": "correct-horse-battery",
                 "name": "A. Jain", "role": "officer", "officer_code": "demo-code"}
            ]
        }
    }


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="token lifetime in seconds")
    user: UserOut


class AuthConfigOut(BaseModel):
    """What the sign-up screen needs to know before it renders.

    Lets the app hide the officer option (and its code field) on a server where
    officer sign-up is switched off, instead of offering a choice that will 403.
    """
    accounts_available: bool
    officer_signup_enabled: bool
    min_password_length: int
    ephemeral_secret: bool = Field(
        ...,
        description="true when LABEL_JAANO_SECRET is unset, so tokens are signed with "
                    "a per-process key and every restart invalidates all sessions",
    )


# --------------------------------------------------------------------------- #
# Scan history
# --------------------------------------------------------------------------- #
class ScanSummaryOut(BaseModel):
    """One stored inspection without its full report — the list-view shape.

    ``summary`` carries the same keys as ``ReportOut.summary`` so the client can
    reuse one parser for a live report and a history row.
    """
    id: str
    created_at: str
    user_id: Optional[str] = None
    verdict: str
    score: float
    category: str
    packs_applied: list[str] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    source: str = "json"
    mock: bool = False
    product_name: Optional[str] = None
    note: Optional[str] = None
    location: Optional[str] = None


class ScanDetailOut(ScanSummaryOut):
    """A stored inspection plus the verbatim report that was shown at the time."""
    report: Optional[dict[str, Any]] = None
    scan_input: Optional[dict[str, Any]] = None


class ScanListOut(BaseModel):
    items: list[ScanSummaryOut]
    total: int = Field(..., description="matching rows before limit/offset")
    limit: int
    offset: int
    scope: str = Field(..., description="'own' or 'all' — which corpus was searched")


class ExtractionProvenanceOut(BaseModel):
    """How the label values in this report were actually obtained.

    Present because the offline fallback is silent by default: with no
    ``GEMINI_API_KEY`` configured, field extraction returns canned values and the
    engine judges those, so any photo — a book, a brick — can come back compliant.
    ``mock`` is true when *any* stage ran offline, and ``reason`` says which and why,
    so a client can put a warning on screen instead of presenting fiction as a finding.
    """
    mock: bool
    ocr_mock: bool
    gemini_mock: bool
    reason: str


class SavedReportOut(ReportOut):
    """A live report, plus the id it was filed under when the caller is signed in.

    ``scan_id`` is null for an anonymous scan: the verdict is returned but nothing
    was written, so there is no record to fetch or export later.
    """
    scan_id: Optional[str] = None
    saved: bool = False
    extraction: Optional[ExtractionProvenanceOut] = Field(
        None,
        description="how the values were obtained; set by the image endpoints, which "
                    "are the ones that can silently fall back to a mock read",
    )


class ReportLinkOut(BaseModel):
    """A short-lived link that renders one stored report without a login.

    ``path`` is relative on purpose. A server behind a reverse proxy or an ngrok
    tunnel does not reliably know the origin the client reached it on — ``Host`` and
    ``X-Forwarded-*`` are attacker-influenced headers — so inventing an absolute URL
    here would sometimes be wrong and would be a redirect-shaped footgun. The client
    already knows the base address it dialled; it joins the two.

    ``ticket`` is exposed separately for clients that would rather build the request
    themselves. See :mod:`auth.tickets` for what it can and cannot do — it is scoped
    to this one inspection, expires in minutes, and is rejected everywhere a session
    token is expected.
    """
    scan_id: str
    path: str = Field(..., description="append to your API base URL, e.g. /scans/<id>/report.html?ticket=<t>")
    ticket: str
    expires_at: str = Field(..., description="ISO 8601, UTC")
    expires_in_seconds: int


class CategoryStatOut(BaseModel):
    category: str
    scans: int
    average_score: float


class TopViolationOut(BaseModel):
    declaration_id: str
    declaration_label: str
    legal_reference: str
    severity: str
    occurrences: int = Field(..., description="failed checks (one label can fail several)")
    scans_affected: int = Field(..., description="distinct products in breach")


class StatsOut(BaseModel):
    """Dashboard aggregates. ``no_label_detected`` reads are excluded from
    ``average_score`` and ``compliance_rate`` — a photo that was not a label at all
    is not a product scoring zero."""
    total_scans: int
    scored_scans: int
    by_verdict: dict[str, int]
    average_score: float
    compliance_rate: float
    violations_by_severity: dict[str, int]
    violations_total: int
    by_category: list[CategoryStatOut] = Field(default_factory=list)
    top_violations: list[TopViolationOut] = Field(default_factory=list)
    scope: str = Field(..., description="'own' or 'all'")


# --------------------------------------------------------------------------- #
# Category discovery
# --------------------------------------------------------------------------- #
class CategoryOut(BaseModel):
    """One product category the engine can score, and what it pulls in.

    The client renders its category picker from this rather than a hardcoded list, so
    adding a rule pack to the rulepacks directory makes its categories selectable
    without shipping a new app build.
    """
    id: str
    label: str
    packs: list[str] = Field(..., description="pack ids that apply to this category")
    declarations: int = Field(..., description="mandatory declarations after merging")
    authorities: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Administration
# --------------------------------------------------------------------------- #
class AdminUserOut(UserOut):
    """An account as an administrator sees it: the public shape plus its scan count.

    Inherits from :class:`UserOut`, which has no password field at all, so extending
    the admin view cannot accidentally widen it to the verifier.
    """
    scans: int = 0


class AdminUserListOut(BaseModel):
    items: list[AdminUserOut]
    total: int
    limit: int
    offset: int
    by_role: dict[str, int] = Field(
        default_factory=dict,
        description="account count per role over the whole table, not just this page",
    )


class AdminUserCreate(BaseModel):
    """Create an account at any role.

    This is the only way an ``admin`` can come into existence over HTTP, and it is
    itself admin-gated — there is deliberately no enrolment code that mints one.
    """
    email: str
    password: str
    name: str = ""
    role: str = Field("consumer", description="consumer | officer | admin")


class AdminUserPatch(BaseModel):
    """Partial update. Omitted fields are left alone; at least one is required."""
    role: Optional[str] = Field(None, description="consumer | officer | admin")
    disabled: Optional[bool] = None


class AuditEntryOut(BaseModel):
    id: int
    created_at: str
    actor_id: Optional[str] = None
    actor_email: str = ""
    actor_role: str = Field("", description="the actor's role AT THE TIME of the action")
    action: str
    target: Optional[str] = None
    detail: Optional[dict[str, Any]] = None


class AuditListOut(BaseModel):
    items: list[AuditEntryOut]
    total: int
    limit: int
    offset: int
