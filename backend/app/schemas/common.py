# File: backend/app/schemas/common.py
from datetime import datetime
from enum import StrEnum
from pydantic import BaseModel

# Mandatory Scenario 1 Exact Abstention Message
ABSTENTION_MESSAGE_VI: str = (
    "Không tìm thấy thông tin phù hợp trong các tài liệu bạn có quyền truy cập."
)

DEFAULT_SIMILARITY_THRESHOLD: float = 0.65
DEFAULT_TOP_K: int = 5
EMBEDDING_DIM: int = 768
MAX_UPLOAD_SIZE_BYTES: int = 50 * 1024 * 1024  # 50 MB
ALLOWED_FILE_EXTENSIONS: set[str] = {"pdf", "docx", "xlsx", "txt", "md"}


class UserRole(StrEnum):
    ADMIN = "admin"
    MANAGER = "manager"
    EMPLOYEE = "employee"
    GUEST = "guest"


class DepartmentCode(StrEnum):
    HR = "HR"
    FINANCE = "Finance"
    LEGAL = "Legal"
    ENGINEERING = "Engineering"
    GENERAL = "General"


class AccessLevel(StrEnum):
    PUBLIC = "public"
    DEPARTMENT = "department"
    RESTRICTED = "restricted"


class DocumentStatus(StrEnum):
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class PermissionType(StrEnum):
    READ = "read"
    MANAGE = "manage"


class VerificationStatus(StrEnum):
    VERIFIED = "verified"
    STRIPPED = "stripped"
    UNVERIFIED = "unverified"


class ErrorResponse(BaseModel):
    detail: str
    error_code: str | None = None


class HealthResponse(BaseModel):
    status: str
    database: str
    pgvector: str
    llm_provider: str
    embedding_provider: str
    timestamp: datetime
