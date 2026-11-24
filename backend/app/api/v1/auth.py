# File: backend/app/api/v1/auth.py
from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.exceptions import UnauthorizedException
from app.core.security import create_access_token, verify_password
from app.db.models import AuditLog, User
from app.db.session import get_db
from app.schemas.auth import LoginRequest, TokenResponse
from app.schemas.user import UserRead

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Authenticate user and issue JWT access token",
)
def login_user(
    payload: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> TokenResponse:
    identifier = (payload.email or payload.username or "").strip()
    if not identifier or not payload.password:
        raise UnauthorizedException(
            detail="Email/username and password are required",
            error_code="AUTH_INVALID_CREDENTIALS",
        )

    stmt = select(User).where(
        or_(
            func.lower(User.email) == identifier.lower(),
            func.lower(User.username) == identifier.lower(),
        )
    )
    user = db.scalars(stmt).first()

    if user is None or not verify_password(payload.password, user.hashed_password):
        raise UnauthorizedException(
            detail="Invalid email/username or password",
            error_code="AUTH_INVALID_CREDENTIALS",
        )

    if not user.is_active:
        raise UnauthorizedException(
            detail="User account is inactive",
            error_code="AUTH_USER_INACTIVE",
        )

    access_token = create_access_token(
        subject=user.id,
        extra_claims={
            "email": user.email,
            "username": user.username,
            "role": user.role,
            "department": user.department,
        },
    )

    client_ip = request.client.host if request.client else None
    db.add(
        AuditLog(
            user_id=user.id,
            action="LOGIN",
            resource_type="user",
            resource_id=str(user.id),
            details_json={
                "username": user.username,
                "role": user.role,
                "department": user.department,
            },
            ip_address=client_ip,
        )
    )
    db.commit()
    db.refresh(user)

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=UserRead.model_validate(user),
    )


@router.get(
    "/me",
    response_model=UserRead,
    summary="Get current authenticated user profile, role, and department",
)
def get_me(current_user: User = Depends(get_current_user)) -> UserRead:
    return UserRead.model_validate(current_user)
