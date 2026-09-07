import logging
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
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
    PROCESSOR_TYPE_LLM_PROVIDER,
    Processor,
    ProcessingActivity,
    Purpose,
    PurposeVersion,
    Transfer,
    User,
)
from app.services.audit import log_audit

router = APIRouter(prefix="/chatbot", tags=["chatbot"])
settings = get_settings()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Outbound PII redaction. The system prompt only ever contains aggregates
# (see _gather_platform_context below), but the free-text channel - the
# user's own message and prior conversation turns - is typed by a staff user
# and can contain anything, including a customer's email/phone/Aadhaar/
# external id pasted in while asking a question. That text is what actually
# reaches the third-party LLM provider, so it must be scrubbed *before* it is
# handed to _call_groq - the system prompt's "don't reveal PII" instructions
# only constrain what the model says back, by which point the data has
# already left the platform.
#
# Every quantifier below is bounded (an explicit upper limit on how many
# characters it can consume) rather than open-ended. An unbounded quantifier
# immediately followed by a required literal that is usually absent forces a
# backtracking engine to retry, one character shorter each time, from every
# starting position - O(run length) work per position, O(N^2) overall for an
# N-character message with nothing to match (e.g. a long paste of ordinary
# prose with no "@" in it). That is exactly what turned a ~5.6s reply into
# ~90s at roughly 4x the input length in review. Bounding each quantifier to
# a small constant (a local part and a domain label both have known real-
# world maximum lengths; a phone/Aadhaar-shaped run of digits-and-separators
# only needs to be looked at for so many characters before it can't possibly
# still be one) caps the backtracking cost per starting position, making the
# whole scan linear in input length regardless of content. See also
# ChatRequest.message/ChatMessage.content's max_length below, which is a
# second, independent line of defence at the API boundary.
# ---------------------------------------------------------------------------
# The host half accepts either a normal hostname+TLD, a bare IPv4 literal
# ("user@192.168.1.1"), or a bracketed IPv4 literal ("user@[203.0.113.5]") -
# all three are valid, real email address forms (RFC 5321) and none of the
# hostname-only pattern below matches a numeric-only final label. Every
# alternative keeps its own bounded quantifiers, so trying up to three
# alternatives per "@" found is still O(1) extra work per position, not a
# reintroduction of the unbounded-backtracking problem this whole pattern
# set is designed to avoid.
_IPV4_PART = r"\d{1,3}(?:\.\d{1,3}){3}"
_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+-]{1,64}@(?:"
    rf"\[{_IPV4_PART}\]"
    rf"|{_IPV4_PART}"
    r"|[A-Za-z0-9.-]{1,255}\.[A-Za-z]{2,24}"
    r")"
)
# External customer ids minted by app.services.context.derive_customer_id are
# "CUST-" + a hex digest, but the brief's own shape is "CUST-\d+" (also what
# seed.py's demo customers use); the dash is made optional so "CUST123" (no
# separator) is caught too, and match is bounded to a sane id length.
#
# Matching is case-insensitive, and a bounded lookahead requires at least
# one digit SOMEWHERE in the tail (not necessarily right after the dash) -
# this is what lets "cust-123", "CUST123" and "CUST-A1B2C3" (the hex-digest
# shape app.services.context.derive_customer_id actually produces, whose
# first character can be a letter) all match, while "customer" and
# "custody" still do not, because they contain no digit anywhere. An
# earlier version of this fix required the digit immediately after the
# dash, which excluded the false-positive words but also missed a real
# hex id starting with a letter; requiring a digit anywhere in the tail
# fixes both. The lookahead's own quantifier is bounded ({0,63} then a
# single required digit), so it adds no more than a small constant amount
# of extra work per "CUST" occurrence found - no reintroduction of
# unbounded backtracking.
_EXTERNAL_ID_RE = re.compile(r"\bCUST-?(?=[A-Za-z0-9]{0,63}\d)[A-Za-z0-9]{1,64}\b", re.IGNORECASE)

