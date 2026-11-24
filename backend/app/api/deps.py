# File: backend/app/api/deps.py
import uuid
from collections.abc import Callable

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import ForbiddenException, UnauthorizedException
from app.core.security import decode_access_token
from app.db.models import User
from app.db.session import get_db
from app.schemas.common import UserRole
from app.schemas.user import UserContext

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_STR}/auth/login",
    auto_error=False,
)


def get_current_user(
    db: Session = Depends(get_db),
    token: str | None = Depends(oauth2_scheme),
) -> User:
    """Validates JWT token, fetches active User from DB, or raises 401 UnauthorizedException."""
    if not token:
        raise UnauthorizedException(
            detail="Missing authentication token",
            error_code="AUTH_TOKEN_MISSING",
        )

    raw_token = token.removeprefix("Bearer ").strip()
    try:
        payload = decode_access_token(raw_token)
    except ValueError as exc:
        raise UnauthorizedException(
            detail=str(exc),
            error_code="AUTH_TOKEN_INVALID",
        ) from exc

    sub = payload.get("sub")
    if not sub:
        raise UnauthorizedException(
            detail="Token payload missing subject claim",
            error_code="AUTH_TOKEN_INVALID",
        )

    try:
        user_id = uuid.UUID(str(sub))
    except ValueError as exc:
        raise UnauthorizedException(
            detail="Invalid user ID in token subject",
            error_code="AUTH_TOKEN_INVALID",
        ) from exc

    user = db.scalars(select(User).where(User.id == user_id)).first()
    if user is None:
        raise UnauthorizedException(
            detail="Authenticated user no longer exists",
            error_code="AUTH_USER_NOT_FOUND",
        )
    if not user.is_active:
        raise UnauthorizedException(
            detail="User account is inactive",
            error_code="AUTH_USER_INACTIVE",
        )

    return user


def get_current_user_context(
    current_user: User = Depends(get_current_user),
) -> UserContext:
    """Converts ORM User into Pydantic UserContext for RAGService and RBAC checks."""
    return UserContext.model_validate(current_user)


def require_roles(*allowed_roles: UserRole) -> Callable[[User], User]:
    """Dependency factory enforcing that current_user.role is in allowed_roles (else HTTP 403)."""
    allowed_set = {
        role.value if isinstance(role, UserRole) else str(role)
        for role in allowed_roles
    }

    def _role_checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_set:
            raise ForbiddenException(
                detail=f"Role '{current_user.role}' is not authorized for this endpoint",
                error_code="RBAC_ROLE_FORBIDDEN",
            )
        return current_user

    return _role_checker
