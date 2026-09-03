from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.config import get_settings
from app.core.database import get_db
from app.core.rbac import PERM_DASHBOARD
from app.models.entities import (
    AuditLog,
    Consent,
    ConsentHistory,
    CrmCustomer,
    Customer,
    DataCategory,
    Policy,
    PolicyVersion,
    ProcessingActivity,
    Purpose,
    PurposeVersion,
    User,
)

router = APIRouter(prefix="/chatbot", tags=["chatbot"])
settings = get_settings()


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = []


class ChatResponse(BaseModel):
    reply: str


def _gather_platform_context(db: Session) -> str:
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=30)
    active_statuses = ("GRANTED", "ACTIVE", "RENEWED", "UPDATED")

    total_customers = db.query(func.count(Customer.id)).scalar() or 0
    total_crm_customers = db.query(func.count(CrmCustomer.id)).scalar() or 0
    total_consents = db.query(func.count(Consent.id)).scalar() or 0
    active_consents = (
        db.query(func.count(Consent.id))
        .filter(Consent.status.in_(active_statuses))
        .scalar() or 0
    )
    pending_consents = db.query(func.count(Consent.id)).filter(Consent.status == "PENDING").scalar() or 0
    withdrawn_consents = db.query(func.count(Consent.id)).filter(Consent.status == "WITHDRAWN").scalar() or 0
    denied_consents = db.query(func.count(Consent.id)).filter(Consent.status == "DENIED").scalar() or 0
    expired_consents = db.query(func.count(Consent.id)).filter(Consent.status == "EXPIRED").scalar() or 0
    granted_consents = db.query(func.count(Consent.id)).filter(Consent.status == "GRANTED").scalar() or 0
    expiring_soon = (
        db.query(func.count(Consent.id))
        .filter(
            Consent.status.in_(active_statuses),
            Consent.expires_at.isnot(None),
            Consent.expires_at > now,
            Consent.expires_at <= horizon,
        )
        .scalar() or 0
    )

    purposes = db.query(Purpose).filter(Purpose.is_active.is_(True)).all()
    purpose_list = []
    for p in purposes:
        latest_version = (
            db.query(PurposeVersion)
            .filter(PurposeVersion.purpose_id == p.id, PurposeVersion.is_current.is_(True))
            .first()
        )
        purpose_list.append({
            "code": p.code,
            "name": p.name,
            "description": p.description,
            "legal_basis": p.legal_basis,
            "requires_consent": p.requires_consent,
            "retention_days": p.retention_period_days,
            "data_categories": latest_version.data_category_ids if latest_version else [],
            "processing_activities": latest_version.processing_activity_ids if latest_version else [],
        })

    policies = db.query(Policy).filter(Policy.is_active.is_(True)).all()
    policy_list = []
    for pol in policies:
        pv = (
            db.query(PolicyVersion)
            .filter(PolicyVersion.policy_id == pol.id, PolicyVersion.is_current.is_(True))
            .first()
        )
        policy_list.append({
            "code": pol.code,
            "name": pol.name,
            "description": pol.description,
            "default_decision": pv.default_decision if pv else "REQUIRE_CONSENT",
            "rules_count": len(pv.rules) if pv else 0,
        })

    categories = db.query(DataCategory).filter(DataCategory.is_active.is_(True)).all()
    category_list = [{"code": c.code, "name": c.name} for c in categories]

    activities = db.query(ProcessingActivity).filter(ProcessingActivity.is_active.is_(True)).all()
    activity_list = [{"code": a.code, "name": a.name} for a in activities]

    status_rows = (
        db.query(Consent.status, func.count(Consent.id))
        .group_by(Consent.status)
        .all()
    )
    status_dist = {s: c for s, c in status_rows}

    purpose_dist_rows = (
        db.query(Purpose.code, Purpose.name, Consent.status, func.count(Consent.id))
        .join(Consent, Consent.purpose_id == Purpose.id)
        .group_by(Purpose.code, Purpose.name, Consent.status)
        .all()
    )
    purpose_dist: dict[str, dict] = {}
    for code, name, status, count in purpose_dist_rows:
        bucket = purpose_dist.setdefault(code, {"code": code, "name": name, "active": 0, "total": 0})
        bucket["total"] += count
        if status in active_statuses:
            bucket["active"] += count

    # R3-09: Strip PII from audit events - use opaque event types only
    recent_event_types = (
        db.query(AuditLog.event, func.count(AuditLog.id))
        .order_by(AuditLog.created_at.desc())
        .group_by(AuditLog.event)
        .limit(20)
        .all()
    )
    event_summary = {e: c for e, c in recent_event_types}

    total_users = db.query(func.count(User.id)).scalar() or 0

    ctx = f"""
=== CONSENT360 PLATFORM DATA (live snapshot) ===

CUSTOMERS
- Total consent platform customers: {total_customers}
- Total CRM directory customers: {total_crm_customers}

CONSENTS
- Total consents: {total_consents}
- Active (GRANTED/ACTIVE/RENEWED/UPDATED): {active_consents}
- Pending: {pending_consents}
- Granted (awaiting activation): {granted_consents}
- Withdrawn: {withdrawn_consents}
- Denied: {denied_consents}
- Expired: {expired_consents}
- Expiring within 30 days: {expiring_soon}
- Status distribution: {status_dist}

PURPOSES ({len(purposes)} active)
"""
    for pl in purpose_list:
        ctx += f"- [{pl['code']}] {pl['name']}: {pl['description']} | Legal basis: {pl['legal_basis']} | Requires consent: {pl['requires_consent']} | Retention: {pl['retention_days']} days\n"

    ctx += f"\nDATA CATEGORIES ({len(categories)} active)\n"
    for cl in category_list:
        ctx += f"- [{cl['code']}] {cl['name']}\n"

    ctx += f"\nPROCESSING ACTIVITIES ({len(activities)} active)\n"
    for al in activity_list:
        ctx += f"- [{al['code']}] {al['name']}\n"

    ctx += f"\nCONSENT POLICIES ({len(policies)} active)\n"
    for pl in policy_list:
        ctx += f"- [{pl['code']}] {pl['name']}: {pl['description']} | Default decision: {pl['default_decision']} | Rules: {pl['rules_count']}\n"

    ctx += f"\nCONSENT DISTRIBUTION BY PURPOSE\n"
    for code, info in purpose_dist.items():
        ctx += f"- [{code}] {info['name']}: {info['active']} active / {info['total']} total\n"

    ctx += f"\nPLATFORM USERS: {total_users}\n"

    # R3-09: Aggregate event counts only - no actor names or customer names
    ctx += f"\nRECENT ACTIVITY (aggregated event counts)\n"
    for event_type, count in event_summary.items():
        ctx += f"- {event_type}: {count} occurrences\n"

    return ctx


