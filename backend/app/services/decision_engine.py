from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.entities import (
    Consent,
    ConsentDecisionLog,
    Customer,
    DataCategory,
    Policy,
    ProcessingActivity,
    Purpose,
    PurposeVersion,
)
from app.services.audit import log_audit


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _active_consent_statuses() -> tuple[str, ...]:
    return ("GRANTED", "ACTIVE", "RENEWED", "UPDATED")


class Decision:
    def __init__(
        self,
        decision: str,
        reason: str,
        allowed: bool,
        *,
        consent: Optional[Consent] = None,
        policy: Optional[Policy] = None,
        policy_version_number: Optional[int] = None,
        policy_version_id: Optional[int] = None,
    ):
        self.decision = decision
        self.reason = reason
        self.allowed = allowed
        self.consent = consent
        self.policy = policy
        self.policy_version_number = policy_version_number
        self.policy_version_id = policy_version_id


def get_active_policy(db: Session) -> Optional[Policy]:
    policies = (
        db.query(Policy)
        .filter(Policy.is_active.is_(True), Policy.status == "ACTIVE")
        .order_by(Policy.id.asc())
        .all()
    )
    for policy in policies:
        for version in sorted(policy.versions, key=lambda v: v.version_number, reverse=True):
            if version.is_current:
                return policy
    return None


def find_applicable_rule(policy: Policy, purpose_code: str, dc_code: str, pa_code: str):
    version = None
    for v in sorted(policy.versions, key=lambda x: x.version_number, reverse=True):
        if v.is_current:
            version = v
            break
    if not version:
        return None, None, version
    for rule in version.rules or []:
        if (
            rule.get("purpose_code") == purpose_code
            and rule.get("data_category_code") == dc_code
            and rule.get("processing_activity_code") == pa_code
        ):
            return rule, version, version
    return None, version, version


def _s9_parental_consent_gap(db: Session, customer: Customer, purpose: Purpose) -> Optional[dict]:
    """R1-14: is this principal one whose consent s.9(1) does not permit us to
    rely on? Returns the gap, or None.

    Returns None - leaving every existing decision untouched - unless there is
    an age-assurance record saying this principal is a child or a person with
    disability with a lawful guardian. A missing record means "never
    assessed", not "child": see app/models/guardian.py.

    A Fourth Schedule (R.12) exemption that relaxes verifiable parental
    consent for this exact purpose closes the gap, which is why the exemption
    lookup is here rather than in the caller.
    """
    from app.services import guardian as guardian_service

    assurance = guardian_service.get_age_assurance(db, customer.id)
    if assurance is None or not assurance.requires_guardian_consent:
        return None
    if guardian_service.verified_guardian_consent(db, customer.id) is not None:
        return None
    if guardian_service.find_exemption(
        db,
        tenant_id=customer.tenant_id,
        purpose_code=purpose.code,
        obligation=guardian_service.OBLIGATION_PARENTAL_CONSENT,
    ) is not None:
        return None
    return {
        "age_assurance_id": assurance.id,
        "reason": (
            f"This data principal is recorded as "
            f"{'a child' if assurance.is_child else 'a person with disability with a lawful guardian'}, "
            "and no verified parental or lawful-guardian consent record exists for them. "
            f"DPDP Act s.9(1) with Rules 2025 R.10 requires that consent before "
            f"{purpose.name} may be relied on, so the existing consent record cannot be "
            "acted upon and verifiable consent must be obtained."
        ),
    }