# Phone numbers and Aadhaar-like numbers are matched in two steps rather than
# one exact-shape regex: a cheap, boundedly-quantified "candidate" pattern
# finds a run that is plausibly digits-with-separators (space, dot, dash,
# comma, parentheses), and a small Python validator then strips the
# separators and checks the actual digit count/shape. This handles a
# separator appearing *anywhere* in the number, in any of these characters,
# and more than one of them - "+91-98765-43210", "98765 43210",
# "98765.43210", "(98765) 43210", "98765, 43210" are all one 10-digit number
# with a separator in a different place - without needing a combinatorial
# explosion of grouping alternatives hard-coded into the regex itself. The
# window is 7-24 (was 7-17/9-17) to comfortably fit real formatting variance
# (extra spaces, a separator plus surrounding parentheses) while remaining a
# small bounded constant - still O(1) extra work per starting position, not
# a step back towards the unbounded quantifiers this design avoids.
_PHONE_CANDIDATE_RE = re.compile(r"(?<!\d)\+?\(?\d[\d\s(),.-]{7,24}\d(?!\d)")
_AADHAAR_CANDIDATE_RE = re.compile(r"(?<!\d)\d[\d\s,.-]{9,24}\d(?!\d)")


def _digits_only(s: str) -> str:
    """Extract the digits in `s`, converting each to its ASCII value first.
    `unicodedata.digit()` gives the integer value of any Unicode decimal
    digit character - Bengali, Devanagari, Perso-Arabic, full-width, etc. -
    not just the ASCII '0'-'9' range, so a phone or Aadhaar number typed in
    any digit script is checked on its actual numeric shape. (The candidate
    regexes above already match these via `\\d`, which is Unicode-aware by
    default; this is what makes the *value* comparisons in
    _looks_like_phone/_looks_like_aadhaar work correctly on them too,
    instead of comparing a non-ASCII digit character against a literal
    ASCII string and always getting False.)
    """
    out = []
    for ch in s:
        if unicodedata.category(ch) == "Nd":
            out.append(str(unicodedata.digit(ch)))
    return "".join(out)


def _looks_like_phone(candidate: str) -> bool:
    digits = _digits_only(candidate)
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    return len(digits) == 10 and digits[0] in "6789"


def _looks_like_aadhaar(candidate: str) -> bool:
    return len(_digits_only(candidate)) == 12


# Patterns whose regex match is itself the thing to redact.
_SIMPLE_REDACTIONS = (
    (_EMAIL_RE, "[redacted-email]"),
    (_EXTERNAL_ID_RE, "[redacted-id]"),
)
# Patterns that need a second, Python-level check on the candidate match
# before it counts as a real hit.
_VALIDATED_REDACTIONS = (
    (_PHONE_CANDIDATE_RE, _looks_like_phone, "[redacted-phone]"),
    (_AADHAAR_CANDIDATE_RE, _looks_like_aadhaar, "[redacted-aadhaar]"),
)


def _normalize_for_matching(text: str) -> tuple[str, list[int]]:
    """Build a version of `text` for pattern matching only, and return it
    alongside an index map back to the original. Two evasions this closes:
    a zero-width character inserted into the middle of an identifier (e.g.
    "jane<ZWSP>@example.com"), and a look-alike character that only
    resembles ASCII (e.g. full-width "@" or digits) - stripped and NFKC-
    normalised respectively, character by character so each output
    character can be traced back to the exact original index it came from.

    Matching against this cleaned text and then mapping the match span back
    through `index_map` lets `_redact_pii` replace exactly the corresponding
    span of the ORIGINAL text. Everything outside a matched span - including
    the user's own unrelated use of unusual Unicode, or the case where
    nothing at all is redacted - is copied through byte-for-byte, so what
    reaches the provider is the redacted original, never a silently
    reformatted version of it.
    """
    out_chars: list[str] = []
    index_map: list[int] = []
    for i, ch in enumerate(text):
        if unicodedata.category(ch) == "Cf":  # zero-width/format characters
            continue
        for c in unicodedata.normalize("NFKC", ch):
            out_chars.append(c)
            index_map.append(i)
    return "".join(out_chars), index_map


