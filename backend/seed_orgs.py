"""Seed organizations and their users.

Idempotent and safe to re-run: an Organization row is created only if it
does not already exist (e.g. one of CRM_PORTAL/CAREER_HUB may already have
been auto-provisioned blank by app.services.tenancy.resolve_tenant_id the
first time that demo site's traffic touched the platform), and every demo
tenant - whether just created here or pre-existing - has its Rule 3 contact
fields (dpo_name/dpo_email/dpo_phone, withdraw/rights/grievance/board_complaint
URLs) filled in wherever they are still blank. Re-running this script never
overwrites a value an operator has already customised.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.core.encryption import hmac_digest
from app.models.entities import Organization, OrganizationUser

db = SessionLocal()

# org_portal_users is only populated for tenants that also have an org
# portal login (the staff-facing org dashboard, Task 2's /organizations
# routes); CRM_PORTAL and CAREER_HUB are demo *source_app* tenants (CRM and
# job-portal identify themselves with these codes when calling the
# integration API) with no org portal users of their own, so their entry is
# an empty user list - only the Organization row (and its Rule 3 contact
# fields) matters for them.
DEMO_TENANTS = [
    {
        "code": "JOBHUB",
        "name": "Jobhub",
        "domain": "jobhub.local",
        "description": "Jobhub platform",
        "users": [
            ("jobhub.admin", "JobHub Admin", "admin@jobhub.local", "jobhub_admin"),
            ("jobhub.viewer", "JobHub Viewer", "viewer@jobhub.local", "jobhub_viewer"),
        ],
        "contacts": dict(
            dpo_name="Anita Rao", dpo_email="dpo@jobhub.local", dpo_phone="+91-80-4000-1001",
            withdraw_url="https://jobhub.local/privacy/withdraw-consent",
            rights_url="https://jobhub.local/privacy/your-rights",
            grievance_url="https://jobhub.local/privacy/grievance",
            board_complaint_url="https://www.dpb.gov.in/complaints",
        ),
    },
    {
        "code": "CODEX",
        "name": "Codex",
        "domain": "codex.local",
        "description": "Codex platform",
        "users": [
            ("codex.admin", "Codex Admin", "admin@codex.local", "codex_admin"),
            ("codex.viewer", "Codex Viewer", "viewer@codex.local", "codex_viewer"),
        ],
        "contacts": dict(
            dpo_name="Rahul Menon", dpo_email="dpo@codex.local", dpo_phone="+91-80-4000-1002",
            withdraw_url="https://codex.local/privacy/withdraw-consent",
            rights_url="https://codex.local/privacy/your-rights",
            grievance_url="https://codex.local/privacy/grievance",
            board_complaint_url="https://www.dpb.gov.in/complaints",
        ),
    },
    {
        "code": "SKILLLEARN",
        "name": "Skilllearn",
        "domain": "skilllearn.local",
        "description": "Skilllearn platform",
        "users": [
            ("skilllearn.admin", "SkillLearn Admin", "admin@skilllearn.local", "skilllearn_admin"),
            ("skilllearn.viewer", "SkillLearn Viewer", "viewer@skilllearn.local", "skilllearn_viewer"),
        ],
        "contacts": dict(
            dpo_name="Priya Nair", dpo_email="dpo@skilllearn.local", dpo_phone="+91-80-4000-1003",
            withdraw_url="https://skilllearn.local/privacy/withdraw-consent",
            rights_url="https://skilllearn.local/privacy/your-rights",
            grievance_url="https://skilllearn.local/privacy/grievance",
            board_complaint_url="https://www.dpb.gov.in/complaints",
        ),
    },
    {
        "code": "CRM_PORTAL",
        "name": "Crm Portal",
        "domain": "crmportal.local",
        "description": "CRM demo site platform",
        "users": [],
        "contacts": dict(
            dpo_name="Vikram Shah", dpo_email="dpo@crmportal.local", dpo_phone="+91-80-4000-1004",
            withdraw_url="https://crmportal.local/privacy/withdraw-consent",
            rights_url="https://crmportal.local/privacy/your-rights",
            grievance_url="https://crmportal.local/privacy/grievance",
            board_complaint_url="https://www.dpb.gov.in/complaints",
        ),
    },
    {
        "code": "CAREER_HUB",
        "name": "Career Hub",
        "domain": "careerhub.local",
        "description": "Career Hub demo job-portal platform",
        "users": [],
        "contacts": dict(
            dpo_name="Sana Iyer", dpo_email="dpo@careerhub.local", dpo_phone="+91-80-4000-1005",
            withdraw_url="https://careerhub.local/privacy/withdraw-consent",
            rights_url="https://careerhub.local/privacy/your-rights",
            grievance_url="https://careerhub.local/privacy/grievance",
            board_complaint_url="https://www.dpb.gov.in/complaints",
        ),
    },
]


def _apply_contact_defaults(org: Organization, contacts: dict) -> bool:
    """Fill any blank Rule 3 contact/link field from `contacts`. Never
    overwrites a value that is already set, so this is safe to re-run
    against a database an operator has started customising."""
    changed = False
    for field, value in contacts.items():
        if not getattr(org, field):
            setattr(org, field, value)
            changed = True
    return changed


for tenant in DEMO_TENANTS:
    code = tenant["code"]
    org = db.query(Organization).filter(Organization.code == code).first()
    if not org:
        org = Organization(
            name=tenant["name"], code=code, domain=tenant["domain"],
            description=tenant["description"],
            is_active=True,
        )
        db.add(org)
        db.flush()

        for username, full_name, email, role in tenant["users"]:
            user = OrganizationUser(
                organization_id=org.id,
                username=username,
                full_name=full_name,
                email=email,
                email_search=hmac_digest(email),
                password_hash=hash_password("Codex@1234"),
                role=role,
                is_active=True,
            )
            db.add(user)
            print(f"  Created user: {username} ({role})")
        print(f"Created org: {code}")
    else:
        print(f"Org {code} already exists")

    if _apply_contact_defaults(org, tenant["contacts"]):
        print(f"  Populated DPO/rights/grievance contact defaults for {code}")

db.commit()
db.close()
print("Done!")