def _binding_purpose_version(
    db: Session, purpose: Purpose, consent: Optional[Consent]
) -> tuple[Optional[PurposeVersion], str]:
    """Which PurposeVersion's itemisation binds this request?

    THE CALL, made deliberately: **the version the consent pinned**, and only
    the current version when there is no consent to have pinned one.

    `Consent.purpose_version_id` is the version that was in force when the
    principal gave consent - i.e. the itemised list of personal data and
    processing that the R.3(b)(i) notice actually put in front of her. s.6(1)
    consent is agreement "to the processing of her personal data for the
    specified purpose"; the purpose as specified TO HER is that version and no
    other. Binding the current version instead would mean the fiduciary could
    widen the scope of an existing consent by publishing a new version -
    consent by unilateral amendment, which is exactly what pinning
    purpose_version_id on the consent row exists to prevent (and what R1-09's
    `re_consent_required` machinery makes the fiduciary re-ask for). The
    current version is a scope the principal has not agreed to yet, so it can
    only ever be checked against a consent that does not exist.

    With no consent row there is nothing pinned, and the honest reference is
    the itemisation in force now - the one a fresh notice would show if the
    request went on to ask for consent.

    Returns (version, "consent_pinned" | "current"), or (None, ...) when the
    purpose carries no version at all. That last case is a data-integrity
    error the rest of the stack already refuses (`get_current_purpose_version`
    raises 500, so no consent can even be created for such a purpose); it is
    deliberately NOT turned into a DENY here, because inventing a decision
    outcome for a broken row would hide the breakage rather than report it.
    """
    if consent is not None:
        pinned = consent.purpose_version
        if pinned is None and consent.purpose_version_id is not None:
            pinned = db.get(PurposeVersion, consent.purpose_version_id)
        if pinned is not None:
            return pinned, "consent_pinned"
    for version in sorted(purpose.versions or [], key=lambda v: v.version_number, reverse=True):
        if version.is_current:
            return version, "current"
    return None, "none"


def _purpose_scope_violation(
    db: Session,
    purpose: Purpose,
    data_category: DataCategory,
    processing_activity: ProcessingActivity,
    consent: Optional[Consent],
) -> Optional[dict]:
    """R1-05/L-02 - DPDP Act s.6(1) with Rules 2025 R.3(b)(i).

    A PurposeVersion itemises the data categories and processing activities
    the purpose covers (`data_category_ids` / `processing_activity_ids`), and
    a notice has to put that itemisation in front of the principal. The engine
    never read either list, so a consent recorded against an itemised purpose
    authorised *any* category and *any* activity - the itemisation was
    decorative, and data minimisation was unenforced.

    An EMPTY list is "nothing is itemised", not "everything is allowed". A
    purpose that names no data and no processing has specified nothing, and
    s.6(1) consent is limited to the data necessary for the *specified*
    purpose; reading silence as a wildcard is the reading that makes s.6(1)
    unenforceable. This is what the reproduction found: a purpose with both
    lists empty returned ALLOW for an unrelated category and activity.

    Returns None when the request is inside the binding version's scope, or a
    dict describing the violation.
    """
    version, binding = _binding_purpose_version(db, purpose, consent)
    if version is None:
        return None

    category_ids = {int(i) for i in (version.data_category_ids or [])}
    activity_ids = {int(i) for i in (version.processing_activity_ids or [])}
    out_of_scope: list[str] = []
    if data_category.id not in category_ids:
        out_of_scope.append(
            f"the data category {data_category.name} ({data_category.code}) is not among the "
            f"{len(category_ids)} itemised for this purpose"
        )
    if processing_activity.id not in activity_ids:
        out_of_scope.append(
            f"the processing activity {processing_activity.name} ({processing_activity.code}) is "
            f"not among the {len(activity_ids)} itemised for this purpose"
        )
    if not out_of_scope:
        return None

    scope_source = (
        "the purpose version this consent was given under"
        if binding == "consent_pinned"
        else "the purpose version currently in force"
    )
    return {
        "reason": (
            f"Out of scope for {purpose.name}: "
            + " and ".join(out_of_scope)
            + f" (version {version.version_number}, {scope_source}). "
            "DPDP Act s.6(1) limits consent to the personal data necessary for the specified "
            "purpose and Rules 2025 R.3(b)(i) requires the notice to itemise that data and the "
            "processing done on it, so processing outside the itemisation is not covered by this "
            "consent and must not proceed."
        ),
        "details": {
            "purpose_scope_violation": True,
            "binding_purpose_version_id": version.id,
            "binding_purpose_version_number": version.version_number,
            "binding_purpose_version_source": binding,
            "itemised_data_category_ids": sorted(category_ids),
            "itemised_processing_activity_ids": sorted(activity_ids),
            "out_of_scope_data_category_code": (
                data_category.code if data_category.id not in category_ids else None
            ),
            "out_of_scope_processing_activity_code": (
                processing_activity.code if processing_activity.id not in activity_ids else None
            ),
        },
    }


