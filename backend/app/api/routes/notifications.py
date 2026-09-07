"""R3-06 (O-01, O-02, O-04): staff surface for the notification service -
listing what was sent, template management, and a manual trigger for the
event types that have no automated upstream workflow yet (see
NOTIFICATION_EVENT_TYPES's docstring in app/models/entities.py)."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_org_scope, require_permission
from app.core.database import get_db
from app.core.rbac import PERM_AUDIT_VIEW, PERM_CONSENT_MANAGE
from app.models.entities import Notification, NotificationTemplate, User
from app.schemas.schemas import (
    NotificationDeliveryMetricsOut as _NotificationDeliveryMetricsOut,
    NotificationOut,
    NotificationTemplateIn,
    NotificationTemplateOut,
    NotificationTemplateUpdate,
    NotificationTriggerIn,
)
from app.services.audit import log_audit
from app.services.kpi import notification_delivery_metrics
from app.services.notifications import queue_notification
from app.services.tenancy import ANY_TENANT, platform_tenant_id, resolve_customer

router = APIRouter(prefix="/notifications", tags=["notifications"])


class NotificationDeliveryMetricsOut(_NotificationDeliveryMetricsOut):
    """K-44/K-45: widens both rate fields to Optional. Zero attempted (or
    zero delivered) notifications means the respective rate is undefined,
    not "100% delivered" / "0% acknowledged". Overridden locally rather
    than in app/schemas/schemas.py, which this lane does not own - see
    notification_delivery_metrics() in app/services/kpi.py."""

    notification_delivery_rate_pct: Optional[float] = None
    notification_ack_rate_pct: Optional[float] = None


@router.get("", response_model=list[NotificationOut])
def list_notifications(
    customer_external_id: str | None = None,
    event_type: str | None = None,
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_AUDIT_VIEW)),
):
    scope = get_org_scope(user)
    q = db.query(Notification)
    if scope:
        q = q.filter(Notification.source_app == scope)
    if customer_external_id:
        customer = resolve_customer(db, source_app=scope or ANY_TENANT, external_id=customer_external_id)
        if not customer:
            return []
        q = q.filter(Notification.customer_id == customer.id)
    if event_type:
        q = q.filter(Notification.event_type == event_type)
    if status:
        q = q.filter(Notification.status == status)
    rows = q.order_by(Notification.created_at.desc()).limit(limit).all()
    return [NotificationOut.model_validate(n) for n in rows]


@router.get("/metrics", response_model=NotificationDeliveryMetricsOut)
def notification_metrics(db: Session = Depends(get_db), _: User = Depends(require_permission(PERM_AUDIT_VIEW))):
    """K-44/K-45: delivery rate and acknowledgement rate, computed live (the
    same figures app/jobs/kpi_rollup_job.py snapshots periodically into
    kpi_snapshots)."""
    return NotificationDeliveryMetricsOut(**notification_delivery_metrics(db))


@router.post("/trigger", response_model=list[NotificationOut])
def trigger_notification(
    payload: NotificationTriggerIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_CONSENT_MANAGE)),
):
    """Manually queue a notification for one of the event types that has no
    automated trigger wired up yet (erasure warning, breach notice, request/
    grievance status, legacy notice) - see NOTIFICATION_EVENT_TYPES."""
    scope = get_org_scope(user)
    customer = resolve_customer(db, source_app=scope or ANY_TENANT, external_id=payload.customer_external_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    source_app = scope or customer.source_app
    queued = queue_notification(
        db, customer=customer, event_type=payload.event_type, source_app=source_app,
        channels=payload.channels, language=payload.language, context=payload.context,
        actor_username=user.username,
    )
    if not queued:
        raise HTTPException(
            status_code=422,
            detail="No channel could be queued - the customer has no recipient on file for any requested channel",
        )
    return [NotificationOut.model_validate(n) for n in queued]


def _template_out(t: NotificationTemplate) -> NotificationTemplateOut:
    return NotificationTemplateOut.model_validate(t)


@router.get("/templates", response_model=list[NotificationTemplateOut])
def list_templates(db: Session = Depends(get_db), _: User = Depends(require_permission(PERM_AUDIT_VIEW))):
    return [_template_out(t) for t in db.query(NotificationTemplate).order_by(
        NotificationTemplate.event_type, NotificationTemplate.channel, NotificationTemplate.language
    ).all()]


@router.post("/templates", response_model=NotificationTemplateOut, status_code=201)
def create_template(
    payload: NotificationTemplateIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_CONSENT_MANAGE)),
):
    tenant_id = platform_tenant_id(db)
    existing = (
        db.query(NotificationTemplate)
        .filter(
            NotificationTemplate.tenant_id == tenant_id,
            NotificationTemplate.event_type == payload.event_type,
            NotificationTemplate.channel == payload.channel,
            NotificationTemplate.language == payload.language,
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="A template already exists for this event_type/channel/language")
    template = NotificationTemplate(
        tenant_id=tenant_id, event_type=payload.event_type, channel=payload.channel,
        language=payload.language, subject=payload.subject, body_template=payload.body_template,
        is_active=payload.is_active, created_by=user.username,
    )
    db.add(template)
    db.commit()
    db.refresh(template)
    log_audit(db, "NOTIFICATION_QUEUED", actor_username=user.username, actor_type="USER",
              actor_id=str(user.id), source_app="UI",
              reason=f"Notification template created: {payload.event_type}/{payload.channel}/{payload.language}",
              metadata={"template_id": template.id})
    return _template_out(template)


@router.put("/templates/{template_id}", response_model=NotificationTemplateOut)
def update_template(
    template_id: int,
    payload: NotificationTemplateUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_CONSENT_MANAGE)),
):
    template = db.get(NotificationTemplate, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    if payload.subject is not None:
        template.subject = payload.subject
    if payload.body_template is not None:
        template.body_template = payload.body_template
    if payload.is_active is not None:
        template.is_active = payload.is_active
    db.commit()
    db.refresh(template)
    return _template_out(template)
