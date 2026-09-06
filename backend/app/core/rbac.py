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
PERM_TENANT_MANAGE = "tenant.manage"
PERM_NOTICE_MANAGE = "notice.manage"
PERM_RETENTION_MANAGE = "retention.manage"
PERM_RIGHTS_MANAGE = "rights.manage"
PERM_REPORTS_VIEW = "reports.view"
PERM_DPO_VIEW = "dpo.view"
PERM_AUDITOR_VIEW = "auditor.view"

# ---------------------------------------------------------------------------
# RBAC extension: fine-grained, module-scoped permission strings for the
# consent-principal / tenant / platform-operator role model.  Additive only —
# existing permission constants above are untouched so existing roles keep
# working unchanged.
# ---------------------------------------------------------------------------
PERM_TENANT_VIEW = "tenant.view"
PERM_TENANT_UPDATE = "tenant.update"

PERM_USERS_VIEW = "users.view"
PERM_USERS_INVITE = "users.invite"
PERM_USERS_REMOVE = "users.remove"

PERM_NOTICE_VIEW = "notice.view"
PERM_NOTICE_CREATE = "notice.create"
PERM_NOTICE_UPDATE = "notice.update"
PERM_NOTICE_PUBLISH = "notice.publish"
PERM_NOTICE_ARCHIVE = "notice.archive"

PERM_PURPOSE_CREATE = "purpose.create"
PERM_PURPOSE_UPDATE = "purpose.update"

PERM_CONSENT_GRANT = "consent.grant"
PERM_CONSENT_DENY = "consent.deny"
PERM_CONSENT_WITHDRAW = "consent.withdraw"
PERM_CONSENT_EXPORT = "consent.export"

PERM_RIGHTS_VIEW = "rights.view"
PERM_RIGHTS_RESPOND = "rights.respond"

PERM_GRIEVANCE_VIEW = "grievance.view"
PERM_GRIEVANCE_RESPOND = "grievance.respond"
PERM_GRIEVANCE_EXPORT = "grievance.export"

PERM_RETENTION_VIEW = "retention.view"
PERM_RETENTION_UPDATE = "retention.update"

PERM_AUDIT_VIEW_FINE = "audit.view"  # alias of PERM_AUDIT_VIEW; kept for module-scoped naming parity
PERM_AUDIT_EXPORT_FINE = "audit.export"

PERM_API_KEY_VIEW = "api_key.view"
PERM_API_KEY_CREATE = "api_key.create"
PERM_API_KEY_ROTATE = "api_key.rotate"

PERM_WEBHOOK_VIEW = "webhook.view"
PERM_WEBHOOK_CONFIGURE = "webhook.configure"

# Guardian-child linking (verification-gated; enforced via GuardianChildLink table,
# never via a role flag alone).
PERM_GUARDIAN_LINK_MANAGE = "guardian.link_manage"

# Own-data scope for data_principal / guardian roles — enforced in query filters,
# not just granted as a blanket permission.
PERM_OWN_CONSENT_MANAGE = "own_consent.manage"
PERM_OWN_PROFILE_MANAGE = "own_profile.manage"

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
    PERM_TENANT_MANAGE,
    PERM_NOTICE_MANAGE,
    PERM_RETENTION_MANAGE,
    PERM_RIGHTS_MANAGE,
    PERM_REPORTS_VIEW,
    PERM_DPO_VIEW,
    PERM_AUDITOR_VIEW,
    PERM_TENANT_VIEW,
    PERM_TENANT_UPDATE,
    PERM_USERS_VIEW,
    PERM_USERS_INVITE,
    PERM_USERS_REMOVE,
    PERM_NOTICE_VIEW,
    PERM_NOTICE_CREATE,
    PERM_NOTICE_UPDATE,
    PERM_NOTICE_PUBLISH,
    PERM_NOTICE_ARCHIVE,
    PERM_PURPOSE_CREATE,
    PERM_PURPOSE_UPDATE,
    PERM_CONSENT_GRANT,
    PERM_CONSENT_DENY,
    PERM_CONSENT_WITHDRAW,
    PERM_CONSENT_EXPORT,
    PERM_RIGHTS_VIEW,
    PERM_RIGHTS_RESPOND,
    PERM_GRIEVANCE_VIEW,
    PERM_GRIEVANCE_RESPOND,
    PERM_GRIEVANCE_EXPORT,
    PERM_RETENTION_VIEW,
    PERM_RETENTION_UPDATE,
    PERM_API_KEY_VIEW,
    PERM_API_KEY_CREATE,
    PERM_API_KEY_ROTATE,
    PERM_WEBHOOK_VIEW,
    PERM_WEBHOOK_CONFIGURE,
    PERM_GUARDIAN_LINK_MANAGE,
    PERM_OWN_CONSENT_MANAGE,
    PERM_OWN_PROFILE_MANAGE,
]

