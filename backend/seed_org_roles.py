"""Seed org-scoped roles and platform users."""

import sys
sys.path.insert(0, r"C:\Users\RajkumarS\Downloads\consenthub\Default Project\backend")

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.rbac import ROLE_PERMISSIONS, ROLE_DESCRIPTIONS
from app.core.security import hash_password
from app.core.encryption import hmac_digest
from app.models.entities import Role, User

DATABASE_URL = "postgresql://postgres:postgres@localhost:5433/consent_platform"
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)


def seed():
    db = SessionLocal()
    try:
        print("=== Seeding org-scoped roles ===\n")

        # 1. Ensure admin and viewer roles exist
        for role_name in ("admin", "viewer"):
            existing = db.query(Role).filter(Role.name == role_name).first()
            if not existing:
                r = Role(
                    name=role_name,
                    description=ROLE_DESCRIPTIONS[role_name],
                    permissions=ROLE_PERMISSIONS[role_name],
                    is_system=True,
                )
                db.add(r)
                print(f"  Created role: {role_name}")
            else:
                existing.permissions = ROLE_PERMISSIONS[role_name]
                existing.description = ROLE_DESCRIPTIONS[role_name]
                print(f"  Updated role: {role_name}")

        # 2. Delete old consent_manager role, reassign users
        old_role = db.query(Role).filter(Role.name == "consent_manager").first()
        if old_role:
            viewer_role = db.query(Role).filter(Role.name == "viewer").first()
            if viewer_role:
                db.execute(
                    text("UPDATE users SET role_id = :new_id WHERE role_id = :old_id"),
                    {"new_id": viewer_role.id, "old_id": old_role.id},
                )
            db.delete(old_role)
            print("  Deleted old consent_manager role (users reassigned to viewer)")
        else:
            print("  No consent_manager role found (nothing to clean up)")

        # 3. Create org-scoped roles
        for role_name in ("jobhub_admin", "codex_admin", "skilllearn_admin"):
            existing = db.query(Role).filter(Role.name == role_name).first()
            if not existing:
                r = Role(
                    name=role_name,
                    description=ROLE_DESCRIPTIONS[role_name],
                    permissions=ROLE_PERMISSIONS[role_name],
                    is_system=True,
                )
                db.add(r)
                print(f"  Created role: {role_name}")
            else:
                existing.permissions = ROLE_PERMISSIONS[role_name]
                existing.description = ROLE_DESCRIPTIONS[role_name]
                print(f"  Updated role: {role_name}")

        db.commit()

        # 4. Create platform users
        print("\n=== Seeding org-scoped platform users ===\n")
        users_data = [
            ("jobhub_admin", "jobhub@123", "JobHub Admin", "jobhub_admin@consent360.com", "jobhub_admin"),
            ("codex_admin", "codex@123", "Codex Admin", "codex_admin@consent360.com", "codex_admin"),
            ("skilllearn_admin", "skilllearn@123", "SkillLearn Admin", "skilllearn_admin@consent360.com", "skilllearn_admin"),
        ]
        for username, password, full_name, email, role_name in users_data:
            existing = db.query(User).filter(User.username == username).first()
            role = db.query(Role).filter(Role.name == role_name).first()
            if not role:
                print(f"  ERROR: role '{role_name}' not found, skipping user '{username}'")
                continue
            if existing:
                existing.password_hash = hash_password(password)
                existing.role_id = role.id
                existing.full_name = full_name
                existing.email = email
                print(f"  Updated user: {username} (role={role_name})")
            else:
                db.add(User(
                    username=username,
                    full_name=full_name,
                    email=email,
                    email_search=hmac_digest(email),
                    password_hash=hash_password(password),
                    role_id=role.id,
                    is_active=True,
                ))
                print(f"  Created user: {username} (role={role_name}, password={password})")

        db.commit()
        print("\nDone!")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
