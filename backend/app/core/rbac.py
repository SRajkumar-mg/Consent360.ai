PERM_DASHBOARD = "dashboard.view"
PERM_CUSTOMER_VIEW = "customer.view"
PERM_CONSENT_VIEW = "consent.view"
PERM_CONSENT_MANAGE = "consent.manage"
PERM_PURPOSE_VIEW = "purpose.view"
PERM_PURPOSE_MANAGE = "purpose.manage"
PERM_CATEGORY_MANAGE = "category.manage"
PERM_ACTIVITY_MANAGE = "activity.manage"
PERM_POLICY_VIEW = "policy.view"
PERM_POLICY_MANAGE = "policy.manage"
PERM_AUDIT_VIEW = "audit.view"
PERM_AUDIT_EXPORT = "audit.export"
PERM_USER_MANAGE = "user.manage"
PERM_INTEGRATION = "integration.use"
PERM_CONTEXT_USE = "context.use"
# R2-06 (G-01..G-04): grievance redressal is its own permission pair rather
# than being folded into consent.view/consent.manage, because the role that
# has to be able to act on a grievance is the DPO - s.10(2)(a) makes them the
# point of contact for grievance redressal - and the DPO deliberately has no
# consent.manage (it does not operate day-to-day consent actions; see the
# "dpo" entry below). Reusing consent.manage would have left the statutory
# grievance officer unable to resolve the grievances escalated to them.
PERM_GRIEVANCE_VIEW = "grievance.view"
PERM_GRIEVANCE_MANAGE = "grievance.manage"
# R3-08 (I-01..I-04): personal data breach management gets its own pair, and
# deliberately NOT policy.manage. Filing - or failing to file - a statutory
# breach notification with the Data Protection Board under R.7(2), or the
# CERT-In report under the 28 Apr 2022 directions, is a regulator-facing act
# of a wholly different character from editing a policy document. Anyone who
# can do the second should not thereby be able to do the first.
#
# `breach.manage` is a heavy bundle - register, record awareness (which starts
# every statutory clock), scope the affected principals, issue their notices,
# file with the Board and CERT-In, log an extension request, close - and is
# granted only to `admin` and `dpo` below. `breach.view` is the compliance-
# reporting half and goes to every role that can already read the audit trail.
#
# Reading a filing's *content* (a principal notice quotes that principal's own
# data; a Board report aggregates every affected principal) stays gated on
# `audit.export`, the same bar as the ledger export - see
# app/api/routes/breaches.py.
PERM_BREACH_VIEW = "breach.view"
PERM_BREACH_MANAGE = "breach.manage"
# R1-06 (G-01..G-04): the retention and erasure engine gets its own pair, and
# deliberately NOT `policy.manage`. R1-10 gated record-class retention on
# `policy.manage` because "how long do we keep this class of record" is a
# policy judgement about categories. This is a different act entirely:
# irreversibly destroying one named human being's personal data. Anyone who
# can edit a retention schedule should not thereby be able to erase a person.
#
# `erasure.manage` covers everything that can lead to a destruction - writing
# a retention policy, raising an erasure, authorising one, executing one - and
# also placing and releasing a legal hold, because releasing a hold un-blocks
# a statutory erasure and is therefore exactly as consequential as the erasure
# it unblocks. It is granted only to `admin` and `dpo`: s.8(7) erasure and its
# s.12(3) counterpart are the DPO's judgement, not an operational one.
# `erasure.view` is the compliance-reporting half (the register, the backlog,
# K-28..K-31) and goes to every role that can already read the audit trail.
PERM_ERASURE_VIEW = "erasure.view"
PERM_ERASURE_MANAGE = "erasure.manage"
# R2-05 (E-01..E-03, E-05, E-07, E-09): the data-principal rights desk gets
# its own pair, and deliberately NOT `customer.view`/`consent.manage`.
#
# `rights.view` is the queue and the SLA numbers (K-20..K-22): who has asked
# for what, and whether we are answering inside the period we published. That
# is compliance reporting and goes to every role that can already read the
# audit trail.
#
# `rights.manage` is a materially heavier thing than it looks, which is why it
# is not folded into an existing permission:
#
#   * it can trigger the R.14(2) verification code and mark a request verified,
#     after which an ACCESS package - one identified person's entire consent
#     record, contact details, and the list of every processor their data went
#     to - can be served. Fulfilling that request for the wrong person is the
#     single highest-consequence disclosure this platform can make;
#   * it can write to a `customers` row under s.12(1). `customer.view` is
#     read-only everywhere else in this codebase and must stay that way;
#   * it can activate a s.14 nomination, handing a third party the ability to
#     exercise a principal's rights at the moment the principal cannot object.
#
# It deliberately does NOT carry the power to erase: approving a s.12(3)
# request creates an `erasure_jobs` row through the R1-06 engine, and
# authorising/executing that job still needs `erasure.manage`. Two different
# people can therefore be required for a deletion, and the rights desk cannot
# destroy anybody's data on its own.
PERM_RIGHTS_VIEW = "rights.view"
PERM_RIGHTS_MANAGE = "rights.manage"

