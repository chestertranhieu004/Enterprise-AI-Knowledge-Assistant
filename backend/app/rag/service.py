# File: backend/app/rag/service.py
import logging
import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import AuditLog, ChatMessage, ChatSession
from app.rag.prompts import is_prompt_injection, sanitize_user_query
from app.rag.providers.base import (
    BaseEmbeddingProvider,
    BaseLLMProvider,
    RetrievedChunk,
)
from app.rag.providers.factory import get_embedding_provider, get_llm_provider
from app.rag.retriever import PreRetrievalRBACRetriever
from app.rag.verifier import CitationVerifier
from app.schemas.chat import ChatQueryRequest, ChatQueryResponse, CitationItem
from app.schemas.common import ABSTENTION_MESSAGE_VI
from app.schemas.user import UserContext

logger = logging.getLogger(__name__)


class RAGService:
    """
    High-Level RAG Orchestrator enforcing all 3 Mandatory Enterprise Scenarios:
    - Scenario 3: Pre-Retrieval RBAC & Department ABAC inside SQL WHERE (PreRetrievalRBACRetriever).
    - Scenario 1: Similarity threshold & zero-hallucination abstention with 2-3 suggested queries.
    - Scenario 2: Post-Generation Citation Verification against retrieved chunk whitelist & quote overlap.
    """

    def __init__(
        self,
        embedding_provider: BaseEmbeddingProvider | None = None,
        llm_provider: BaseLLMProvider | None = None,
        similarity_threshold: float | None = None,
        quote_overlap_threshold: float | None = None,
    ) -> None:
        self.embedding_provider = embedding_provider or get_embedding_provider()
        self.llm_provider = llm_provider or get_llm_provider()
        self.similarity_threshold = (
            similarity_threshold
            if similarity_threshold is not None
            else settings.SIMILARITY_THRESHOLD
        )
        self.retriever = PreRetrievalRBACRetriever(
            embedding_provider=self.embedding_provider,
            similarity_threshold=self.similarity_threshold,
        )
        self.verifier = CitationVerifier(
            overlap_threshold=(
                quote_overlap_threshold
                if quote_overlap_threshold is not None
                else settings.QUOTE_OVERLAP_THRESHOLD
            )
        )

    def _ensure_suggestions(self, query: str, candidate_suggestions: list[str] | None) -> list[str]:
        cleaned = [str(s).strip() for s in (candidate_suggestions or []) if str(s).strip()]
        if len(cleaned) >= 2:
            return cleaned[:3]
        fallback = self.llm_provider.suggest_rephrased_queries(query)
        return [str(s).strip() for s in fallback if str(s).strip()][:3]

    def query(
        self,
        db: Session,
        user: UserContext,
        request: ChatQueryRequest,
    ) -> ChatQueryResponse:
        """
        Execute an end-to-end RBAC-filtered, citation-verified RAG query.
        """
        start_ns = time.perf_counter_ns()
        clean_query = sanitize_user_query(request.query)

        # 1. Resolve or create ChatSession belonging to `user`
        session_obj: ChatSession | None = None
        if request.session_id is not None:
            session_obj = db.scalars(
                select(ChatSession).where(
                    ChatSession.id == request.session_id,
                    ChatSession.user_id == user.id,
                )
            ).first()

        if session_obj is None:
            session_title = clean_query[:80] if clean_query else "Cuộc hội thoại mới"
            session_obj = ChatSession(
                user_id=user.id,
                title=session_title,
            )
            db.add(session_obj)
            db.flush()
        else:
            session_obj.updated_at = datetime.now(timezone.utc)

        # 2. Persist user's ChatMessage
        user_msg = ChatMessage(
            session_id=session_obj.id,
            role="user",
            content=request.query,
        )
        db.add(user_msg)
        db.flush()

        # 3. Pre-Retrieval Guardrail & Scenario 3 RBAC SQL Retrieval
        prompt_injected = is_prompt_injection(request.query)
        if prompt_injected:
            authorized_chunks: list[RetrievedChunk] = []
        else:
            authorized_chunks = self.retriever.retrieve_authorized_chunks(
                db=db,
                user=user,
                query=request.query,
                top_k=request.top_k,
                similarity_threshold=self.similarity_threshold,
            )

        rejected_chunk_ids: list[str] = []

        # 4. Gate 1 (Scenario 1 & Scenario 3 Abstention): 0 authorized chunks >= threshold
        if not authorized_chunks:
            found = False
            answer = ABSTENTION_MESSAGE_VI
            citations: list[CitationItem] = []
            suggested_queries = self._ensure_suggestions(request.query, None)
        else:
            # 5. Gate 2: Constrained Grounded LLM Generation
            generation = self.llm_provider.generate_grounded_answer(
                query=request.query,
                chunks=authorized_chunks,
            )

            # 6. Gate 3 (Scenario 2): Deterministic Post-Generation Citation Verification
            verification = self.verifier.verify(
                generation=generation,
                authorized_chunks=authorized_chunks,
            )
            rejected_chunk_ids = verification.rejected_chunk_ids

            if not verification.found:
                found = False
                answer = ABSTENTION_MESSAGE_VI
                citations = []
                suggested_queries = self._ensure_suggestions(
                    request.query, generation.suggested_queries
                )
            else:
                found = True
                answer = verification.answer
                citations = verification.citations
                suggested_queries = self._ensure_suggestions(
                    request.query, generation.suggested_queries
                )

        latency_ms = max(1, int((time.perf_counter_ns() - start_ns) / 1_000_000))

        # 7. Persist assistant's ChatMessage
        citations_payload = [c.model_dump(mode="json") for c in citations]
        assistant_msg = ChatMessage(
            session_id=session_obj.id,
            role="assistant",
            content=answer,
            found=found,
            citations_json=citations_payload,
            suggested_queries_json=suggested_queries,
            latency_ms=latency_ms,
        )
        db.add(assistant_msg)

        # 8. Persist AuditLog (Zero leakage of unauthorized document names)
        db.add(
            AuditLog(
                user_id=user.id,
                action="CHAT_QUERY",
                resource_type="chat_session",
                resource_id=str(session_obj.id),
                details_json={
                    "found": found,
                    "citation_count": len(citations),
                    "retrieved_chunk_count": len(authorized_chunks),
                    "rejected_chunk_ids": rejected_chunk_ids,
                    "prompt_injection_blocked": prompt_injected,
                    "top_k": request.top_k,
                },
            )
        )
        db.commit()
        db.refresh(assistant_msg)

        # 9. Return ChatQueryResponse
        return ChatQueryResponse(
            session_id=session_obj.id,
            message_id=assistant_msg.id,
            query=request.query,
            answer=answer,
            found=found,
            citations=citations,
            suggested_queries=suggested_queries,
            latency_ms=latency_ms,
        )
