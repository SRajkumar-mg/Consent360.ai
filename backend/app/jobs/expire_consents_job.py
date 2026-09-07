from sqlalchemy.orm import Session

from app.services.consent import expire_consents


def run(db: Session) -> dict:
    count = expire_consents(db, source_app="SYSTEM")
    return {"expired": count}
