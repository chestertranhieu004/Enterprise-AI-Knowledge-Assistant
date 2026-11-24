# File: backend/app/api/v1/users.py
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.exceptions import ConflictException, NotFoundException
from app.core.security import get_password_hash
from app.db.models import AuditLog, Department, User
from app.db.session import get_db
from app.schemas.common import UserRole
from app.schemas.user import (
    UserCreateRequest,
    UserPermissionUpdateRequest,
    UserRead,
)

router = APIRouter(prefix="/users", tags=["Users"])


@router.get(
    "",
    response_model=list[UserRead],
    summary="List all users (Admin & Manager)",
)
def list_users(
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.MANAGER)),
) -> list[UserRead]:
    users = db.scalars(select(User).order_by(User.created_at.asc())).all()
    return [UserRead.model_validate(u) for u in users]


@router.post(
    "",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user account with role and department (Admin only)",
)
def create_user(
    payload: UserCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
) -> UserRead:
    existing = db.scalars(
        select(User).where(
            or_(
                func.lower(User.email) == payload.email.lower(),
                func.lower(User.username) == payload.username.lower(),
            )
        )
    ).first()
    if existing is not None:
        raise ConflictException(
            detail="A user with this email or username already exists",
            error_code="USER_ALREADY_EXISTS",
        )

    dept = db.scalars(
        select(Department).where(Department.code == payload.department.value)
    ).first()
    if dept is None:
        dept = Department(
            code=payload.department.value,
            name=f"Department {payload.department.value}",
        )
        db.add(dept)
        db.flush()

    new_user = User(
        email=str(payload.email).lower(),
        username=payload.username.strip(),
        hashed_password=get_password_hash(payload.password),
        full_name=payload.full_name.strip(),
        role=payload.role.value,
        department=payload.department.value,
        is_active=payload.is_active,
    )
    db.add(new_user)
    db.flush()

    client_ip = request.client.host if request.client else None
    db.add(
        AuditLog(
            user_id=current_user.id,
            action="USER_CREATE",
            resource_type="user",
            resource_id=str(new_user.id),
            details_json={
                "email": new_user.email,
                "username": new_user.username,
                "role": new_user.role,
                "department": new_user.department,
            },
            ip_address=client_ip,
        )
    )
    db.commit()
    db.refresh(new_user)

    return UserRead.model_validate(new_user)


@router.patch(
    "/{user_id}/permissions",
    response_model=UserRead,
    summary="Update a user's RBAC role, department, or active status (Admin only)",
)
def update_user_permissions(
    user_id: uuid.UUID,
    payload: UserPermissionUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
) -> UserRead:
    target_user = db.scalars(select(User).where(User.id == user_id)).first()
    if target_user is None:
        raise NotFoundException(
            detail="User not found",
            error_code="USER_NOT_FOUND",
        )

    changes: dict[str, object] = {}
    if payload.role is not None:
        changes["old_role"] = target_user.role
        target_user.role = payload.role.value
        changes["new_role"] = target_user.role

    if payload.department is not None:
        dept = db.scalars(
            select(Department).where(Department.code == payload.department.value)
        ).first()
        if dept is None:
            dept = Department(
                code=payload.department.value,
                name=f"Department {payload.department.value}",
            )
            db.add(dept)
            db.flush()
        changes["old_department"] = target_user.department
        target_user.department = payload.department.value
        changes["new_department"] = target_user.department

    if payload.is_active is not None:
        changes["old_is_active"] = target_user.is_active
        target_user.is_active = payload.is_active
        changes["new_is_active"] = target_user.is_active

    target_user.updated_at = datetime.now(timezone.utc)

    client_ip = request.client.host if request.client else None
    db.add(
        AuditLog(
            user_id=current_user.id,
            action="PERMISSION_UPDATE",
            resource_type="user",
            resource_id=str(target_user.id),
            details_json=changes,
            ip_address=client_ip,
        )
    )
    db.commit()
    db.refresh(target_user)

    return UserRead.model_validate(target_user)