def _redact_pii(text: str) -> tuple[str, int]:
    """Replace recognisable PII shapes with a stable placeholder. Returns the
    cleaned text and how many replacements were made - never the original
    matched values, so the count itself is safe to log or return to the
    caller.

    This is shape-matching, not understanding, and is a backstop rather than
    a guarantee. It will NOT catch: an identifier split across two separate
    chat turns (each half looks like harmless text on its own); an
    obfuscated form such as "jane [at] example dot com"; or any PII shape
    not on this list - postal addresses, PAN, passport numbers, bank
    account/IFSC details, or a customer's name typed in prose. Staff should
    still avoid pasting customer identifiers into the chatbot.
    """
    if not text:
        return text, 0

    normalized, index_map = _normalize_for_matching(text)
    if not normalized:
        return text, 0

    spans: list[tuple[int, int, str]] = []
    for pattern, placeholder in _SIMPLE_REDACTIONS:
        for m in pattern.finditer(normalized):
            spans.append((m.start(), m.end(), placeholder))
    for pattern, validator, placeholder in _VALIDATED_REDACTIONS:
        for m in pattern.finditer(normalized):
            if validator(m.group()):
                spans.append((m.start(), m.end(), placeholder))

    if not spans:
        return text, 0

    # Earliest start wins; among equal starts prefer the longer match (e.g.
    # a full external id beats a run of digits nested inside it).
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))

    def _orig_index(norm_idx: int) -> int:
        return index_map[norm_idx] if norm_idx < len(index_map) else len(text)

    result: list[str] = []
    cursor = 0
    count = 0
    consumed_until = -1
    for n_start, n_end, placeholder in spans:
        if n_start < consumed_until:
            continue  # overlaps a match already applied - skip
        orig_start = _orig_index(n_start)
        if orig_start < cursor:
            continue
        orig_end = _orig_index(n_end)
        result.append(text[cursor:orig_start])
        result.append(placeholder)
        count += 1
        cursor = orig_end
        consumed_until = n_end
    result.append(text[cursor:])
    return "".join(result), count


# Free-text sent to a third-party LLM provider is capped so an unbounded
# paste is rejected cleanly (a 422 from FastAPI/pydantic validation) rather
# than absorbed - both because it is otherwise the largest practical input
# to _redact_pii (whose patterns are now bounded and safe regardless, but a
# cap is a second, independent line of defence against the same class of
# problem) and because nothing else in the request path limits how much
# text ends up in the provider's context window. 4,000 characters is
# generous for a genuine multi-paragraph question. History is separately
# capped in turn count, since many turns each just under the per-turn limit
# could otherwise add up to an unbounded overall payload.
_MAX_MESSAGE_LENGTH = 4000
_MAX_HISTORY_TURNS = 50

# Standing, always-present disclosure (not just when something was actually
# redacted): this is a length-capped, best-effort-redacted free-text channel
# to a third-party processor, not a guarantee that no personal data can ever
# reach it - see _redact_pii's docstring for exactly what it does and does
# not catch. Shown on every response so staff cannot come to rely on the
# absence of a per-message redaction notice as proof nothing sensitive could
# have gone through.
_CHANNEL_NOTICE = (
    "This assistant sends your message to a third-party AI provider. Common identifiers "
    "(email addresses, phone numbers, Aadhaar-like numbers, customer ids) are automatically "
    "redacted on a best-effort basis; other personal details - names, addresses, PAN/passport "
    "numbers, or an identifier split across separate messages - are not filtered. Do not enter "
    "customer personal data here."
)


class ChatMessage(BaseModel):
    role: str
    content: str = Field(max_length=_MAX_MESSAGE_LENGTH)


class ChatRequest(BaseModel):
    message: str = Field(max_length=_MAX_MESSAGE_LENGTH)
    history: list[ChatMessage] = Field(default_factory=list, max_length=_MAX_HISTORY_TURNS)


class ChatResponse(BaseModel):
    reply: str
    redacted_count: int = 0
    channel_notice: str = _CHANNEL_NOTICE


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
            "consent_text": latest_version.consent_text if latest_version else "",
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
            "rules": pv.rules if pv else [],
        })

    categories = db.query(DataCategory).filter(DataCategory.is_active.is_(True)).all()
    category_list = [{"code": c.code, "name": c.name, "description": c.description} for c in categories]

    activities = db.query(ProcessingActivity).filter(ProcessingActivity.is_active.is_(True)).all()
    activity_list = [{"code": a.code, "name": a.name, "description": a.description} for a in activities]

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

    recent_events = (
        db.query(AuditLog)
        .order_by(AuditLog.created_at.desc())
        .limit(15)
        .all()
    )
    recent_list = [
        {
            "event": e.event,
            "actor_type": e.actor_type or "SYSTEM",
            "purpose": e.purpose_code or "",
            "old_status": e.old_status or "",
            "new_status": e.new_status or "",
            "created_at": e.created_at.isoformat() if e.created_at else "",
        }
        for e in recent_events
    ]

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
        ctx += f"- [{cl['code']}] {cl['name']}: {cl['description']}\n"

    ctx += f"\nPROCESSING ACTIVITIES ({len(activities)} active)\n"
    for al in activity_list:
        ctx += f"- [{al['code']}] {al['name']}: {al['description']}\n"

    ctx += f"\nCONSENT POLICIES ({len(policies)} active)\n"
    for pl in policy_list:
        ctx += f"- [{pl['code']}] {pl['name']}: {pl['description']} | Default decision: {pl['default_decision']}\n"
        if pl["rules"]:
            for rule in pl["rules"]:
                ctx += f"    Rule: {rule.get('purpose_code','*')} x {rule.get('data_category_code','*')} x {rule.get('processing_activity_code','*')} -> {rule.get('decision','')} (priority: {rule.get('priority','')})\n"

    ctx += f"\nCONSENT DISTRIBUTION BY PURPOSE\n"
    for code, info in purpose_dist.items():
        ctx += f"- [{code}] {info['name']}: {info['active']} active / {info['total']} total\n"

    ctx += f"\nPLATFORM USERS: {total_users}\n"

    ctx += f"\nRECENT AUDIT ACTIVITY (last {len(recent_list)} events)\n"
    for ev in recent_list:
        ctx += f"- {ev['event']} by {ev['actor_type']} | purpose: {ev['purpose']} | {ev['old_status']}->{ev['new_status']} | {ev['created_at']}\n"

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


