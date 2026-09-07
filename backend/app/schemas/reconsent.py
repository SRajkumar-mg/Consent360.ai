"""R1-09: request/response models for the policy change log, re-consent
campaigns and cookie policy versioning.

Kept out of `schemas/schemas.py` for the same reason `schemas/breach.py`,
`schemas/grievance.py` and `schemas/erasure.py` are - so this feature could be
built without contending for the single shared schema file. The one exception
is `ChangeClassificationOut`, which `schemas.PurposeOut` embeds so a publisher
sees the consequence of their own edit in the response to it.
"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

Materiality = Literal["MATERIAL", "NARROWING", "COSMETIC"]
EntityType = Literal["PURPOSE", "POLICY", "NOTICE", "COOKIE_POLICY"]


class ChangeClassificationOut(BaseModel):
    """What publishing a version actually did.

    Returned inline on `PUT /purposes/{id}` so the person making the edit is
    told, in the same response, that they have just required N principals to
    consent again - rather than finding out later from a metrics page.
    """

    change_ref: str
    materiality: Materiality
    materiality_basis: str
    campaign_ref: Optional[str] = None
    consents_flagged: int = 0
    notifications_queued: int = 0


class PolicyChangeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    change_ref: str
    tenant_id: Optional[int]
    entity_type: str
    entity_id: int
    entity_code: str
    from_version: Optional[int]
    to_version: int
    materiality: str
    changed_fields: dict
    materiality_basis: str
    overridden_by: Optional[str]
    override_justification: str
    affected_consents: int
    campaign_id: Optional[int]
    actor_username: str
    created_at: datetime


class ReConsentCampaignOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    campaign_ref: str
    tenant_id: Optional[int]
    entity_type: str
    entity_id: int
    entity_code: str
    from_version: Optional[int]
    to_version: int
    status: str
    reason: str
    consents_flagged: int
    notifications_queued: int
    fresh_consents: int
    started_by: str
    started_at: datetime
    completed_at: Optional[datetime]
    cancelled_at: Optional[datetime]
    cancel_reason: str


class CampaignCloseIn(BaseModel):
    status: Literal["COMPLETED", "CANCELLED"] = "COMPLETED"
    reason: str = ""


class CookieCategoryIn(BaseModel):
    """One itemised cookie category, as BRD 4.2 / Q-03 asks a cookie policy
    page to disclose it."""

    key: str = Field(min_length=1)
    label: str = ""
    description: str = ""
    #: The Purpose code this category is mirrored onto (see
    #: routes/crm.py::COOKIE_CATEGORY_TO_PURPOSE). Without it, a material
    #: change to this category cannot flag the consents it governs.
    purpose_code: Optional[str] = None
    shared_with: list[str] = []
    duration: str = ""
    strictly_necessary: bool = False


class CookiePolicyIn(BaseModel):
    tenant_code: Optional[str] = None
    categories: list[CookieCategoryIn]
    summary: str = ""
    #: Downgrade a free-text field from MATERIAL to COSMETIC, with the
    #: justification recorded on the change log. Only `description` is
    #: meaningful for a cookie policy (its `summary` maps onto that field);
    #: anything else is refused with 422.
    cosmetic_overrides: dict[str, str] = {}


class CookiePolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: int
    version_number: int
    categories: list
    summary: str
    content_hash: Optional[str]
    is_current: bool
    published_by: str
    published_at: datetime
    change_log_id: Optional[int]
    preferences_invalidated: int


class CookiePolicyPublishOut(BaseModel):
    version_number: int
    content_hash: str
    change_ref: str
    materiality: Materiality
    materiality_basis: str
    campaign_ref: Optional[str] = None
    preferences_invalidated: int = 0
    consents_flagged: int = 0


class ReConsentMetricsOut(BaseModel):
    generated_at: datetime
    campaigns: int
    open_campaigns: int
    consents_flagged: int
    fresh_consents: int
    #: K-09.
    re_consent_rate_pct: Optional[float]
    consents_blocked_now: int
    material_changes: int
    changes_logged: int


class MaterialityRuleOut(BaseModel):
    """One field's rule, served so the definition of "material" is readable
    from the API rather than only from the source."""

    field: str
    kind: str
    always_material: bool
    overridable: bool
    why_material: str
    why_narrowing: str = ""
