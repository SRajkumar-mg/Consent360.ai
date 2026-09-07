"""R1-08/D-05/M-02: log every disclosure of personal data to a transferee
fiduciary/processor, so an access-right response (E-01, future work) can
list "who was this shared with and what was shared"."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_org_scope, require_permission
from app.core.database import get_db
from app.core.encryption import hmac_signature, hmac_signature_matches
from app.core.rbac import PERM_CONSENT_MANAGE, PERM_CONSENT_VIEW
from app.models.entities import Consent, DataCategory, DataSharingEvent, Processor, Purpose, User
from app.schemas.schemas import DataSharingEventIn, DataSharingEventOut
from app.services.audit import log_audit
from app.services.tenancy import ANY_TENANT, resolve_customer, resolve_tenant_id

router = APIRouter(prefix="/sharing-events", tags=["sharing-events"])


def _signature_parts(event: DataSharingEvent) -> tuple[str, ...]:
    # .astimezone(timezone.utc) before .isoformat(): psycopg2 returns a
    # DateTime(timezone=True) column converted to the DB session's local
    # timezone, not necessarily UTC, so the same instant round-trips through
    # Postgres with a *different* (but equal) offset than the naive
    # datetime.now(timezone.utc) this was first signed with - e.g. "+00:00"
    # becomes "+08:00" with the wall-clock time shifted to match. Normalising
    # to UTC first makes the string - and therefore the signature - stable
    # across a create-then-reload round trip, not just at creation time.
    occurred_at_utc = event.occurred_at.astimezone(timezone.utc)
    return (
        str(event.customer_id), str(event.processor_id), str(event.purpose_id),
        event.event_type, ",".join(str(i) for i in sorted(event.data_category_ids)),
        occurred_at_utc.isoformat(),
    )


def _sign(event: DataSharingEvent) -> str:
    return hmac_signature(*_signature_parts(event))


def _signature_valid(event: DataSharingEvent) -> bool:
    """Verified through hmac_signature_matches, which accepts a signature
    produced under any key in the HMAC key ring. A plain
    `event.signature == _sign(event)` would mark every disclosure logged
    before an HMAC key rotation as tampered - see
    app/services/receipts.py::verify_receipt for the same reasoning."""
    return hmac_signature_matches(event.signature, *_signature_parts(event))


def _out(event: DataSharingEvent) -> DataSharingEventOut:
    codes = [c.code for c in _categories_by_id(event).values()]
    return DataSharingEventOut(
        id=event.id, customer_id=event.customer_id, processor_id=event.processor_id,
        processor_name=event.processor.name if event.processor else "",
        purpose_id=event.purpose_id, purpose_code=event.purpose.code if event.purpose else "",
        consent_id=event.consent_id, data_category_ids=event.data_category_ids or [],
        data_category_codes=codes, event_type=event.event_type, legal_basis=event.legal_basis,
        reason=event.reason, actor_username=event.actor_username, source_app=event.source_app,
        occurred_at=event.occurred_at, signature_valid=_signature_valid(event),
    )


def _categories_by_id(event: DataSharingEvent) -> dict:
    # Populated by the caller via a query, cached on the instance to avoid
    # re-querying per row when listing; see list_sharing_events.
    return getattr(event, "_category_lookup", {})


@router.post("", response_model=DataSharingEventOut, status_code=201)
def create_sharing_event(payload: DataSharingEventIn, db: Session = Depends(get_db),
                         current_user: User = Depends(require_permission(PERM_CONSENT_MANAGE))):
    scope = get_org_scope(current_user)
    customer = resolve_customer(db, source_app=scope or ANY_TENANT, external_id=payload.customer_external_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    purpose = db.query(Purpose).filter(Purpose.code == payload.purpose_code).first()
    if not purpose:
        raise HTTPException(status_code=404, detail="Purpose not found")

    processor = db.get(Processor, payload.processor_id)
    if not processor or not processor.is_active:
        raise HTTPException(status_code=422, detail="Processor not found or not active")

    categories = db.query(DataCategory).filter(DataCategory.code.in_(payload.data_category_codes)).all()
    found_codes = {c.code for c in categories}
    missing = set(payload.data_category_codes) - found_codes
    if missing:
        raise HTTPException(status_code=404, detail=f"Unknown data category codes: {sorted(missing)}")

    consent = None
    if payload.consent_id is not None:
        consent = db.get(Consent, payload.consent_id)
        if not consent or consent.customer_id != customer.id:
            raise HTTPException(status_code=400, detail="consent_id does not belong to this customer")

    event = DataSharingEvent(
        tenant_id=resolve_tenant_id(db, customer.source_app),
        customer_id=customer.id, processor_id=processor.id, purpose_id=purpose.id,
        consent_id=consent.id if consent else None,
        data_category_ids=[c.id for c in categories],
        event_type=payload.event_type, legal_basis=purpose.legal_basis, reason=payload.reason,
        actor_username=current_user.username, source_app=customer.source_app,
        occurred_at=datetime.now(timezone.utc), signature="",
    )
    event.signature = _sign(event)
    db.add(event)
    db.flush()

    log_audit(db, "SHARING_EVENT_LOGGED", actor_username=current_user.username,
              actor_role=current_user.role.name if current_user.role else "",
              actor_type="USER", actor_id=str(current_user.id), source_app=customer.source_app,
              customer_id=customer.id, customer_external_id=customer.external_id,
              consent_id=consent.id if consent else None, purpose_id=purpose.id, purpose_code=purpose.code,
              reason=f"Data shared with processor {processor.name} ({payload.event_type})",
              metadata={"processor_id": processor.id, "data_category_codes": sorted(found_codes)})
    db.commit()
    db.refresh(event)

    # R3-07/M-02 sharing-event hook: tell the processor what it just received
    # so the two sides' disclosure logs can be reconciled. Best-effort - a
    # processor register problem must not fail the disclosure log itself,
    # which is the record of legal significance here. Only SENT events are
    # notified and only when the processor has a webhook configured; see
    # services/processors.py::raise_sharing_event_alert.
    try:
        from app.services.processors import raise_sharing_event_alert

        raise_sharing_event_alert(db, event, customer=customer, actor_username=current_user.username)
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger("app.processors").exception(
            "Failed to raise sharing-event alert for data_sharing_event id=%s", event.id
        )

    event._category_lookup = {c.id: c for c in categories}
    return _out(event)


@router.get("", response_model=list[DataSharingEventOut])
def list_sharing_events(
    customer_external_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_CONSENT_VIEW)),
):
    scope = get_org_scope(user)
    q = db.query(DataSharingEvent)
    if customer_external_id:
        customer = resolve_customer(db, source_app=scope or ANY_TENANT, external_id=customer_external_id)
        if not customer:
            return []
        q = q.filter(DataSharingEvent.customer_id == customer.id)
    elif scope:
        q = q.filter(DataSharingEvent.source_app == scope)
    events = q.order_by(DataSharingEvent.occurred_at.desc()).limit(200).all()
    all_cat_ids = {cid for e in events for cid in (e.data_category_ids or [])}
    cat_lookup = {c.id: c for c in db.query(DataCategory).filter(DataCategory.id.in_(all_cat_ids)).all()} if all_cat_ids else {}
    for e in events:
        e._category_lookup = {cid: cat_lookup[cid] for cid in (e.data_category_ids or []) if cid in cat_lookup}
    return [_out(e) for e in events]


@router.get("/{event_id}", response_model=DataSharingEventOut)
def get_sharing_event(event_id: int, db: Session = Depends(get_db),
                      user: User = Depends(require_permission(PERM_CONSENT_VIEW))):
    scope = get_org_scope(user)
    event = db.get(DataSharingEvent, event_id)
    if not event or (scope and event.source_app != scope):
        raise HTTPException(status_code=404, detail="Sharing event not found")
    event._category_lookup = {
        c.id: c for c in db.query(DataCategory).filter(DataCategory.id.in_(event.data_category_ids or [])).all()
    }
    return _out(event)
