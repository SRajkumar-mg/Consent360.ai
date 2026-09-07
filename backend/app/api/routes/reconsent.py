"""R1-09 (P-01, P-02, P-03, K-09): the policy change log, re-consent
campaigns, the cookie policy, and the materiality rules themselves.

Permissions, and why these ones:

* Reading the change log, campaigns, the current cookie policy and K-09 needs
  **`policy.view`** - it is compliance reporting about published documents,
  available to every role that can already read a policy.
* Publishing a cookie policy version, and closing or cancelling a campaign,
  needs **`policy.manage`** (admin and DPO). Both are decisions about what a
  principal was told and whether they must be asked again.

No new permission pair here, deliberately, unlike R1-06's `erasure.*`. Every
act on this router is a *policy publication* act - the same kind of judgement
`policy.manage` already governs - and inventing a permission for it would
split one job across two grants. Erasure earned its own pair because
destroying one named person's data is a different kind of act, not a heavier
version of the same one.

Note the most important surface of this feature is NOT here: the campaign is
started by `PUT /purposes/{id}` (app/api/routes/purposes.py), which publishes
the version, and the block is enforced by
`app/services/decision_engine.py::evaluate_decision`. This router is where you
read what happened and where the cookie policy is published.

Route order matters: `/re-consent/metrics`, `/re-consent/rules`,
`/re-consent/changes` and `/re-consent/cookie-policy` are all declared before
`/re-consent/campaigns/{campaign_ref}`.

--------------------------------------------------------------------------
R2-11 / A-08: the legacy-notice campaign lives on this router too
--------------------------------------------------------------------------
`/re-consent/legacy-notice/*` is the s.5(2) surface: the pre-Act cohort, the
campaign that notifies it, and the delivery evidence behind each notice.

It is *not* re-consent, and the module docstring of
`app/services/legacy_notice.py` says why the two must not be conflated in the
data model. It shares this router because it shares the question - "is the
consent we are relying on still one the principal actually gave, to what we
are actually doing?" - and, more practically, because it shares the screen:
the admin console's `/re-consent` page grew a tab rather than a sibling page,
so an operator reviewing what principals have been asked sees both drives in
one place. The permissions are the same ones for the same reason: sending a
legacy notice is publishing a notice, which is what `policy.manage` governs.
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_org_scope, require_permission
from app.core.database import get_db
from app.core.rbac import PERM_POLICY_MANAGE, PERM_POLICY_VIEW
from app.core.utils import get_request_id
from app.models.entities import Organization, User
from app.models.reconsent import CookiePolicyVersion, PolicyChangeLog, ReConsentCampaign
from app.schemas.legacy_notice import (
    LegacyCohortMemberOut,
    LegacyCohortOut,
    LegacyNoticeCampaignOut,
    LegacyNoticeMetricsOut,
    LegacyNoticeSendIn,
    LegacyNoticeSendOut,
    NotificationDispatchOut,
)
from app.schemas.reconsent import (
    CampaignCloseIn,
    CookiePolicyIn,
    CookiePolicyOut,
    CookiePolicyPublishOut,
    MaterialityRuleOut,
    PolicyChangeOut,
    ReConsentCampaignOut,
    ReConsentMetricsOut,
)
from app.services import legacy_notice as legacy_notice_service
from app.services import material_change as reconsent_service
from app.services.notifications import dispatch_pending
from app.services.tenancy import platform_tenant_id

router = APIRouter(prefix="/re-consent", tags=["re-consent"])


def _tenant_id(db: Session, tenant_code: Optional[str], user: User) -> int:
    """Resolve the tenant a cookie policy belongs to.

    An org-scoped role (jobhub_admin, codex_admin, skilllearn_admin) is pinned
    to its own tenant and may not name another one - naming a `tenant_code`
    that is not theirs is a 403, not a silent redirect to their own, because
    silently retargeting a publication would put a cookie policy live for the
    wrong audience.
    """
    scope = get_org_scope(user)
    if scope:
        if tenant_code and tenant_code != scope:
            raise HTTPException(
                status_code=403,
                detail="This role may only publish a cookie policy for its own tenant",
            )
        tenant_code = scope
    if not tenant_code:
        return platform_tenant_id(db)
    organization = db.query(Organization).filter(Organization.code == tenant_code).first()
    if not organization:
        raise HTTPException(status_code=404, detail="Unknown tenant_code")
    return organization.id


@router.get("/rules", response_model=list[MaterialityRuleOut])
def materiality_rules(_: User = Depends(require_permission(PERM_POLICY_VIEW))):
    """The definition of "material change", field by field, served from the
    same constant the classifier uses.

    Published as an endpoint rather than left in a source file because a
    publisher deciding whether to edit a purpose needs to know, before they
    edit it, which fields will force every existing consent to be obtained
    again - and because a definition nobody can read is one nobody can
    challenge.
    """
    return [
        MaterialityRuleOut(
            field=rule.field,
            kind=rule.kind,
            always_material=rule.kind in ("widening", "increase", "any", "false_to_true"),
            overridable=rule.field in reconsent_service.OVERRIDABLE_FIELDS,
            why_material=rule.why_material,
            why_narrowing=rule.why_narrowing,
        )
        for rule in reconsent_service.FIELD_RULES
    ]


@router.get("/metrics", response_model=ReConsentMetricsOut)
def metrics(
    db: Session = Depends(get_db),
    _: User = Depends(require_permission(PERM_POLICY_VIEW)),
):
    """K-09: fresh consents divided by consents flagged 're-consent required',
    plus how many consents are blocked right now."""
    return reconsent_service.re_consent_metrics(db)


@router.get("/changes", response_model=list[PolicyChangeOut])
def list_changes(
    entity_type: Optional[str] = Query(default=None),
    materiality: Optional[str] = Query(default=None),
    limit: int = Query(default=100, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(require_permission(PERM_POLICY_VIEW)),
):
    """P-02: every publication of a versioned artefact, material or not.

    Cosmetic rows are here on purpose - a log that recorded only the changes
    someone judged material could not be used to check that judgement, and
    "what did you decide was cosmetic, and why?" is the question an audit
    actually asks.
    """
    q = db.query(PolicyChangeLog)
    if entity_type:
        q = q.filter(PolicyChangeLog.entity_type == entity_type)
    if materiality:
        q = q.filter(PolicyChangeLog.materiality == materiality)
    return q.order_by(PolicyChangeLog.id.desc()).limit(limit).all()


@router.get("/cookie-policy", response_model=Optional[CookiePolicyOut])
def get_cookie_policy(
    tenant_code: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_POLICY_VIEW)),
):
    return reconsent_service.current_cookie_policy(db, _tenant_id(db, tenant_code, user))


@router.get("/cookie-policy/versions", response_model=list[CookiePolicyOut])
def list_cookie_policy_versions(
    tenant_code: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_POLICY_VIEW)),
):
    tenant_id = _tenant_id(db, tenant_code, user)
    return (
        db.query(CookiePolicyVersion)
        .filter(CookiePolicyVersion.tenant_id == tenant_id)
        .order_by(CookiePolicyVersion.version_number.desc())
        .all()
    )


@router.post("/cookie-policy", response_model=CookiePolicyPublishOut, status_code=201)
def publish_cookie_policy(
    payload: CookiePolicyIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_POLICY_MANAGE)),
):
    """P-03: publish a new cookie policy version.

    A MATERIAL change invalidates the stored banner preferences it described
    (server-side, by clearing them - so a client that never got the memo still
    re-asks) and flags the mirrored consent rows, so the decision engine
    blocks processing under those cookie purposes until a fresh choice is
    recorded. Clearing the banner's memory alone would not do that: the
    platform's own consent rows would still say GRANTED.
    """
    tenant_id = _tenant_id(db, payload.tenant_code, user)
    try:
        return reconsent_service.publish_cookie_policy(
            db, tenant_id=tenant_id,
            categories=[c.model_dump() for c in payload.categories],
            summary=payload.summary, actor_username=user.username, source_app="UI",
            request_id=get_request_id(),
            cosmetic_overrides=payload.cosmetic_overrides or None,
        )
    except reconsent_service.MaterialityError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/campaigns", response_model=list[ReConsentCampaignOut])
def list_campaigns(
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=100, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(require_permission(PERM_POLICY_VIEW)),
):
    q = db.query(ReConsentCampaign)
    if status:
        q = q.filter(ReConsentCampaign.status == status)
    return q.order_by(ReConsentCampaign.id.desc()).limit(limit).all()


@router.get("/campaigns/{campaign_ref}", response_model=ReConsentCampaignOut)
def get_campaign(
    campaign_ref: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission(PERM_POLICY_VIEW)),
):
    campaign = (
        db.query(ReConsentCampaign)
        .filter(ReConsentCampaign.campaign_ref == campaign_ref)
        .first()
    )
    if not campaign:
        raise HTTPException(status_code=404, detail="Re-consent campaign not found")
    return campaign


@router.post("/campaigns/{campaign_ref}/close", response_model=ReConsentCampaignOut)
def close_campaign(
    campaign_ref: str,
    payload: CampaignCloseIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_POLICY_MANAGE)),
):
    """Close a campaign.

    CANCELLED lifts the block on every consent the campaign flagged, because
    cancelling means the change was rolled back or superseded - leaving
    principals unprocessable for a change that no longer exists would be the
    mirror image of the failure this feature fixes. COMPLETED leaves any
    remaining flags exactly where they are: the change is still live, so the
    consents that have not been re-given are still not consent to it.
    """
    campaign = (
        db.query(ReConsentCampaign)
        .filter(ReConsentCampaign.campaign_ref == campaign_ref)
        .first()
    )
    if not campaign:
        raise HTTPException(status_code=404, detail="Re-consent campaign not found")
    if campaign.status != "OPEN":
        raise HTTPException(
            status_code=409, detail=f"Campaign {campaign_ref} is already {campaign.status}"
        )
    if payload.status == "CANCELLED" and not payload.reason.strip():
        raise HTTPException(
            status_code=422,
            detail=(
                "A reason is required to cancel a re-consent campaign: cancelling lifts the "
                "block on every consent it flagged, so the record has to say why that is lawful."
            ),
        )
    try:
        return reconsent_service.close_campaign(
            db, campaign, status=payload.status, actor_username=user.username,
            reason=payload.reason, request_id=get_request_id(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


# ---------------------------------------------------------------------------
# R2-11 / A-08 (K-26): the s.5(2) legacy-notice campaign
# ---------------------------------------------------------------------------
# Every path below sits under `/re-consent/legacy-notice/`, whose second
# segment is a literal that cannot collide with `/re-consent/campaigns/
# {campaign_ref}` above - so unlike the routes in the block before it, these
# do not depend on declaration order.
def _legacy_scope(user: User, requested: Optional[str]) -> Optional[str]:
    """The `source_app` a legacy-notice call may act on.

    An org-scoped role is pinned to its own tenant and may not name another
    one. Naming someone else's tenant is a 403 rather than a silent redirect
    to your own, for the same reason `_tenant_id` above refuses it: quietly
    retargeting would send one tenant's principals a notice about a fiduciary
    relationship they do not have, and mailing the wrong cohort is not a
    recoverable mistake.
    """
    scope = get_org_scope(user)
    if scope:
        if requested and requested != scope:
            raise HTTPException(
                status_code=403,
                detail="This role may only run a legacy-notice campaign for its own tenant",
            )
        return scope
    return requested


@router.get("/legacy-notice/cohort", response_model=LegacyCohortOut)
def legacy_cohort(
    cutoff: Optional[str] = Query(
        default=None,
        description=(
            "ISO date taken as the commencement of the Act for this cohort. Defaults to "
            "services/legacy_notice.py::DEFAULT_PRE_ACT_CUTOFF, which is an engineering "
            "default and not a legal determination."
        ),
    ),
    source_app: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_POLICY_VIEW)),
):
    """Who is still being processed under consent that pre-dates the Act.

    Read-only and safe to run repeatedly: it sends nothing. An operator is
    expected to look at this, change the cut-off, look again, and only then
    POST the campaign - which is why the cut-off is a query parameter here
    and a required body field there.
    """
    scope = _legacy_scope(user, source_app)
    cutoff_date = _parse_cutoff(cutoff)
    members = legacy_notice_service.build_cohort(
        db, cutoff=cutoff_date, source_app=scope, include_notified=True
    )
    notified = sum(1 for m in members if m.already_notified)
    return LegacyCohortOut(
        generated_at=datetime.now(timezone.utc),
        cutoff=cutoff_date,
        source_app=scope or "",
        cohort_size=len(members),
        outstanding=len(members) - notified,
        already_notified=notified,
        truncated=len(members) > limit,
        members=[
            LegacyCohortMemberOut(
                customer_external_id=m.external_id,
                source_app=m.source_app,
                consent_count=m.consent_count,
                purpose_names=m.purpose_names,
                oldest_consent_at=m.oldest_consent_at,
                last_notice_status=m.last_notice_status,
                notified_at=m.notified_at,
                last_campaign_ref=m.last_campaign_ref,
                already_notified=m.already_notified,
            )
            for m in members[:limit]
        ],
    )


def _parse_cutoff(raw: Optional[str]):
    from datetime import date as _date

    if not raw:
        return legacy_notice_service.DEFAULT_PRE_ACT_CUTOFF
    try:
        return _date.fromisoformat(raw)
    except ValueError:
        raise HTTPException(status_code=422, detail="cutoff must be an ISO date (YYYY-MM-DD)")


@router.post("/legacy-notice/campaigns", response_model=LegacyNoticeSendOut, status_code=201)
def start_legacy_notice_campaign(
    payload: LegacyNoticeSendIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_POLICY_MANAGE)),
):
    """Queue an s.5(2) notice for every principal in the cohort.

    Returns what was **queued**, never what was delivered - nothing has been
    attempted when this returns. Delivery happens in the notification
    dispatcher (the scheduled job, or POST .../dispatch below when the
    scheduler is off), and only `Notification.status` may be read as the
    answer to "did she get it?".
    """
    scope = _legacy_scope(user, payload.source_app)
    result = legacy_notice_service.send_legacy_notices(
        db,
        cutoff=payload.cutoff,
        source_app=scope,
        channels=payload.channels,
        language=payload.language,
        note=payload.note,
        include_notified=payload.include_notified,
        limit=payload.limit,
        actor_username=user.username,
        request_id=get_request_id(),
    )
    return LegacyNoticeSendOut(**result)


@router.post("/legacy-notice/dispatch", response_model=NotificationDispatchOut)
def dispatch_legacy_notices(
    limit: int = Query(default=500, ge=1, le=2000),
    db: Session = Depends(get_db),
    _: User = Depends(require_permission(PERM_POLICY_MANAGE)),
):
    """Attempt delivery of the queued legacy notices now.

    `SCHEDULER_ENABLED` defaults to false (see docs/ARCHITECTURE.md), so on a deployment
    that has not turned the scheduler on, a queued notice would otherwise sit
    in `PENDING` forever and the compliance screen would be honestly - and
    permanently - reporting that nothing was delivered. This is the button
    that makes it true.

    Scoped to LEGACY_NOTICE. An operator pressing "send the notices I just
    queued" must not also flush an unrelated backlog of breach or erasure
    warnings under their name and their request id.
    """
    return NotificationDispatchOut(**dispatch_pending(db, limit=limit, event_type="LEGACY_NOTICE"))


@router.get("/legacy-notice/metrics", response_model=LegacyNoticeMetricsOut)
def legacy_notice_metrics(
    cutoff: Optional[str] = Query(default=None),
    source_app: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_POLICY_VIEW)),
):
    """K-26: pre-Act consents notified / total pre-Act consents."""
    scope = _legacy_scope(user, source_app)
    return LegacyNoticeMetricsOut(
        **legacy_notice_service.legacy_notice_metrics(
            db, cutoff=_parse_cutoff(cutoff), source_app=scope
        )
    )


@router.get("/legacy-notice/campaigns", response_model=list[LegacyNoticeCampaignOut])
def list_legacy_notice_campaigns(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_POLICY_VIEW)),
):
    """Every legacy-notice campaign, counted from its delivery rows.

    The per-campaign totals are recomputed on every read rather than cached,
    so a retry that succeeded an hour after the campaign closed moves the
    numbers - there is no summary row that can disagree with the notifications
    it summarises.
    """
    scope = get_org_scope(user)
    return [
        LegacyNoticeCampaignOut(**summary)
        for summary in legacy_notice_service.list_campaigns(db, source_app=scope, limit=limit)
    ]


@router.get("/legacy-notice/campaigns/{campaign_ref}", response_model=LegacyNoticeCampaignOut)
def get_legacy_notice_campaign(
    campaign_ref: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_POLICY_VIEW)),
):
    """One campaign plus its per-recipient delivery evidence (s.6(10))."""
    scope = get_org_scope(user)
    detail = legacy_notice_service.campaign_detail(db, campaign_ref, source_app=scope)
    if detail is None:
        raise HTTPException(status_code=404, detail="Legacy-notice campaign not found")
    return LegacyNoticeCampaignOut(**detail)