# ---------------------------------------------------------------------------
# New actor/role model (Step 3). These are seeded as real `roles` table rows
# (see seed.py) — this dict is the single source of truth for their default
# permission sets, mirroring the existing ROLE_PERMISSIONS pattern below.
# Kept in a separate dict (merged into ROLE_PERMISSIONS) so the pre-existing
# demo roles (admin/consent_manager/viewer/jobhub_admin/...) are untouched.
# ---------------------------------------------------------------------------
NEW_ROLE_PERMISSIONS: dict[str, list[str]] = {
    "data_principal": [
        PERM_OWN_CONSENT_MANAGE,
        PERM_OWN_PROFILE_MANAGE,
        PERM_CONSENT_VIEW,
        PERM_CONSENT_GRANT,
        PERM_CONSENT_DENY,
        PERM_CONSENT_WITHDRAW,
        PERM_NOTICE_VIEW,
        PERM_RIGHTS_VIEW,
        PERM_GRIEVANCE_VIEW,
    ],
    "guardian": [
        PERM_OWN_PROFILE_MANAGE,
        PERM_GUARDIAN_LINK_MANAGE,
        PERM_CONSENT_VIEW,
        PERM_CONSENT_GRANT,
        PERM_CONSENT_DENY,
        PERM_CONSENT_WITHDRAW,
        PERM_NOTICE_VIEW,
        PERM_GRIEVANCE_VIEW,
    ],
    "tenant_admin": [
        PERM_TENANT_VIEW, PERM_TENANT_UPDATE, PERM_TENANT_MANAGE,
        PERM_USERS_VIEW, PERM_USERS_INVITE, PERM_USERS_REMOVE,
        PERM_PURPOSE_VIEW, PERM_PURPOSE_CREATE, PERM_PURPOSE_UPDATE,
        PERM_CATEGORY_MANAGE,
        PERM_INTEGRATION, PERM_API_KEY_VIEW, PERM_API_KEY_CREATE, PERM_API_KEY_ROTATE,
        PERM_WEBHOOK_VIEW, PERM_WEBHOOK_CONFIGURE,
        PERM_REPORTS_VIEW, PERM_AUDIT_VIEW, PERM_AUDIT_EXPORT,
        PERM_DASHBOARD,
    ],
    "tenant_privacy_officer": [
        PERM_NOTICE_VIEW, PERM_NOTICE_CREATE, PERM_NOTICE_UPDATE, PERM_NOTICE_PUBLISH, PERM_NOTICE_ARCHIVE,
        PERM_NOTICE_MANAGE,
        PERM_PURPOSE_VIEW, PERM_PURPOSE_CREATE, PERM_PURPOSE_UPDATE,
        PERM_RETENTION_VIEW, PERM_RETENTION_UPDATE,
        PERM_RIGHTS_VIEW, PERM_RIGHTS_RESPOND, PERM_RIGHTS_MANAGE,
        PERM_GRIEVANCE_VIEW, PERM_GRIEVANCE_RESPOND, PERM_GRIEVANCE_EXPORT,
        PERM_REPORTS_VIEW, PERM_AUDIT_VIEW, PERM_AUDIT_EXPORT,
        PERM_DASHBOARD, PERM_DPO_VIEW,
        PERM_CONSENT_VIEW, PERM_CONSENT_EXPORT,
    ],
    "tenant_developer": [
        PERM_INTEGRATION, PERM_API_KEY_VIEW, PERM_API_KEY_CREATE, PERM_API_KEY_ROTATE,
        PERM_WEBHOOK_VIEW, PERM_WEBHOOK_CONFIGURE,
        PERM_DASHBOARD,
    ],
    "tenant_support": [
        PERM_CUSTOMER_VIEW, PERM_CONSENT_VIEW,
        PERM_DASHBOARD,
    ],
    "platform_super_admin": ALL_PERMISSIONS,
    "platform_ops_admin": [
        PERM_DASHBOARD, PERM_REPORTS_VIEW,
        PERM_AUDIT_VIEW,
        PERM_API_KEY_VIEW, PERM_WEBHOOK_VIEW,
    ],
    "platform_compliance_officer": [
        PERM_DASHBOARD, PERM_REPORTS_VIEW,
        PERM_AUDIT_VIEW, PERM_AUDIT_EXPORT,
        PERM_DPO_VIEW,
        PERM_RIGHTS_VIEW, PERM_RIGHTS_RESPOND,
        PERM_GRIEVANCE_VIEW, PERM_GRIEVANCE_EXPORT,
        PERM_RETENTION_VIEW,
        PERM_TENANT_VIEW,
        PERM_CONSENT_VIEW, PERM_CONSENT_EXPORT,
    ],
    "platform_auditor": [
        PERM_AUDIT_VIEW, PERM_AUDITOR_VIEW,
        PERM_CONSENT_VIEW, PERM_NOTICE_VIEW,
        PERM_REPORTS_VIEW, PERM_DASHBOARD,
        PERM_TENANT_VIEW,
    ],
    "platform_support_admin": [
        PERM_DASHBOARD, PERM_TENANT_VIEW, PERM_CUSTOMER_VIEW,
        PERM_CONSENT_VIEW,
    ],
    # DPDP-specific responsibilities are permission sets layered on top of a
    # base tenant role via a second user_tenant_roles row (Phase 2 default;
    # see seed.py) — NOT duplicate parallel roles.
    "tenant_dpo": [
        PERM_DPO_VIEW, PERM_REPORTS_VIEW, PERM_AUDIT_VIEW,
        PERM_RETENTION_VIEW,
    ],
    "tenant_grievance_officer": [
        PERM_GRIEVANCE_VIEW, PERM_GRIEVANCE_RESPOND, PERM_GRIEVANCE_EXPORT,
        PERM_RIGHTS_VIEW, PERM_RIGHTS_RESPOND, PERM_RIGHTS_MANAGE,
    ],
}

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
    ],
    "viewer": [
        PERM_DASHBOARD,
        PERM_CUSTOMER_VIEW,
        PERM_CONSENT_VIEW,
        PERM_PURPOSE_VIEW,
        PERM_POLICY_VIEW,
        PERM_AUDIT_VIEW,
        PERM_AUDIT_EXPORT,
        PERM_REPORTS_VIEW,
    ],
    "jobhub_admin": [
        PERM_DASHBOARD,
        PERM_CUSTOMER_VIEW,
        PERM_CONSENT_VIEW,
        PERM_CONSENT_MANAGE,
        PERM_PURPOSE_VIEW,
        PERM_AUDIT_VIEW,
        PERM_AUDIT_EXPORT,
        PERM_RIGHTS_MANAGE,
        PERM_REPORTS_VIEW,
    ],
    "codex_admin": [
        PERM_DASHBOARD,
        PERM_CUSTOMER_VIEW,
        PERM_CONSENT_VIEW,
        PERM_CONSENT_MANAGE,
        PERM_PURPOSE_VIEW,
        PERM_AUDIT_VIEW,
        PERM_AUDIT_EXPORT,
        PERM_RIGHTS_MANAGE,
        PERM_REPORTS_VIEW,
    ],
    "skilllearn_admin": [
        PERM_DASHBOARD,
        PERM_CUSTOMER_VIEW,
        PERM_CONSENT_VIEW,
        PERM_CONSENT_MANAGE,
        PERM_PURPOSE_VIEW,
        PERM_AUDIT_VIEW,
        PERM_AUDIT_EXPORT,
        PERM_RIGHTS_MANAGE,
        PERM_REPORTS_VIEW,
    ],
    "dpo": [
        PERM_DASHBOARD,
        PERM_CUSTOMER_VIEW,
        PERM_CONSENT_VIEW,
        PERM_CONSENT_MANAGE,
        PERM_PURPOSE_VIEW,
        PERM_POLICY_VIEW,
        PERM_AUDIT_VIEW,
        PERM_DPO_VIEW,
    ],
    "auditor": [
        PERM_DASHBOARD,
        PERM_CUSTOMER_VIEW,
        PERM_CONSENT_VIEW,
        PERM_AUDIT_VIEW,
        PERM_DPO_VIEW,
        PERM_AUDITOR_VIEW,
    ],
}

