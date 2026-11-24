# File: backend/app/api/v1/chat.py
import logging
import time
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_user_context
from app.core.exceptions import NotFoundException
from app.db.models import AuditLog, ChatMessage, ChatSession
from app.db.session import get_db
from app.schemas.chat import (
    ChatQueryRequest,
    ChatQueryResponse,
    ChatSessionDetail,
    ChatSessionSummary,
)
from app.schemas.common import ABSTENTION_MESSAGE_VI
from app.schemas.user import UserContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["Chat"])


@router.post(
    "/query",
    response_model=ChatQueryResponse,
    summary="Execute a RBAC-filtered, citation-verified RAG query",
)
def query_knowledge_base(
    payload: ChatQueryRequest,
    db: Session = Depends(get_db),
    user_ctx: UserContext = Depends(get_current_user_context),
) -> ChatQueryResponse:
    # Lazily import RAGService (Phase 3 module owned by @rag_engineer)
    try:
        from app.rag.service import RAGService

        rag_service = RAGService()
        return rag_service.query(db=db, user=user_ctx, request=payload)
    except ImportError:
        logger.warning(
            "app.rag.service.RAGService not available yet (Phase 2); returning Scenario 1 abstention fallback."
        )
        start_ns = time.perf_counter_ns()
        session_obj: ChatSession | None = None
        if payload.session_id is not None:
            session_obj = db.scalars(
                select(ChatSession).where(
                    ChatSession.id == payload.session_id,
                    ChatSession.user_id == user_ctx.id,
                )
            ).first()

        if session_obj is None:
            session_obj = ChatSession(
                user_id=user_ctx.id,
                title=payload.query[:80],
            )
            db.add(session_obj)
            db.flush()

        user_msg = ChatMessage(
            session_id=session_obj.id,
            role="user",
            content=payload.query,
        )
        db.add(user_msg)

        suggested = [
            f"Quy định chung của công ty về: {payload.query[:60]}?",
            f"Hướng dẫn quy trình nội bộ cho phòng {user_ctx.department.value}?",
        ]
        latency_ms = max(1, int((time.perf_counter_ns() - start_ns) / 1_000_000))
        assistant_msg = ChatMessage(
            session_id=session_obj.id,
            role="assistant",
            content=ABSTENTION_MESSAGE_VI,
            found=False,
            citations_json=[],
            suggested_queries_json=suggested,
            latency_ms=latency_ms,
        )
        db.add(assistant_msg)
        db.add(
            AuditLog(
                user_id=user_ctx.id,
                action="CHAT_QUERY",
                resource_type="chat_session",
                resource_id=str(session_obj.id),
                details_json={"found": False, "citation_count": 0},
            )
        )
        db.commit()
        db.refresh(assistant_msg)

        return ChatQueryResponse(
            session_id=session_obj.id,
            message_id=assistant_msg.id,
            query=payload.query,
            answer=ABSTENTION_MESSAGE_VI,
            found=False,
            citations=[],
            suggested_queries=suggested,
            latency_ms=latency_ms,
        )


@router.get(
    "/sessions",
    response_model=list[ChatSessionSummary],
    summary="List chat sessions belonging to the current user",
)
def list_chat_sessions(
    db: Session = Depends(get_db),
    user_ctx: UserContext = Depends(get_current_user_context),
) -> list[ChatSessionSummary]:
    stmt = (
        select(ChatSession)
        .options(selectinload(ChatSession.messages))
        .where(ChatSession.user_id == user_ctx.id)
        .order_by(ChatSession.updated_at.desc())
    )
    sessions = db.scalars(stmt).all()
    return [ChatSessionSummary.model_validate(s) for s in sessions]


@router.get(
    "/sessions/{session_id}",
    response_model=ChatSessionDetail,
    summary="Get full message history and citations for a specific chat session",
)
def get_chat_session_detail(
    session_id: uuid.UUID,
    db: Session = Depends(get_db),
    user_ctx: UserContext = Depends(get_current_user_context),
) -> ChatSessionDetail:
    stmt = (
        select(ChatSession)
        .options(selectinload(ChatSession.messages))
        .where(
            ChatSession.id == session_id,
            ChatSession.user_id == user_ctx.id,
        )
    )
    session_obj = db.scalars(stmt).first()
    if session_obj is None:
        raise NotFoundException(
            detail="Chat session not found",
            error_code="CHAT_SESSION_NOT_FOUND",
        )
    return ChatSessionDetail.model_validate(session_obj)
