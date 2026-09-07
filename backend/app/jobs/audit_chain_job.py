from sqlalchemy.orm import Session

from app.core.audit_chain import verify_chain


def run(db: Session) -> dict:
    return verify_chain(db)
