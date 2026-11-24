# File: backend/app/schemas/chat.py
import uuid
from datetime import datetime
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, model_validator
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

    @model_validator(mode="before")
    @classmethod
    def _extract_json_columns(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "citations" not in data and "citations_json" in data:
                data = dict(data)
                data["citations"] = data.get("citations_json") or []
            if "suggested_queries" not in data and "suggested_queries_json" in data:
                data = dict(data)
                data["suggested_queries"] = data.get("suggested_queries_json") or []
            return data
        return data


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