def evaluate_decision(
    db: Session,
    customer: Customer,
    purpose: Purpose,
    data_category: DataCategory,
    processing_activity: ProcessingActivity,
    *,
    requested_by: str = "system",
    source_app: str = "",
    persist: bool = True,
    request_id: Optional[str] = None,
) -> Decision:
    """Deterministic, rule-based consent decision evaluation."""

    now = utcnow()

    # ----------------------------------------------------------------- #
    # R1-14/F-04 - DPDP Act s.9(3). PLACEMENT, stated plainly because the
    # precedence order in docs/ARCHITECTURE.md is documented:
    #
    # This is a NEW step inserted at the HEAD, ahead of the explicit
    # policy-rule DENY. Nothing below it is reordered - the five documented
    # steps keep their existing order relative to each other, and the
    # re-consent branch R1-09 added inside step 2 is untouched.
    #
    # It goes first, rather than after the policy-rule DENY, for one reason:
    # every other step in this function decides what the *fiduciary's own
    # policy and the principal's own consent* permit. s.9(3) is neither. The
    # Act does not make tracking, behavioural monitoring or targeted
    # advertising directed at a child consentable, so no policy rule can
    # allow it, no valid granted consent can allow it, and a parent cannot
    # allow it either. When a policy rule happens to deny the same
    # processing, both outcomes are DENY and only the recorded reason
    # differs - and the reason that belongs in the ledger is the statutory
    # prohibition, not an incidental rule that could be edited away
    # tomorrow.
    #
    # It is strictly additive: it can only turn an ALLOW into a DENY, never
    # a DENY into an ALLOW, and it returns None for every principal without
    # an age-assurance record saying they are a child - so no existing
    # decision changes.
    #
    # The single thing that lifts it is a Fourth Schedule (R.12) exemption
    # the tenant has configured, authorised, and scoped to this exact
    # purpose; that check lives inside child_prohibition_for.
    # ----------------------------------------------------------------- #
    from app.services.guardian import child_prohibition_for

    prohibition = child_prohibition_for(
        db,
        customer_id=customer.id,
        tenant_id=customer.tenant_id,
        purpose_code=purpose.code,
        purpose_name=purpose.name,
        processing_activity_code=processing_activity.code,
        purpose_child_restricted=bool(getattr(purpose, "child_restricted", False)),
        now=now,
    )
    if prohibition is not None:
        # Guarded on `persist` exactly as _finish is: a dry-run evaluation
        # (routes/decision_validation.py) must not leave an uncommitted audit
        # row hanging in the caller's transaction.
        if persist:
            log_audit(
                db,
                "CHILD_PROHIBITED_DECISION_DENIED",
                actor_username=requested_by,
                actor_type="SYSTEM",
                source_app=source_app,
                tenant_id=customer.tenant_id,
                customer_id=customer.id,
                purpose_id=purpose.id,
                purpose_code=purpose.code,
                decision="DENY",
                reason=prohibition.reason,
                request_id=request_id,
                metadata=prohibition.as_details(),
                commit=False,
            )
        d = Decision("DENY", prohibition.reason, False)
        return _finish(
            d, db, customer, purpose, data_category, processing_activity, requested_by,
            source_app, persist, request_id, extra_details=prohibition.as_details(),
        )

    policy = get_active_policy(db)
    rule = None
    policy_version = None
    policy_version_id = None
    policy_code = None
    policy_version_number = None

    if policy:
        rule, policy_version, _ = find_applicable_rule(policy, purpose.code, data_category.code, processing_activity.code)
        if policy_version:
            policy_code = policy.code
            policy_version_number = policy_version.version_number
            policy_version_id = policy_version.id

    if policy and rule:
        rule_decision = rule.get("decision", "ALLOW")
        if rule_decision == "DENY":
            d = Decision(
                "DENY",
                f"Policy {policy.code} v{policy_version_number} explicitly denies processing "
                f"{processing_activity.name} on {data_category.name} for {purpose.name}.",
                False,
                policy=policy,
                policy_version_number=policy_version_number,
                policy_version_id=policy_version_id,
            )
            return _finish(d, db, customer, purpose, data_category, processing_activity, requested_by, source_app, persist, request_id)

    # Find the most relevant consent record.
    #
    # Hoisted above the purpose-scope check below (it used to sit directly
    # under the policy-rule block). It is a pure read with no side effects, so
    # moving it changes no behaviour on its own; the scope check needs it
    # because the version that binds is the one THIS consent pinned.
    consent = (
        db.query(Consent)
        .filter(
            Consent.customer_id == customer.id,
            Consent.purpose_id == purpose.id,
            Consent.data_category_id == data_category.id,
            Consent.processing_activity_id == processing_activity.id,
        )
        .order_by(Consent.consent_version.desc(), Consent.created_at.desc())
        .first()
    )

    # ----------------------------------------------------------------- #
    # R1-05/L-02 - DPDP Act s.6(1) with Rules 2025 R.3(b)(i). PLACEMENT,
    # stated plainly because the precedence order in docs/ARCHITECTURE.md is documented:
    #
    # A NEW step inserted AFTER the explicit policy-rule DENY and BEFORE the
    # consent-status step. Nothing is reordered: an explicit policy DENY still
    # wins and still reports its own reason, and every step below keeps its
    # existing order relative to the others.
    #
    # After the policy rule, because a fiduciary's own published policy saying
    # "never do this" is the more specific and more damning finding, and the
    # reason that belongs in the ledger when both apply.
    #
    # Before the consent status, because this asks a prior question: is the
    # request even within the purpose the principal was asked about? A
    # consent's status - active, withdrawn, expired - is an answer about data
    # and processing the notice itemised. Reporting "consent withdrawn" for a
    # category the consent never covered asserts that a consent covering it
    # once existed. It did not, and saying so is the more accurate answer to a
    # principal asking why.
    #
    # It is strictly additive in the same sense as the s.9(3) step above: it
    # can only turn an ALLOW (or a WITHDRAWN/EXPIRED/REQUIRE_CONSENT) into a
    # DENY, never a DENY into an ALLOW, and it returns None for every request
    # that is inside the itemisation - which is every request the demo data
    # and every existing caller makes.
    # ----------------------------------------------------------------- #
    scope_gap = _purpose_scope_violation(db, purpose, data_category, processing_activity, consent)
    if scope_gap is not None:
        d = Decision(
            "DENY",
            scope_gap["reason"],
            False,
            consent=consent,
            policy=policy,
            policy_version_number=policy_version_number,
            policy_version_id=policy_version_id,
        )
        return _finish(
            d, db, customer, purpose, data_category, processing_activity, requested_by,
            source_app, persist, request_id, extra_details=scope_gap["details"],
        )

    if consent:
        if consent.status == "WITHDRAWN":
            d = Decision(
                "WITHDRAWN",
                "Consent for this purpose has been withdrawn by the data principal.",
                False,
                consent=consent,
                policy=policy,
                policy_version_number=policy_version_number,
                policy_version_id=policy_version_id,
            )
            return _finish(d, db, customer, purpose, data_category, processing_activity, requested_by, source_app, persist, request_id)

        if consent.status == "DENIED":
            d = Decision(
                "DENY",
                "Consent for this purpose has been explicitly denied.",
                False,
                consent=consent,
                policy=policy,
                policy_version_number=policy_version_number,
                policy_version_id=policy_version_id,
            )
            return _finish(d, db, customer, purpose, data_category, processing_activity, requested_by, source_app, persist, request_id)

        expired = consent.expires_at is not None and consent.expires_at <= now
        if consent.status == "EXPIRED" or expired:
            d = Decision(
                "EXPIRED",
                "Consent for this purpose has expired and may no longer be relied upon.",
                False,
                consent=consent,
                policy=policy,
                policy_version_number=policy_version_number,
                policy_version_id=policy_version_id,
            )
            return _finish(d, db, customer, purpose, data_category, processing_activity, requested_by, source_app, persist, request_id)

        # R1-09/P-01: a material change was published to this purpose and the
        # principal has not consented again. The consent's *status* is still
        # active - nobody withdrew, denied or expired anything - but s.6(1)
        # consent is agreement "to the processing of her personal data for the
        # specified purpose", and the specified purpose is no longer the one
        # she agreed to. Continuing to process under it is processing without
        # consent, so this is a refusal, not a warning banner.
        #
        # Placement, stated plainly because the precedence order in docs/ARCHITECTURE.md
        # is documented and this refines it: this is a new branch INSIDE the
        # existing "consent status" step, after WITHDRAWN / DENIED / EXPIRED
        # and immediately before "active -> ALLOW". Nothing is reordered - the
        # policy-rule DENY still wins over everything, and a withdrawn,
        # denied or expired consent still reports its own outcome rather than
        # this one. It only narrows the single case that used to fall straight
        # through to ALLOW.
        #
        # The outcome is REQUIRE_CONSENT rather than a new decision value:
        # `DECISION_OUTCOMES` in models/entities.py is what KPI bucketing
        # (services/kpi.py) and the decisions API already enumerate, and
        # REQUIRE_CONSENT is exactly what this is - consent must be obtained
        # before processing. The distinguishing detail goes in the reason and
        # in the decision log's `details`, where a report can find it without
        # a schema change rippling through three other modules.
        if getattr(consent, "re_consent_required", False):
            d = Decision(
                "REQUIRE_CONSENT",
                (
                    f"A material change was published to {purpose.name} and fresh consent has "
                    f"not been given since"
                    + (
                        f" (requested {consent.re_consent_requested_at.isoformat()})"
                        if consent.re_consent_requested_at else ""
                    )
                    + ". The existing consent (status "
                    f"{consent.status}, version {consent.consent_version}) was given for an "
                    "earlier version of this purpose and does not cover the current one, so "
                    "processing is blocked until the data principal consents again "
                    "(DPDP Act s.6(1))."
                ),
                False,
                consent=consent,
                policy=policy,
                policy_version_number=policy_version_number,
                policy_version_id=policy_version_id,
            )
            return _finish(
                d, db, customer, purpose, data_category, processing_activity, requested_by,
                source_app, persist, request_id,
                extra_details={
                    "re_consent_required": True,
                    "re_consent_campaign_id": consent.re_consent_campaign_id,
                    "re_consent_requested_at": (
                        consent.re_consent_requested_at.isoformat()
                        if consent.re_consent_requested_at else None
                    ),
                },
            )

        # R1-14/F-01,F-02 - DPDP Act s.9(1) with Rules 2025 R.10. PLACEMENT:
        # a second new branch INSIDE the existing "consent status" step,
        # after WITHDRAWN / DENIED / EXPIRED and after R1-09's re-consent
        # branch, immediately before "active -> ALLOW". It sits exactly where
        # R1-09 put its own branch, and for the same reason: the consent's
        # STATUS is active - nobody withdrew, denied or expired anything -
        # but the consent may not be relied on, so this narrows only the case
        # that used to fall straight through to ALLOW. Nothing is reordered,
        # and a withdrawn/denied/expired consent still reports its own
        # outcome rather than this one, which is the more specific and more
        # useful answer for a principal asking why.
        #
        # Why it is needed at all when grant_consent already refuses: a
        # principal can be RE-ASSESSED as a child, or a parental consent can
        # be revoked, AFTER consents were granted. Those already-granted rows
        # would otherwise keep evaluating to ALLOW forever. The gate in
        # services/consent.py stops new ones being recorded; this stops old
        # ones being relied on.
        #
        # REQUIRE_CONSENT rather than DENY, and rather than a new outcome
        # value: DECISION_OUTCOMES in models/entities.py is what the KPI
        # bucketing and the decisions API enumerate, and REQUIRE_CONSENT is
        # precisely what this is - verifiable parental consent must be
        # obtained before the processing may continue.
        if consent.status in _active_consent_statuses() and not expired:
            child_gap = _s9_parental_consent_gap(db, customer, purpose)
            if child_gap is not None:
                d = Decision(
                    "REQUIRE_CONSENT",
                    child_gap["reason"],
                    False,
                    consent=consent,
                    policy=policy,
                    policy_version_number=policy_version_number,
                    policy_version_id=policy_version_id,
                )
                return _finish(
                    d, db, customer, purpose, data_category, processing_activity,
                    requested_by, source_app, persist, request_id,
                    extra_details={
                        "child_principal": True,
                        "s9_1_parental_consent_missing": True,
                        "age_assurance_id": child_gap["age_assurance_id"],
                    },
                )

        if consent.status in _active_consent_statuses() and not expired:
            reason = (
                f"Valid active consent exists for {purpose.name} "
                f"(status {consent.status}, version {consent.consent_version}) "
                f"and the consent has not expired or been withdrawn."
            )
            d = Decision(
                "ALLOW",
                reason,
                True,
                consent=consent,
                policy=policy,
                policy_version_number=policy_version_number,
                policy_version_id=policy_version_id,
            )
            return _finish(d, db, customer, purpose, data_category, processing_activity, requested_by, source_app, persist, request_id)

    # No valid consent
    if rule and rule.get("requires_active_consent", True) is False:
        reason = (
            f"No active consent required by policy {policy_code} for {purpose.name}, "
            f"processing {data_category.name} for {processing_activity.name}."
        )
        d = Decision("ALLOW", reason, True, policy=policy, policy_version_number=policy_version_number, policy_version_id=policy_version_id)
        return _finish(d, db, customer, purpose, data_category, processing_activity, requested_by, source_app, persist, request_id)

    if purpose.requires_consent is False:
        reason = (
            f"Purpose {purpose.name} does not require consent (legal basis {purpose.legal_basis}); "
            f"processing {processing_activity.name} on {data_category.name} is permitted."
        )
        d = Decision("ALLOW", reason, True, policy=policy, policy_version_number=policy_version_number, policy_version_id=policy_version_id)
        return _finish(d, db, customer, purpose, data_category, processing_activity, requested_by, source_app, persist, request_id)

    d = Decision(
        "REQUIRE_CONSENT",
        f"No valid consent exists for {purpose.name} processing {data_category.name} "
        f"for {processing_activity.name}. Consent must be obtained before processing.",
        False,
        policy=policy,
        policy_version_number=policy_version_number,
        policy_version_id=policy_version_id,
    )
    return _finish(d, db, customer, purpose, data_category, processing_activity, requested_by, source_app, persist, request_id)