# `customer.view` answers "may this staff member work this person's record".
# `customer.contact.view` answers the separate, narrower question "may they
# read the person's actual email address and telephone number".
#
# The two were the same permission until now, and the admin console papered
# over that by masking email and phone in the browser while the API kept
# returning them in full - a display convention presented to staff as a
# privacy control. Anyone holding `customer.view` could read every principal's
# contact details straight out of the JSON, so the screen promised a
# protection that did not exist. Masking is now decided on the server: the
# customer response layer emits `mask_identifier(...)` unless the caller holds
# this permission (see app/schemas/schemas.py::CustomerOut.for_staff).
#
# NAME IS DELIBERATELY NOT COVERED. It stays visible to every `customer.view`
# holder, because a staff member cannot work a grievance (s.10(2)(a)), a
# rights request (s.11-14) or a consent queue without knowing which human
# being the record concerns - "who is this about" is the irreducible minimum
# for doing the job at all. A full telephone number is not: it is needed to
# *contact* the principal, which is a different and much rarer act than
# identifying them. Masking the name would break the work; masking the
# contact details does not.
#
# Granted below to `admin` and `dpo` only. That is a deliberate least-
# privilege starting point, not an exhaustive judgement: `auditor` reviews
# the compliance trail and `viewer` reads dashboards, and neither needs a
# principal's phone number to do it. The three org-scoped admins
# (`jobhub_admin`, `codex_admin`, `skilllearn_admin`) also do NOT hold it
# yet - recorded here as a decision rather than left as an oversight. They
# run day-to-day consent operations for one tenant and may well turn out to
# need it; widening the grant is a one-line change here plus a re-seed (or
# a restart, since sync_roles() below re-syncs on every boot), and is far
# easier than clawing the capability back once it has been handed out.
PERM_CUSTOMER_CONTACT_VIEW = "customer.contact.view"

ALL_PERMISSIONS = [
    PERM_DASHBOARD,
    PERM_CUSTOMER_VIEW,
    PERM_CONSENT_VIEW,
    PERM_CONSENT_MANAGE,
    PERM_PURPOSE_VIEW,
    PERM_PURPOSE_MANAGE,
    PERM_CATEGORY_MANAGE,
    PERM_ACTIVITY_MANAGE,
    PERM_POLICY_VIEW,
    PERM_POLICY_MANAGE,
    PERM_AUDIT_VIEW,
    PERM_AUDIT_EXPORT,
    PERM_USER_MANAGE,
    PERM_INTEGRATION,
    PERM_CONTEXT_USE,
    PERM_GRIEVANCE_VIEW,
    PERM_GRIEVANCE_MANAGE,
    PERM_BREACH_VIEW,
    PERM_BREACH_MANAGE,
    PERM_ERASURE_VIEW,
    PERM_ERASURE_MANAGE,
    PERM_RIGHTS_VIEW,
    PERM_RIGHTS_MANAGE,
    PERM_CUSTOMER_CONTACT_VIEW,
]

