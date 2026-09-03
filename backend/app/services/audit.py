from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core.utils import get_request_id
from app.models.entities import AuditLog
from app.services.audit_chain import chain_evidence, get_last_entry_hash


def log_audit(
    db: Session,
    event: str,
    *,
    actor_username: str = "system",
    actor_role: str = "",
    source_app: str = "",
    customer_id: Optional[int] = None,
    customer_external_id: Optional[str] = None,
    consent_id: Optional[int] = None,
    purpose_id: Optional[int] = None,
    purpose_code: Optional[str] = None,
    policy_id: Optional[int] = None,
    policy_code: Optional[str] = None,
    old_status: Optional[str] = None,
    new_status: Optional[str] = None,
    consent_version: Optional[int] = None,
    policy_version: Optional[int] = None,
    decision: Optional[str] = None,
    reason: str = "",
    request_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    commit: bool = True,
    actor_type: str = "USER",
    actor_id: Optional[str] = None,
    ip_address: str = "",
    user_agent: str = "",
    tenant_id: Optional[int] = None,
) -> AuditLog:
    prev_hash = get_last_entry_hash(db, tenant_id)
    entry = AuditLog(
        event=event,
        actor_username=actor_username,
        actor_role=actor_role,
        source_app=source_app,
        customer_id=customer_id,
        customer_external_id=customer_external_id,
        consent_id=consent_id,
        purpose_id=purpose_id,
        purpose_code=purpose_code,
        policy_id=policy_id,
        policy_code=policy_code,
        old_status=old_status,
        new_status=new_status,
        consent_version=consent_version,
        policy_version=policy_version,
        decision=decision,
        reason=reason,
        request_id=request_id or get_request_id(),
        details=metadata or {},
        actor_type=actor_type,
        actor_id=actor_id or actor_username,
        ip_address=ip_address,
        user_agent=user_agent,
        tenant_id=tenant_id,
    )
    chain_evidence(entry, prev_hash)
    db.add(entry)
    if commit:
        db.commit()
        db.refresh(entry)
    return entry
