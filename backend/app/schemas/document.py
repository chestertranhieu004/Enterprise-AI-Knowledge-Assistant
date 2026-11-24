# File: backend/app/schemas/document.py
import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field
from app.schemas.common import (
    AccessLevel,
    DepartmentCode,
    DocumentStatus,
    PermissionType,
    UserRole,
)


class DocumentPermissionEntry(BaseModel):
    id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    department: DepartmentCode | None = None
    role: UserRole | None = None
    permission_type: PermissionType = PermissionType.READ

    model_config = ConfigDict(from_attributes=True)


class DocumentRead(BaseModel):
    id: uuid.UUID
    title: str
    file_name: str
    file_type: str
    file_size_bytes: int
    department: DepartmentCode
    access_level: AccessLevel
    allowed_roles: list[UserRole]
    uploaded_by: uuid.UUID
    status: DocumentStatus
    error_message: str | None = None
    chunk_count: int
    permissions: list[DocumentPermissionEntry] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DocumentACLUpdateRequest(BaseModel):
    department: DepartmentCode | None = None
    access_level: AccessLevel | None = None
    allowed_roles: list[UserRole] | None = None
    permissions: list[DocumentPermissionEntry] | None = None


class DocumentDeleteResponse(BaseModel):
    deleted: bool
    document_id: uuid.UUID