def _llm_processor_block_reason(db: Session) -> str | None:
    """Returns None when the chatbot is permitted to call the LLM provider,
    or a short internal reason string (for the audit log only - never shown
    to the user) explaining why it is blocked."""
    proc = (
        db.query(Processor)
        .filter(Processor.type == PROCESSOR_TYPE_LLM_PROVIDER, Processor.is_active.is_(True))
        .first()
    )
    if not proc:
        return "No active processor is configured for the LLM provider"
    if proc.contract_valid_until and proc.contract_valid_until < date.today():
        return "The active processor's contract has expired"

    # The transfer register's `restricted` flag is a stored value that can
    # drift from the live RESTRICTED_COUNTRIES config (e.g. a country is
    # added to the restricted list after the transfer row was created), so
    # recompute it here against the current config on every check - the
    # transfer register is kept in sync, and a restricted destination is
    # actually enforced rather than only ever reflecting whatever a human
    # wrote when the row was inserted.
    transfers = db.query(Transfer).filter(Transfer.processor_id == proc.id).all()
    dirty = False
    restricted_now = False
    for t in transfers:
        computed = settings.is_restricted_destination(t.destination_country)
        if t.restricted != computed:
            t.restricted = computed
            dirty = True
        if computed:
            restricted_now = True
    if dirty:
        db.commit()
    if restricted_now:
        return "The active processor's transfer destination is a restricted country"
    return None


_BLOCKED_REPLY = (
    "The AI assistant is temporarily unavailable: no active data-processing agreement is on "
    "file for the language-model provider. Contact your administrator."
)


@router.post("", response_model=ChatResponse)
def chat(
    body: ChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(PERM_DASHBOARD)),
):
    block_reason = _llm_processor_block_reason(db)
    if block_reason:
        log_audit(
            db,
            "CHATBOT_BLOCKED",
            actor_username=current_user.username,
            actor_id=str(current_user.id),
            actor_type="USER",
            reason=block_reason,
        )
        return ChatResponse(reply=_BLOCKED_REPLY)

    redacted_message, message_redactions = _redact_pii(body.message)
    redacted_history: list[ChatMessage] = []
    history_redactions = 0
    for msg in body.history:
        clean_content, n = _redact_pii(msg.content)
        history_redactions += n
        redacted_history.append(ChatMessage(role=msg.role, content=clean_content))
    total_redactions = message_redactions + history_redactions

    if total_redactions:
        # Never log the matched values themselves - only that redaction
        # happened, and how many times.
        logger.info(
            "chatbot: redacted %d PII-shaped value(s) from outbound message/history before calling the LLM provider",
            total_redactions,
        )

    platform_data = _gather_platform_context(db)
    system_prompt = SYSTEM_PROMPT.format(platform_data=platform_data)

    try:
        reply = _call_groq(system_prompt, redacted_message, redacted_history)
    except Exception as e:
        reply = (
            "I'm sorry, I encountered an error processing your request. "
            "Please ensure the Groq API key is configured correctly. "
            f"Error: {type(e).__name__}"
        )

    if total_redactions:
        plural = "" if total_redactions == 1 else "s"
        reply = (
            f"[Note: {total_redactions} personal identifier{plural} in your message were redacted before "
            "being sent to the AI assistant, so the behaviour is not silently surprising.]\n\n" + reply
        )

    return ChatResponse(reply=reply, redacted_count=total_redactions)