ROLE_PERMISSIONS: dict[str, list[str]] = {
    "admin": ALL_PERMISSIONS,
    "consent_manager": [
        PERM_DASHBOARD,
        PERM_CUSTOMER_VIEW,
        PERM_CONSENT_VIEW,
        PERM_CONSENT_MANAGE,
        PERM_PURPOSE_VIEW,
        PERM_POLICY_VIEW,
        PERM_AUDIT_VIEW,
        PERM_GRIEVANCE_VIEW,
        PERM_GRIEVANCE_MANAGE,
        # R3-08: read-only. Grievance handling is routine operational work;
        # notifying the Board of a breach is not, and this role is not the
        # statutory point of contact for it.
        PERM_BREACH_VIEW,
        # R1-06: read-only. Erasing a data principal is a DPO decision.
        PERM_ERASURE_VIEW,
        # R2-05: the rights desk mirrors the grievance queue's grants - the
        # same people work both, and a role that can resolve a grievance
        # about an ignored access request must be able to work the request.
        PERM_RIGHTS_VIEW,
        PERM_RIGHTS_MANAGE,
    ],
    # R3-02/R-01/N-03: BRD 4.6.1's named role set (Admin, DPO, Auditor,
    # Operator) is fixed roles seeded from rbac.py, matching how every
    # other role here works - "custom role management" is handled instead
    # by admin.py's role CRUD (POST/PUT/DELETE /admin/roles) for roles NOT
    # in this dict, which sync_roles() below never touches.
    "dpo": [
        # The DPO (s.10(2)(a): India-based, reports to the board, the
        # grievance point of contact) oversees and approves policy/consent
        # design and reviews the compliance trail; it does not operate day
        # to day consent actions (no PERM_CONSENT_MANAGE) or manage staff
        # accounts (no PERM_USER_MANAGE) - those are Admin/Operator's jobs.
        PERM_DASHBOARD,
        PERM_CUSTOMER_VIEW,
        PERM_CONSENT_VIEW,
        PERM_PURPOSE_VIEW,
        PERM_PURPOSE_MANAGE,
        PERM_POLICY_VIEW,
        PERM_POLICY_MANAGE,
        PERM_AUDIT_VIEW,
        PERM_AUDIT_EXPORT,
        # s.10(2)(a): the DPO is the grievance point of contact, and the
        # escalation path for an overdue grievance ends here. They must be
        # able to work the queue, not merely read it.
        PERM_GRIEVANCE_VIEW,
        PERM_GRIEVANCE_MANAGE,
        # R3-08: the DPO decides whether an incident is a notifiable personal
        # data breach, records the moment of awareness that starts all three
        # statutory clocks, and signs and files every regulator report. This
        # is the role the breach workflow is built around.
        PERM_BREACH_VIEW,
        PERM_BREACH_MANAGE,
        # R1-06: s.8(7) erasure and its s.12(3) counterpart are the DPO's
        # judgement - what must be erased, what a law requires us to keep, and
        # when a legal hold displaces both.
        PERM_ERASURE_VIEW,
        PERM_ERASURE_MANAGE,
        # R2-05: the rights desk mirrors the grievance queue's grants - the
        # same people work both, and a role that can resolve a grievance
        # about an ignored access request must be able to work the request.
        PERM_RIGHTS_VIEW,
        PERM_RIGHTS_MANAGE,
        # Contact details: the DPO is the statutory point of contact for
        # grievance redressal (s.10(2)(a)) and the person who has to reach
        # a principal to answer one - and who serves the s.8(6)/R.7(1)
        # breach notice to each affected principal individually. Reaching
        # them requires their real email address and telephone number.
        PERM_CUSTOMER_CONTACT_VIEW,
    ],
    "auditor": [
        # Strictly read-only, for independent compliance review - every
        # *_VIEW/_EXPORT permission, zero *_MANAGE ones.
        PERM_DASHBOARD,
        PERM_CUSTOMER_VIEW,
        PERM_CONSENT_VIEW,
        PERM_PURPOSE_VIEW,
        PERM_POLICY_VIEW,
        PERM_AUDIT_VIEW,
        PERM_AUDIT_EXPORT,
        PERM_GRIEVANCE_VIEW,
        PERM_BREACH_VIEW,
        PERM_ERASURE_VIEW,
        # R2-05: the rights desk mirrors the grievance queue's grants - the
        # read-only half, for compliance reporting (K-20..K-22).
        PERM_RIGHTS_VIEW,
    ],
    "operator": [
        # Day-to-day consent operations staff - same shape as
        # consent_manager minus policy visibility, which is a DPO/Admin
        # concern rather than an operational one.
        PERM_DASHBOARD,
        PERM_CUSTOMER_VIEW,
        PERM_CONSENT_VIEW,
        PERM_CONSENT_MANAGE,
        PERM_PURPOSE_VIEW,
        PERM_AUDIT_VIEW,
        PERM_GRIEVANCE_VIEW,
        PERM_GRIEVANCE_MANAGE,
        PERM_BREACH_VIEW,
        PERM_ERASURE_VIEW,
        # R2-05: the rights desk mirrors the grievance queue's grants - the
        # same people work both, and a role that can resolve a grievance
        # about an ignored access request must be able to work the request.
        PERM_RIGHTS_VIEW,
        PERM_RIGHTS_MANAGE,
    ],
    "viewer": [
        PERM_DASHBOARD,
        PERM_CUSTOMER_VIEW,
        PERM_CONSENT_VIEW,
        PERM_PURPOSE_VIEW,
        PERM_POLICY_VIEW,
        PERM_AUDIT_VIEW,
        PERM_GRIEVANCE_VIEW,
        PERM_BREACH_VIEW,
        PERM_ERASURE_VIEW,
        # R2-05: the rights desk mirrors the grievance queue's grants - the
        # read-only half, for compliance reporting (K-20..K-22).
        PERM_RIGHTS_VIEW,
    ],
    "jobhub_admin": [
        PERM_DASHBOARD,
        PERM_CUSTOMER_VIEW,
        PERM_CONSENT_VIEW,
        PERM_CONSENT_MANAGE,
        PERM_PURPOSE_VIEW,
        PERM_AUDIT_VIEW,
        PERM_GRIEVANCE_VIEW,
        PERM_GRIEVANCE_MANAGE,
        # R3-08: read-only, scoped to their own tenant by ORG_SCOPE_MAP. A
        # per-application admin is not the data fiduciary's statutory point of
        # contact with the Board; escalate to the DPO to file.
        PERM_BREACH_VIEW,
        # R1-06: read-only for the same reason.
        PERM_ERASURE_VIEW,
        # R2-05: the rights desk mirrors the grievance queue's grants - the
        # same people work both, and a role that can resolve a grievance
        # about an ignored access request must be able to work the request.
        PERM_RIGHTS_VIEW,
        PERM_RIGHTS_MANAGE,
    ],
    "codex_admin": [
        PERM_DASHBOARD,
        PERM_CUSTOMER_VIEW,
        PERM_CONSENT_VIEW,
        PERM_CONSENT_MANAGE,
        PERM_PURPOSE_VIEW,
        PERM_AUDIT_VIEW,
        PERM_GRIEVANCE_VIEW,
        PERM_GRIEVANCE_MANAGE,
        # R3-08: read-only, scoped to their own tenant by ORG_SCOPE_MAP. A
        # per-application admin is not the data fiduciary's statutory point of
        # contact with the Board; escalate to the DPO to file.
        PERM_BREACH_VIEW,
        # R1-06: read-only for the same reason.
        PERM_ERASURE_VIEW,
        # R2-05: the rights desk mirrors the grievance queue's grants - the
        # same people work both, and a role that can resolve a grievance
        # about an ignored access request must be able to work the request.
        PERM_RIGHTS_VIEW,
        PERM_RIGHTS_MANAGE,
    ],
    "skilllearn_admin": [
        PERM_DASHBOARD,
        PERM_CUSTOMER_VIEW,
        PERM_CONSENT_VIEW,
        PERM_CONSENT_MANAGE,
        PERM_PURPOSE_VIEW,
        PERM_AUDIT_VIEW,
        PERM_GRIEVANCE_VIEW,
        PERM_GRIEVANCE_MANAGE,
        # R3-08: read-only, scoped to their own tenant by ORG_SCOPE_MAP. A
        # per-application admin is not the data fiduciary's statutory point of
        # contact with the Board; escalate to the DPO to file.
        PERM_BREACH_VIEW,
        # R1-06: read-only for the same reason.
        PERM_ERASURE_VIEW,
        # R2-05: the rights desk mirrors the grievance queue's grants - the
        # same people work both, and a role that can resolve a grievance
        # about an ignored access request must be able to work the request.
        PERM_RIGHTS_VIEW,
        PERM_RIGHTS_MANAGE,
    ],
}