SYSTEM_PROMPT = """You are Consent360 AI Assistant, an expert chatbot for the Consent360 consent management platform.

Your role:
1. Answer ALL questions about the Consent360 platform using the live platform data provided below.
2. Answer both QUANTITATIVE questions (counts, percentages, statistics, metrics) and DESCRIPTIVE questions (what things mean, how processes work, explanations).
3. Always use the live data snapshot to give accurate, specific answers with exact numbers.
4. Explain consent management concepts, the DPDP Act (Digital Personal Data Protection Act, 2023), cookie consent, and privacy regulations clearly.
5. Be helpful, professional, and concise.

DPDP ACT COMPLIANCE - CRITICAL RULES:
- NEVER reveal, expose, or discuss any customer's personal information (name, email, phone, address, Aadhaar number, age, or any PII).
- If asked about a specific customer's personal details, respond: "I cannot share personal customer details. This is protected under the DPDP Act 2023 and Consent360's privacy policy."
- If asked to identify or look up a customer by name/email/phone, REFUSE politely and explain why.
- You may discuss AGGREGATE/ANONYMOUS statistics (e.g., "there are X customers total") but never individual customer data.
- You may discuss consent statuses, purposes, policies, and platform configuration freely.
- If a question could lead to PII exposure, err on the side of caution and decline.

PLATFORM CONTEXT (live data):
{platform_data}

IMPORTANT GUIDELINES:
- For quantitative questions, provide exact numbers from the data above.
- For "how many" questions, count from the data.
- For "what is" questions, explain concepts using the data.
- For "which purpose" or "which policy" questions, reference the specific codes and names.
- For DPDP Act questions, explain the act's provisions as they relate to consent management.
- For questions about consent lifecycle, explain the state machine: NOT_REQUESTED -> REQUESTED -> PENDING -> GRANTED -> ACTIVE, with branches to DENIED, WITHDRAWN, EXPIRED.
- If you don't have enough data to answer, say so honestly.
- Format responses with clear structure. Use bullet points and short paragraphs.
"""


def _call_groq(system_prompt: str, user_message: str, history: list[ChatMessage]) -> str:
    from langchain_groq import ChatGroq

    llm = ChatGroq(
        model="openai/gpt-oss-120b",
        temperature=0,
        api_key=settings.GROQ_API_KEY,
    )

    messages = [("system", system_prompt)]
    for msg in history[-10:]:
        if msg.role == "user":
            messages.append(("user", msg.content))
        elif msg.role == "assistant":
            messages.append(("assistant", msg.content))
    messages.append(("user", user_message))

    response = llm.invoke(messages)
    return response.content


@router.post("", response_model=ChatResponse)
def chat(
    body: ChatRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission(PERM_DASHBOARD)),
):
    platform_data = _gather_platform_context(db)
    system_prompt = SYSTEM_PROMPT.format(platform_data=platform_data)

    try:
        reply = _call_groq(system_prompt, body.message, body.history)
    except Exception as e:
        reply = (
            "I'm sorry, I encountered an error processing your request. "
            "Please ensure the Groq API key is configured correctly. "
            f"Error: {type(e).__name__}"
        )

    return ChatResponse(reply=reply)
