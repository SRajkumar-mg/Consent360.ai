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
}

ORG_SCOPE_MAP = {
    "jobhub_admin": "JOBHUB",
    "codex_admin": "CODEX",
    "skilllearn_admin": "SKILLLEARN",
}