ROLE_DESCRIPTIONS = {
    "admin": "Full access to all platform features",
    "consent_manager": "Manages customer consents and audit trails",
    "dpo": "Data Protection Officer: oversees policy, purpose design and the compliance audit trail",
    "auditor": "Independent, read-only compliance review across all data and audit trails",
    "operator": "Day-to-day consent operations: customers, consents and their own audit trail",
    "viewer": "Read-only access to all data",
    "jobhub_admin": "Manages JobHub organization consents and customers",
    "codex_admin": "Manages Codex organization consents and customers",
    "skilllearn_admin": "Manages SkillLearn organization consents and customers",
}

ORG_SCOPE_MAP = {
    "jobhub_admin": "JOBHUB",
    "codex_admin": "CODEX",
    "skilllearn_admin": "SKILLLEARN",
}


def sync_roles(db) -> None:
    """Idempotently upsert every role in ROLE_PERMISSIONS into the `roles`
    table - creating DPO/AUDITOR/OPERATOR (and any future addition here)
    the first time they're missing, and re-syncing permissions for a role
    that already exists but has drifted from this file, exactly like
    `seed.py` already documents doing ("re-syncs role permissions from
    rbac.py"). Called from app/main.py's lifespan on every startup, so
    these roles exist even before an operator re-runs seed.py.

    Never touches a role NOT listed in ROLE_PERMISSIONS: custom roles
    created via `POST /admin/roles` (app/api/routes/admin.py) are a
    disjoint set this function will never create, modify or delete.
    """
    from app.models.entities import Role
    from app.services.audit import log_audit

    existing = {r.name: r for r in db.query(Role).all()}
    changed = False
    for name, permissions in ROLE_PERMISSIONS.items():
        role = existing.get(name)
        if role is None:
            role = Role(name=name, description=ROLE_DESCRIPTIONS.get(name, ""), permissions=list(permissions), is_system=True)
            db.add(role)
            db.flush()
            log_audit(
                db, "ROLE_CHANGED", actor_username="system", actor_type="SYSTEM", source_app="SYSTEM",
                reason=f"Role '{name}' provisioned at startup", metadata={"role_id": role.id, "permissions": list(permissions)},
                commit=False,
            )
            changed = True
        elif (role.permissions or []) != list(permissions):
            role.permissions = list(permissions)
            log_audit(
                db, "ROLE_CHANGED", actor_username="system", actor_type="SYSTEM", source_app="SYSTEM",
                reason=f"Role '{name}' permissions re-synced from rbac.py at startup",
                metadata={"role_id": role.id, "permissions": list(permissions)}, commit=False,
            )
            changed = True
    if changed:
        db.commit()
