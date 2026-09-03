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
PERM_USER_MANAGE = "user.manage"
PERM_INTEGRATION = "integration.use"
PERM_CONTEXT_USE = "context.use"

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
    PERM_USER_MANAGE,
    PERM_INTEGRATION,
    PERM_CONTEXT_USE,
]

ROLE_PERMISSIONS: dict[str, list[str]] = {
    "admin": ALL_PERMISSIONS,
    "viewer": [
        PERM_DASHBOARD,
        PERM_CUSTOMER_VIEW,
        PERM_CONSENT_VIEW,
        PERM_PURPOSE_VIEW,
        PERM_POLICY_VIEW,
        PERM_AUDIT_VIEW,
    ],
    "jobhub_admin": [
        PERM_DASHBOARD,
        PERM_CUSTOMER_VIEW,
        PERM_CONSENT_VIEW,
        PERM_CONSENT_MANAGE,
        PERM_PURPOSE_VIEW,
        PERM_AUDIT_VIEW,
    ],
    "codex_admin": [
        PERM_DASHBOARD,
        PERM_CUSTOMER_VIEW,
        PERM_CONSENT_VIEW,
        PERM_CONSENT_MANAGE,
        PERM_PURPOSE_VIEW,
        PERM_AUDIT_VIEW,
    ],
    "skilllearn_admin": [
        PERM_DASHBOARD,
        PERM_CUSTOMER_VIEW,
        PERM_CONSENT_VIEW,
        PERM_CONSENT_MANAGE,
        PERM_PURPOSE_VIEW,
        PERM_AUDIT_VIEW,
    ],
}

ROLE_DESCRIPTIONS = {
    "admin": "Full access to all platform features",
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
