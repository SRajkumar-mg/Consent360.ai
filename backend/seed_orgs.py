"""Seed organizations and their users."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.core.encryption import hmac_digest
from app.models.entities import Organization, OrganizationUser

db = SessionLocal()

orgs_data = [
    ("JOBHUB", [
        ("jobhub.admin", "JobHub Admin", "admin@jobhub.local", "jobhub_admin"),
        ("jobhub.viewer", "JobHub Viewer", "viewer@jobhub.local", "jobhub_viewer"),
    ]),
    ("CODEX", [
        ("codex.admin", "Codex Admin", "admin@codex.local", "codex_admin"),
        ("codex.viewer", "Codex Viewer", "viewer@codex.local", "codex_viewer"),
    ]),
    ("SKILLLEARN", [
        ("skilllearn.admin", "SkillLearn Admin", "admin@skilllearn.local", "skilllearn_admin"),
        ("skilllearn.viewer", "SkillLearn Viewer", "viewer@skilllearn.local", "skilllearn_viewer"),
    ]),
]

for code, users_data in orgs_data:
    existing = db.query(Organization).filter(Organization.code == code).first()
    if not existing:
        org = Organization(
            name=code.title(), code=code, domain=f"{code.lower()}.local",
            description=f"{code.title()} platform",
            is_active=True,
        )
        db.add(org)
        db.flush()

        for username, full_name, email, role in users_data:
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

db.commit()
db.close()
print("Done!")