ROLE_DESCRIPTIONS = {
    "admin": "Full access to all platform features",
    "consent_manager": "Manages customer consents and audit trails",
    "viewer": "Read-only access to all data",
    "jobhub_admin": "Manages JobHub organization consents and customers",
    "codex_admin": "Manages Codex organization consents and customers",
    "skilllearn_admin": "Manages SkillLearn organization consents and customers",
    "dpo": "Data Protection Officer - oversees compliance and data protection",
    "auditor": "Auditor - reviews access logs and compliance audits",
    "data_principal": "External user managing their own consent and data",
    "guardian": "External user managing consent on behalf of verified linked children",
    "tenant_admin": "Tenant-scoped administrator - settings, users, integrations, analytics",
    "tenant_privacy_officer": "Tenant-scoped privacy/compliance management (notices, purposes, retention, rights, grievances)",
    "tenant_developer": "Tenant-scoped API/webhook/sandbox access, no unmasked PII",
    "tenant_support": "Tenant-scoped read/support access to consent status and customer search",
    "platform_super_admin": "Consent360 operator - full platform administration",
    "platform_ops_admin": "Consent360 operator - infra, deployments, system health; no tenant privacy-config or PII",
    "platform_compliance_officer": "Consent360 operator - global compliance dashboards, audit investigations, DPDP config",
    "platform_auditor": "Consent360 operator - strictly read-only audit/compliance access",
    "platform_support_admin": "Consent360 operator - limited tenant/user lookup and support actions",
    "tenant_dpo": "DPDP designation: tenant's official Data Protection Officer (assignment layered on a base tenant role)",
    "tenant_grievance_officer": "DPDP designation: tenant's grievance queue owner (assignment layered on a base tenant role)",
}

ORG_SCOPE_MAP = {
    "jobhub_admin": "JOBHUB",
    "codex_admin": "CODEX",
    "skilllearn_admin": "SKILLLEARN",
}

# Roles that operate at platform-global scope (tenant_id is NULL in
# user_tenant_roles for these) rather than being bound to a single tenant.
PLATFORM_ROLE_NAMES = {
    "platform_super_admin",
    "platform_ops_admin",
    "platform_compliance_officer",
    "platform_auditor",
    "platform_support_admin",
}

# DPDP-specific designations: assigned as an *additional* user_tenant_roles
# row alongside a base tenant role, never as a replacement for it.
DPDP_DESIGNATION_ROLE_NAMES = {
    "tenant_dpo",
    "tenant_grievance_officer",
}

ROLE_PERMISSIONS.update(NEW_ROLE_PERMISSIONS)
