from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.database import get_db
from app.core.rbac import PERM_USER_MANAGE
from app.models.entities import Role, User
from app.schemas.schemas import RoleOut

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/roles", response_model=list[RoleOut])
def list_roles(db: Session = Depends(get_db), _: User = Depends(require_permission(PERM_USER_MANAGE))):
    roles = db.query(Role).order_by(Role.id).all()
    return [RoleOut.model_validate(r) for r in roles]
