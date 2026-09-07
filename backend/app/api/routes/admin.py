from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Optional

from app.api.deps import require_permission
from app.core.database import get_db
from app.core.rbac import ALL_PERMISSIONS, PERM_USER_MANAGE
from app.models.entities import Role, User
from app.schemas.schemas import RoleOut
from app.services.audit import log_audit

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/roles", response_model=list[RoleOut])
def list_roles(db: Session = Depends(get_db), _: User = Depends(require_permission(PERM_USER_MANAGE))):
    roles = db.query(Role).order_by(Role.id).all()
    return [RoleOut.model_validate(r) for r in roles]


def _reject_unknown_permissions(permissions: list[str]) -> None:
    invalid = [p for p in permissions if p not in ALL_PERMISSIONS and p != "*"]
    if invalid:
        raise HTTPException(status_code=422, detail=f"Unknown permission(s): {invalid}")


class RoleCreate(BaseModel):
    name: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    description: str = ""
    permissions: list[str] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    description: Optional[str] = None
    permissions: Optional[list[str]] = None


@router.post("/roles", response_model=RoleOut, status_code=201)
def create_role(payload: RoleCreate, db: Session = Depends(get_db),
                 current_user: User = Depends(require_permission(PERM_USER_MANAGE))):
    """R3-02/R-01/N-03: custom role management. Built-in roles (admin, dpo,
    auditor, operator, viewer, consent_manager, the org-scoped *_admin
    ones) are centrally defined in rbac.ROLE_PERMISSIONS and kept in sync
    by rbac.sync_roles() at every startup - this endpoint is for
    organization- or team-specific roles beyond that fixed set."""
    if db.query(Role).filter(Role.name == payload.name).first():
        raise HTTPException(status_code=409, detail="A role with this name already exists")
    _reject_unknown_permissions(payload.permissions)
    role = Role(name=payload.name, description=payload.description, permissions=payload.permissions, is_system=False)
    db.add(role)
    db.flush()
    log_audit(db, "ROLE_CHANGED", actor_username=current_user.username,
              actor_role=current_user.role.name if current_user.role else "",
              actor_type="USER", actor_id=str(current_user.id), source_app="UI",
              reason=f"Role '{role.name}' created",
              metadata={"role_id": role.id, "permissions": role.permissions})
    db.commit()
    db.refresh(role)
    return RoleOut.model_validate(role)


@router.put("/roles/{role_id}", response_model=RoleOut)
def update_role(role_id: int, payload: RoleUpdate, db: Session = Depends(get_db),
                 current_user: User = Depends(require_permission(PERM_USER_MANAGE))):
    role = db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    if role.is_system:
        raise HTTPException(status_code=403, detail="System roles are managed in rbac.py, not editable here")
    changes: dict = {}
    if payload.description is not None:
        role.description = payload.description
        changes["description"] = payload.description
    if payload.permissions is not None:
        _reject_unknown_permissions(payload.permissions)
        role.permissions = payload.permissions
        changes["permissions"] = payload.permissions
    log_audit(db, "ROLE_CHANGED", actor_username=current_user.username,
              actor_role=current_user.role.name if current_user.role else "",
              actor_type="USER", actor_id=str(current_user.id), source_app="UI",
              reason=f"Role '{role.name}' updated", metadata=changes)
    db.commit()
    db.refresh(role)
    return RoleOut.model_validate(role)


@router.delete("/roles/{role_id}")
def delete_role(role_id: int, db: Session = Depends(get_db),
                 current_user: User = Depends(require_permission(PERM_USER_MANAGE))):
    role = db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    if role.is_system:
        raise HTTPException(status_code=403, detail="System roles are managed in rbac.py, not deletable here")
    if db.query(User).filter(User.role_id == role_id).first():
        raise HTTPException(status_code=409, detail="Cannot delete a role that is still assigned to a staff user")
    name = role.name
    db.delete(role)
    log_audit(db, "ROLE_CHANGED", actor_username=current_user.username,
              actor_role=current_user.role.name if current_user.role else "",
              actor_type="USER", actor_id=str(current_user.id), source_app="UI",
              reason=f"Role '{name}' deleted")
    db.commit()
    return {"deleted": True, "role_id": role_id}


@router.get("/access-review")
def access_review(db: Session = Depends(get_db), current_user: User = Depends(require_permission(PERM_USER_MANAGE))):
    """R3-02/R-04: periodic access review report - every staff user with
    their role, permission set, active/inactive status and last login,
    plus a per-role headcount, for a DPO/security reviewer to audit who
    can do what without hand-joining /auth/users and /admin/roles."""
    users = db.query(User).order_by(User.username).all()
    roles = db.query(Role).order_by(Role.name).all()
    log_audit(db, "USER_LIST_VIEWED", actor_username=current_user.username,
              actor_role=current_user.role.name if current_user.role else "",
              actor_type="USER", actor_id=str(current_user.id), source_app="UI",
              reason="Access review report generated", metadata={"user_count": len(users)})
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "users": [
            {
                "username": u.username,
                "full_name": u.full_name,
                "role": u.role.name if u.role else None,
                "permissions": u.role.permissions if u.role else [],
                "is_active": u.is_active,
                "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
            for u in users
        ],
        "roles": [
            {
                "name": r.name,
                "description": r.description,
                "permissions": r.permissions,
                "is_system": r.is_system,
                "user_count": sum(1 for u in users if u.role_id == r.id),
            }
            for r in roles
        ],
    }
