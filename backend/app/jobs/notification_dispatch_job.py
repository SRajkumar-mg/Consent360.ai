from sqlalchemy.orm import Session

from app.services.notifications import dispatch_pending


def run(db: Session) -> dict:
    return dispatch_pending(db, limit=500)
