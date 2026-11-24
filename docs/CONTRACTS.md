# Python Module & Interface Contracts (`docs/CONTRACTS.md`)

> **Project**: Enterprise Knowledge Assistant  
> **Phase**: Phase 1 — Cross-Module Interface Specification  
> **Owner**: `@architect`  
> **Consumers**: `@backend_core`, `@ingestion`, `@rag_engineer`, `@frontend`, `@qa_security`

---

## 1. Purpose & Contract Invariants

This document specifies the **exact module paths, class names, method signatures, enum values, constants, and Pydantic v2 models** shared across `@backend_core`, `@ingestion`, and `@rag_engineer`.

Adhering strictly to these signatures ensures that all parallel workstreams integrate cleanly on first run without `ImportError`, `AttributeError`, or schema validation mismatches.

---

## 2. Global Constants & Enumerations (`backend/app/schemas/common.py`)

Owned by **`@backend_core`** and imported by **`@ingestion`** and **`@rag_engineer`**.

```python
# File: backend/app/schemas/common.py
from enum import StrEnum

# Mandatory Scenario 1 Exact Abstention Message
ABSTENTION_MESSAGE_VI: str = (
    "Không tìm thấy thông tin phù hợp trong các tài liệu bạn có quyền truy cập."
)

DEFAULT_SIMILARITY_THRESHOLD: float = 0.65
DEFAULT_TOP_K: int = 5
EMBEDDING_DIM: int = 768
MAX_UPLOAD_SIZE_BYTES: int = 50 * 1024 * 1024  # 50 MB


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
```

---

## 3. Configuration Contract (`backend/app/core/config.py`)

Owned by **`@backend_core`** and imported via `from app.core.config import settings`.

```python
# File: backend/app/core/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "Enterprise Knowledge Assistant"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: str = "development"

    # Database
    DATABASE_URL: str = "postgresql+psycopg://eka_user:eka_password@localhost:5432/eka_db"

    # JWT Security
    JWT_SECRET_KEY: str = "super-secret-enterprise-jwt-key-change-in-prod"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24 hours

    # Providers: "mock" | "openai" | "gemini"
    LLM_PROVIDER: str = "mock"
    EMBEDDING_PROVIDER: str = "mock"

    # OpenAI Configuration
    OPENAI_API_KEY: str | None = None
    OPENAI_LLM_MODEL: str = "gpt-4o-mini"
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"

    # Gemini Configuration
    GEMINI_API_KEY: str | None = None
    GEMINI_LLM_MODEL: str = "gemini-2.0-flash"
    GEMINI_EMBEDDING_MODEL: str = "models/text-embedding-004"

    # Vector & RAG Parameters
    EMBEDDING_DIM: int = 768
    SIMILARITY_THRESHOLD: float = 0.65
    DEFAULT_TOP_K: int = 5
    QUOTE_OVERLAP_THRESHOLD: float = 0.50

    # Ingestion & Storage Parameters
    UPLOAD_DIR: str = "./data/uploads"
    MAX_UPLOAD_SIZE_MB: int = 50
    CHUNK_SIZE: int = 512
    CHUNK_OVERLAP: int = 64
    INGESTION_MODE: str = "sync"  # "sync" | "background"

    # Auto-seed database on startup
    SEED_ON_STARTUP: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
```

---

## 4. Database Session & ORM Models Contract (`backend/app/db/`)

Owned by **`@backend_core`**. We use **synchronous SQLAlchemy 2.0 (`Session`)** with `psycopg` (`postgresql+psycopg://`) or `psycopg2` so that FastAPI endpoints, background tasks, LlamaIndex utilities, and Pytest fixtures operate cleanly without event-loop conflicts. (Async endpoints can inject `db: Session = Depends(get_db)` seamlessly).

### 4.1 `backend/app/db/session.py`

```python
# File: backend/app/db/session.py
from collections.abc import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker, DeclarativeBase
from app.core.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

### 4.2 `backend/app/db/models.py`

Must define the 8 SQLAlchemy 2.0 ORM classes matching `docs/ERD.md`:

```python
# File: backend/app/db/models.py
import uuid
from datetime import datetime
from typing import Any
from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, BigInteger,
    String, Text, UniqueConstraint, Index
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector
from app.db.session import Base


