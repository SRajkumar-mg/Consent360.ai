"""Test that login works - check the actual error."""
import traceback
try:
    from app.core.database import SessionLocal
    from app.models.entities import User
    from app.core.security import verify_password
    from app.core.encryption import hmac_digest

    db = SessionLocal()
    user = db.query(User).filter(User.username == "admin").first()
    if user:
        print(f"User found: {user.username}, email={user.email}, email_search={user.email_search}")
        print(f"Password hash: {user.password_hash[:20]}...")
        print(f"Verify: {verify_password('Admin@1234', user.password_hash)}")
    else:
        print("User not found!")
    db.close()
except Exception as e:
    traceback.print_exc()
