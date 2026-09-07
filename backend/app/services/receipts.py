"""R1-08/B-09: consent receipts.

`issue_receipt` is called from `app.services.consent.grant_consent` and
`renew_consent` (the two events the workbook names) inside the same
transaction as the ConsentHistory/ConsentEvidence rows those functions
already write, so a receipt and the event it documents are always committed
together - there is no window where a grant/renew exists without its
receipt.
"""
import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.encryption import hmac_signature, hmac_signature_matches
from app.core.utils import mask_identifier
from app.models.entities import Consent, ConsentEvidence, ConsentReceipt, NoticeVersion
from app.services.audit import log_audit


def _build_payload(consent: Consent, evidence: ConsentEvidence, action: str, receipt_ref: str,
                   notice_version: NoticeVersion | None, issued_at: datetime) -> dict:
    purpose = consent.purpose
    pv = consent.purpose_version
    customer = consent.customer
    return {
        "schema": "ISO/IEC TS 27560:2023 (adapted for DPDP)",
        "receipt_ref": receipt_ref,
        "action": action,
        "consent_record_id": consent.id,
        "consent_version": consent.consent_version,
        "status": consent.status,
        "principal": {
            "customer_id": customer.id,
            "external_id_masked": mask_identifier(customer.external_id),
        },
        "fiduciary": {"tenant_id": consent.tenant_id, "source_app": consent.source_app},
        "purpose": {
            "code": purpose.code, "name": purpose.name,
            "legal_basis": purpose.legal_basis, "version_number": pv.version_number,
            "requires_consent": purpose.requires_consent,
        },
        "data_category": {"code": consent.data_category.code, "name": consent.data_category.name},
        "processing_activity": {
            "code": consent.processing_activity.code, "name": consent.processing_activity.name,
        },
        "collection": {
            "method": evidence.collection_method, "language": evidence.language,
            "affirmative_action": evidence.affirmative_action,
        },
        "notice": (
            {
                "notice_version_id": notice_version.id,
                "version_number": notice_version.version_number,
                "content_hash": notice_version.content_hash,
                "title": notice_version.title,
            }
            if notice_version is not None else None
        ),
        "evidence": {
            "evidence_ref": evidence.evidence_ref,
            "content_hash": evidence.content_hash,
            "notice_hash": evidence.notice_hash,
        },
        "timestamps": {
            "granted_at": consent.granted_at.isoformat() if consent.granted_at else None,
            "renewed_at": consent.renewed_at.isoformat() if consent.renewed_at else None,
            "expires_at": consent.expires_at.isoformat() if consent.expires_at else None,
            "issued_at": issued_at.isoformat(),
        },
    }


def issue_receipt(db: Session, consent: Consent, evidence: ConsentEvidence, *, action: str) -> ConsentReceipt:
    """Create and persist a ConsentReceipt for a GRANTED/RENEWED consent
    event. Does not commit - the caller (grant_consent/renew_consent)
    commits once, alongside the history/evidence rows for the same event."""
    # Read once off the already-resolved, already-tenant-scoped Consent the
    # caller passed in (grant_consent/renew_consent operate on a Consent row
    # obtained via a properly scoped lookup upstream) - not a second,
    # independent identity resolution.
    customer = consent.customer
    notice_version = db.get(NoticeVersion, evidence.notice_version_id) if evidence.notice_version_id else None
    issued_at = datetime.now(timezone.utc)
    receipt_ref = f"RCPT-{uuid.uuid4().hex[:20].upper()}"
    payload = _build_payload(consent, evidence, action, receipt_ref, notice_version, issued_at)
    payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    signature = hmac_signature(payload_hash)

    receipt = ConsentReceipt(
        tenant_id=consent.tenant_id,
        receipt_ref=receipt_ref,
        consent_id=consent.id,
        evidence_id=evidence.id,
        customer_id=consent.customer_id,
        source_app=consent.source_app,
        action=action,
        consent_version=consent.consent_version,
        notice_version_id=evidence.notice_version_id,
        payload=payload,
        payload_hash=payload_hash,
        signature=signature,
        issued_at=issued_at,
    )
    db.add(receipt)
    db.flush()

    log_audit(
        db, "RECEIPT_ISSUED", actor_username=evidence.collected_by, source_app=consent.source_app,
        tenant_id=consent.tenant_id, customer_id=consent.customer_id,
        customer_external_id=customer.external_id, consent_id=consent.id,
        purpose_id=consent.purpose_id, purpose_code=consent.purpose.code,
        reason=f"Consent receipt {receipt_ref} issued ({action})",
        metadata={"receipt_ref": receipt_ref, "payload_hash": payload_hash},
        commit=False,
    )
    return receipt


def verify_receipt(receipt: ConsentReceipt) -> bool:
    """Recompute payload_hash/signature from the stored payload and compare -
    True only if neither has been tampered with since issuance.

    The signature check goes through `hmac_signature_matches`, which tries
    every key in the HMAC key ring rather than only the current primary. A
    receipt is signed once and verified years later, so a direct
    `hmac_signature(...) == receipt.signature` would report every
    pre-rotation receipt as TAMPERED the moment an HMAC key is rotated - a
    false integrity alarm on precisely the artefact a DPDP audit relies on.
    """
    recomputed_hash = hashlib.sha256(
        json.dumps(receipt.payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    if recomputed_hash != receipt.payload_hash:
        return False
    return hmac_signature_matches(receipt.signature, recomputed_hash)