class Department(Base):
    __tablename__ = "departments"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="employee")
    department: Mapped[str] = mapped_column(String(50), ForeignKey("departments.code"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    file_name: Mapped[str] = mapped_column(String(500), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    file_type: Mapped[str] = mapped_column(String(32), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    department: Mapped[str] = mapped_column(String(50), ForeignKey("departments.code"), nullable=False)
    access_level: Mapped[str] = mapped_column(String(32), nullable=False, default="department")
    allowed_roles: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=lambda: ["admin", "manager", "employee"])
    uploaded_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="processing")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedding_provider: Mapped[str] = mapped_column(String(32), nullable=False, default="mock")
    embedding_model: Mapped[str] = mapped_column(String(128), nullable=False, default="mock-768")
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    permissions: Mapped[list["DocumentPermission"]] = relationship(
        "DocumentPermission", back_populates="document", cascade="all, delete-orphan"
    )
    chunks: Mapped[list["DocumentChunk"]] = relationship(
        "DocumentChunk", back_populates="document", cascade="all, delete-orphan"
    )


class DocumentPermission(Base):
    __tablename__ = "document_permissions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    department: Mapped[str | None] = mapped_column(String(50), ForeignKey("departments.code", ondelete="CASCADE"), nullable=True)
    role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    permission_type: Mapped[str] = mapped_column(String(32), nullable=False, default="read")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    document: Mapped["Document"] = relationship("Document", back_populates="permissions")


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_or_sheet: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    embedding: Mapped[list[float]] = mapped_column(Vector(768), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    document: Mapped["Document"] = relationship("Document", back_populates="chunks")


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False, default="Cuộc hội thoại mới")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    messages: Mapped[list["ChatMessage"]] = relationship(
        "ChatMessage", back_populates="session", cascade="all, delete-orphan", order_by="ChatMessage.created_at"
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    found: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    citations_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    suggested_queries_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    session: Mapped["ChatSession"] = relationship("ChatSession", back_populates="messages")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
```

---

## 5. Shared Pydantic v2 Schemas Contract (`backend/app/schemas/`)

Owned by **`@backend_core`** and imported across the application.

### 5.1 Auth & User Schemas (`backend/app/schemas/auth.py` & `user.py`)

```python
# File: backend/app/schemas/user.py
import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict, EmailStr
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
    username: str
    password: str
    full_name: str
    role: UserRole = UserRole.EMPLOYEE
    department: DepartmentCode = DepartmentCode.GENERAL
    is_active: bool = True


class UserPermissionUpdateRequest(BaseModel):
    role: UserRole | None = None
    department: DepartmentCode | None = None
    is_active: bool | None = None
```

```python
# File: backend/app/schemas/auth.py
from pydantic import BaseModel
from app.schemas.user import UserRead


class LoginRequest(BaseModel):
    email: str | None = None
    username: str | None = None
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 86400
    user: UserRead
```

### 5.2 Document & Permission Schemas (`backend/app/schemas/document.py`)

```python
# File: backend/app/schemas/document.py
import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field
from app.schemas.common import AccessLevel, DepartmentCode, DocumentStatus, PermissionType, UserRole


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
```

### 5.3 Chat & Citation Schemas (`backend/app/schemas/chat.py`)

```python
# File: backend/app/schemas/chat.py
import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field
from app.schemas.common import VerificationStatus


class CitationItem(BaseModel):
    document_id: uuid.UUID
    file_name: str
    page_or_sheet: str
    chunk_id: uuid.UUID
    quote: str
    score: float
    verification_status: VerificationStatus = VerificationStatus.VERIFIED


class ChatQueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4000)
    session_id: uuid.UUID | None = None
    top_k: int = Field(default=5, ge=1, le=20)


class ChatQueryResponse(BaseModel):
    session_id: uuid.UUID
    message_id: uuid.UUID
    query: str
    answer: str
    found: bool
    citations: list[CitationItem] = Field(default_factory=list)
    suggested_queries: list[str] = Field(default_factory=list)
    latency_ms: int


class ChatMessageRead(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    role: str
    content: str
    found: bool | None = None
    citations: list[CitationItem] = Field(default_factory=list)
    suggested_queries: list[str] = Field(default_factory=list)
    latency_ms: int | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ChatSessionSummary(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    title: str
    message_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ChatSessionDetail(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    title: str
    messages: list[ChatMessageRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
```

---

## 6. Ingestion Module Contract (`backend/app/ingestion/`)

Owned by **`@ingestion`** and called by **`@backend_core`** (`backend/app/api/v1/documents.py`).

### 6.1 Data Structures & Parsers (`backend/app/ingestion/chunker.py` & `parsers/`)

```python
# File: backend/app/ingestion/chunker.py
from typing import Any
from pydantic import BaseModel, Field


class ParsedSection(BaseModel):
    """Output of format-specific parsers (PDF page, DOCX section, XLSX sheet row-block)."""
    text: str
    page_or_sheet: str  # e.g. "Page 1", "Section 2: Nghỉ phép", "Sheet: Budget_2026 (Rows 1-20)"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChunkData(BaseModel):
    """Output of LlamaIndex SentenceSplitter node chunking ready for embedding."""
    chunk_index: int
    page_or_sheet: str
    content: str
    token_count: int
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class LlamaIndexChunker:
    """Uses llama_index.core.schema.Document and SentenceSplitter(chunk_size, chunk_overlap)."""

    def __init__(self, chunk_size: int = 512, chunk_overlap: int = 64) -> None: ...

    def split_sections(self, sections: list[ParsedSection], file_name: str) -> list[ChunkData]:
        """Splits ParsedSections into ChunkData objects preserving page_or_sheet coordinates."""
        ...
```

Format-specific parser functions:
- `backend/app/ingestion/parsers/pdf_parser.py`:
  - `def parse_pdf(file_path: str | Path) -> list[ParsedSection]:`
- `backend/app/ingestion/parsers/docx_parser.py`:
  - `def parse_docx(file_path: str | Path) -> list[ParsedSection]:`
- `backend/app/ingestion/parsers/xlsx_parser.py`:
  - `def parse_xlsx(file_path: str | Path, rows_per_section: int = 25) -> list[ParsedSection]:` (Must prepend column headers to every row group so tabular context is preserved).

### 6.2 `IngestionService` (`backend/app/ingestion/service.py`)

```python
# File: backend/app/ingestion/service.py
import uuid
from pathlib import Path
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.rag.providers.base import BaseEmbeddingProvider
from app.schemas.common import DocumentStatus


class IngestionResult(BaseModel):
    document_id: uuid.UUID
    status: DocumentStatus
    chunk_count: int
    error_message: str | None = None


class IngestionService:
    def __init__(
        self,
        embedding_provider: BaseEmbeddingProvider | None = None,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
    ) -> None:
        """
        If embedding_provider is None, resolves default via get_embedding_provider()
        from app.rag.providers.factory.
        """
        ...

    def ingest_document(
        self,
        db: Session,
        document_id: uuid.UUID,
        file_path: str | Path | None = None,
    ) -> IngestionResult:
        """
        1. Loads Document row by document_id from db.
        2. Dispatches file_path (or doc.file_path) to parse_pdf / parse_docx / parse_xlsx / text parser.
        3. Splits sections into ChunkData via LlamaIndexChunker.
        4. Generates 768-dim embeddings via self.embedding_provider.embed_texts([c.content for c in chunks]).
        5. Deletes any old chunks for document_id, bulk inserts DocumentChunk rows.
        6. Updates Document.status = 'ready', Document.chunk_count = len(chunks), and commits.
        7. On exception, sets Document.status = 'failed', Document.error_message = str(exc), commits, and returns IngestionResult.
        """
        ...
```

---

## 7. RAG, Providers, Retriever & Verifier Contract (`backend/app/rag/`)

Owned by **`@rag_engineer`**, consumed by **`@backend_core`** (`backend/app/api/v1/chat.py`) and **`@ingestion`** (`get_embedding_provider`).

### 7.1 Provider Base Interfaces (`backend/app/rag/providers/base.py`)

```python
# File: backend/app/rag/providers/base.py
import uuid
from abc import ABC, abstractmethod
from typing import Any
from pydantic import BaseModel, Field


class RetrievedChunk(BaseModel):
    """Authorized chunk returned by PreRetrievalRBACRetriever (Scenario 3 compliant)."""
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    file_name: str
    title: str
    department: str
    access_level: str
    page_or_sheet: str
    chunk_index: int
    content: str
    score: float
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class RawCitationCandidate(BaseModel):
    """Candidate citation proposed by the LLM before post-generation verification."""
    chunk_id: uuid.UUID | str
    quote: str = ""


class RawLLMGeneration(BaseModel):
    """Structured output produced by BaseLLMProvider.generate_grounded_answer."""
    answer: str
    abstained: bool = False
    citations: list[RawCitationCandidate] = Field(default_factory=list)
    suggested_queries: list[str] = Field(default_factory=list)


class BaseEmbeddingProvider(ABC):
    provider_name: str = "base"
    model_name: str = "base-768"
    dimension: int = 768

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """Returns a 768-dimensional L2-normalized float vector for a query string."""
        ...

    @abstractmethod
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Returns a list of 768-dimensional L2-normalized float vectors for batch document chunks."""
        ...


class BaseLLMProvider(ABC):
    provider_name: str = "base"
    model_name: str = "base-llm"

    @abstractmethod
    def generate_grounded_answer(
        self,
        query: str,
        chunks: list[RetrievedChunk],
    ) -> RawLLMGeneration:
        """
        Generates a Vietnamese (or query-language-matched) grounded answer using ONLY the provided
        authorized chunks. Every claim must cite [chunk_id] and provide a verbatim quote.
        """
        ...

    @abstractmethod
    def suggest_rephrased_queries(
        self,
        query: str,
    ) -> list[str]:
        """
        Returns 2-3 suggested query rephrasings in Vietnamese for Scenario 1 (when found=False).
        Must NEVER reference or leak any unauthorized document titles or content.
        """
        ...
```

### 7.2 Provider Factory (`backend/app/rag/providers/factory.py`)

```python
# File: backend/app/rag/providers/factory.py
from app.rag.providers.base import BaseEmbeddingProvider, BaseLLMProvider


def get_embedding_provider(provider_override: str | None = None) -> BaseEmbeddingProvider:
    """
    Returns MockEmbeddingProvider, OpenAIEmbeddingProvider, or GeminiEmbeddingProvider
    based on settings.EMBEDDING_PROVIDER (or provider_override).
    """
    ...


def get_llm_provider(provider_override: str | None = None) -> BaseLLMProvider:
    """
    Returns MockLLMProvider, OpenAILLMProvider, or GeminiLLMProvider
    based on settings.LLM_PROVIDER (or provider_override).
    """
    ...
```

### 7.3 Pre-Retrieval RBAC Retriever (`backend/app/rag/retriever.py`)

Enforces **Scenario 3** inside PostgreSQL + `pgvector` SQL `WHERE` clause before vector distance sorting.

```python
# File: backend/app/rag/retriever.py
from sqlalchemy.orm import Session
from app.rag.providers.base import BaseEmbeddingProvider, RetrievedChunk
from app.schemas.user import UserContext


class PreRetrievalRBACRetriever:
    def __init__(
        self,
        embedding_provider: BaseEmbeddingProvider | None = None,
        similarity_threshold: float | None = None,
    ) -> None: ...

    def retrieve_authorized_chunks(
        self,
        db: Session,
        user: UserContext,
        query: str,
        top_k: int = 5,
        similarity_threshold: float | None = None,
    ) -> list[RetrievedChunk]:
        """
        1. Computes 768-dim query embedding via self.embedding_provider.embed_query(query).
        2. Executes the single parameterized PostgreSQL query defined in ADR-002 that filters
           documents & document_permissions by (user.id, user.role, user.department) inside WHERE
           BEFORE computing cosine similarity and applying >= similarity_threshold and LIMIT top_k.
        3. Wraps results into LlamaIndex NodeWithScore internally and returns list[RetrievedChunk].
        4. Unauthorized documents/chunks NEVER enter Python memory.
        """
        ...
```

### 7.4 Post-Generation Citation Verifier (`backend/app/rag/verifier.py`)

Enforces **Scenario 2** (Citation Verification) and falls back to **Scenario 1** if all citations are invalid.

```python
# File: backend/app/rag/verifier.py
import uuid
from pydantic import BaseModel, Field
from app.rag.providers.base import RawLLMGeneration, RetrievedChunk
from app.schemas.chat import CitationItem


class VerificationResult(BaseModel):
    found: bool
    answer: str
    citations: list[CitationItem] = Field(default_factory=list)
    rejected_chunk_ids: list[str] = Field(default_factory=list)


class CitationVerifier:
    def __init__(self, overlap_threshold: float = 0.50) -> None: ...

    def verify(
        self,
        generation: RawLLMGeneration,
        authorized_chunks: list[RetrievedChunk],
    ) -> VerificationResult:
        """
        1. Builds whitelist map: {str(chunk.chunk_id): chunk for chunk in authorized_chunks}.
        2. Collects candidate citations from generation.citations AND regex matches of
           `\[([0-9a-fA-F-]{36})\]` inside generation.answer.
        3. For each candidate citation:
           a. Checks chunk_id in whitelist map. If not in whitelist -> reject.
           b. Verifies quote lexical/token containment >= overlap_threshold against authoritative
              chunk.content (or extracts verbatim matching span from chunk.content if answer claim
              overlaps >= overlap_threshold with chunk.content). If ungrounded -> reject and strip
              from answer.
           c. Builds CitationItem using authoritative chunk.document_id, chunk.file_name,
              chunk.page_or_sheet, chunk.chunk_id, verified quote, chunk.score,
              verification_status=VerificationStatus.VERIFIED.
        4. If generation.abstained is True OR len(verified_citations) == 0:
           Returns VerificationResult(
               found=False,
               answer=ABSTENTION_MESSAGE_VI,
               citations=[],
               rejected_chunk_ids=rejected_ids
           )
        5. Returns VerificationResult(found=True, answer=cleaned_answer, citations=verified_citations).
        """
        ...
```

### 7.5 High-Level RAG Orchestrator (`backend/app/rag/service.py`)

```python
# File: backend/app/rag/service.py
from sqlalchemy.orm import Session
from app.rag.providers.base import BaseEmbeddingProvider, BaseLLMProvider
from app.schemas.chat import ChatQueryRequest, ChatQueryResponse
from app.schemas.user import UserContext


class RAGService:
    def __init__(
        self,
        embedding_provider: BaseEmbeddingProvider | None = None,
        llm_provider: BaseLLMProvider | None = None,
        similarity_threshold: float | None = None,
    ) -> None: ...

    def query(
        self,
        db: Session,
        user: UserContext,
        request: ChatQueryRequest,
    ) -> ChatQueryResponse:
        """
        End-to-end RAG execution:
        1. Resolves or creates ChatSession for user (if request.session_id is None, creates new session).
        2. Saves user's ChatMessage(role="user", content=request.query).
        3. Calls PreRetrievalRBACRetriever.retrieve_authorized_chunks(db, user, request.query, request.top_k).
        4. Gate 1 (Scenario 1 & 3): If len(authorized_chunks) == 0:
           - Calls llm_provider.suggest_rephrased_queries(request.query) (returns 2-3 suggestions).
           - Sets found = False, answer = ABSTENTION_MESSAGE_VI, citations = [].
        5. Gate 2: Calls llm_provider.generate_grounded_answer(request.query, authorized_chunks).
        6. Gate 3 (Scenario 2): Calls CitationVerifier.verify(generation, authorized_chunks).
           - If verification.found is False -> ensures answer = ABSTENTION_MESSAGE_VI, citations = [],
             and 2-3 suggested_queries are populated.
        7. Saves assistant's ChatMessage(role="assistant", content=answer, found=found,
           citations_json=..., suggested_queries_json=..., latency_ms=...).
        8. Writes AuditLog(user_id=user.id, action="CHAT_QUERY", resource_type="chat_session", ...).
        9. Returns ChatQueryResponse.
        """
        ...
```

---

## 8. FastAPI Dependency Injection Contract (`backend/app/api/deps.py`)

Owned by **`@backend_core`**.

```python
# File: backend/app/api/deps.py
from collections.abc import Callable
from fastapi import Depends
from sqlalchemy.orm import Session
from app.db.models import User
from app.db.session import get_db
from app.schemas.common import UserRole
from app.schemas.user import UserContext


def get_current_user(
    db: Session = Depends(get_db),
    token: str = Depends(...),  # OAuth2PasswordBearer / HTTPBearer
) -> User:
    """Validates JWT token, fetches active User from DB, or raises HTTPException(401)."""
    ...


def get_current_user_context(
    current_user: User = Depends(get_current_user),
) -> UserContext:
    """Converts ORM User into Pydantic UserContext for RAGService and RBAC checks."""
    return UserContext.model_validate(current_user)


def require_roles(*allowed_roles: UserRole) -> Callable[[User], User]:
    """Dependency factory enforcing that current_user.role is in allowed_roles (else HTTP 403)."""
    ...
```