def _finish(
    d: Decision,
    db: Session,
    customer: Customer,
    purpose: Purpose,
    data_category: DataCategory,
    processing_activity: ProcessingActivity,
    requested_by: str,
    source_app: str,
    persist: bool,
    request_id: Optional[str],
    extra_details: Optional[dict] = None,
) -> Decision:
    if not persist:
        return d
    log = ConsentDecisionLog(
        customer_id=customer.id,
        purpose_id=purpose.id,
        data_category_id=data_category.id,
        processing_activity_id=processing_activity.id,
        consent_id=d.consent.id if d.consent else None,
        decision=d.decision,
        reason=d.reason,
        consent_status=d.consent.status if d.consent else None,
        consent_version=d.consent.consent_version if d.consent else None,
        policy_id=d.policy.id if d.policy else None,
        policy_version_id=d.policy_version_id,
        policy_version_number=d.policy_version_number,
        requested_by=requested_by,
        source_app=source_app,
        request_id=request_id,
        details={"purpose_code": purpose.code, "data_category_code": data_category.code,
                 "processing_activity_code": processing_activity.code,
                 **(extra_details or {})},
        evaluated_at=utcnow(),
    )
    db.add(log)
    log_audit(
        db,
        "DECISION_EVALUATED",
        actor_username=requested_by,
        source_app=source_app,
        customer_id=customer.id,
        customer_external_id=customer.external_id,
        consent_id=d.consent.id if d.consent else None,
        purpose_id=purpose.id,
        purpose_code=purpose.code,
        policy_id=d.policy.id if d.policy else None,
        policy_code=d.policy.code if d.policy else None,
        consent_version=d.consent.consent_version if d.consent else None,
        policy_version=d.policy_version_number,
        decision=d.decision,
        reason=d.reason,
        request_id=request_id,
        commit=False,
    )
    db.commit()
    db.refresh(log)
    return d
