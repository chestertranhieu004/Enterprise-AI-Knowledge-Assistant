# File: backend/app/schemas/user.py
import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from app.schemas.common import DepartmentCode, UserRole


class UserContext(BaseModel):
    """Lightweight authenticated user principal passed to RBAC Retriever & RAGService."""
    id: uuid.UUID
    email: str
    username: str
    full_name: str
    role: UserRole
    department: DepartmentCode
    is_active: bool = True

    model_config = ConfigDict(from_attributes=True)


class UserRead(UserContext):
    created_at: datetime


class UserCreateRequest(BaseModel):
    email: EmailStr
    username: str = Field(..., min_length=3, max_length=100)
    password: str = Field(..., min_length=8)
    full_name: str
    role: UserRole = UserRole.EMPLOYEE
    department: DepartmentCode = DepartmentCode.GENERAL
    is_active: bool = True


class UserPermissionUpdateRequest(BaseModel):
    role: UserRole | None = None
    department: DepartmentCode | None = None
    is_active: bool | None = None
