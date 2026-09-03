"""Test the full login flow step by step."""
import traceback
try:
    from app.core.database import SessionLocal
    from app.models.entities import User
    from app.core.security import verify_password, create_access_token, create_refresh_token

    db = SessionLocal()
    user = db.query(User).filter(User.username == "admin").first()
    print(f"1. User loaded: {user.username}")
    print(f"   email (decrypted): {user.email}")
    print(f"   email_search: {user.email_search}")
    print(f"   role: {user.role.name if user.role else 'None'}")
    print(f"   role permissions: {user.role.permissions if user.role else []}")

    print(f"2. verify_password OK: {verify_password('Admin@1234', user.password_hash)}")

    token = create_access_token(user.id, user.username, user.role.name if user.role else "")
    print(f"3. access_token created: {token[:30]}...")

    refresh = create_refresh_token(user.id)
    print(f"4. refresh_token created: {refresh[:30]}...")

    print("\nAll OK - login should work")
    db.close()
except Exception as e:
    traceback.print_exc()
