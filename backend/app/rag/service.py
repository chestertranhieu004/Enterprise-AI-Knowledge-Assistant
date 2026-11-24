# File: backend/app/rag/service.py
import inspect
import logging
import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import AuditLog, ChatMessage, ChatSession
from app.rag.agent_router import classify_conversational_intent
from app.rag.prompts import is_prompt_injection, sanitize_user_query
from app.rag.providers.base import (
    BaseEmbeddingProvider,
    BaseLLMProvider,
    RawLLMGeneration,
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
    High-Level Conversational RAG Orchestrator enforcing all 3 Mandatory Enterprise Scenarios
    alongside natural multi-turn social interaction:
    - Security Gate: Prompt-injection & jailbreak blocking pre-intent and pre-retrieval.
    - Conversational Intent Router: Natural greetings/identity/capabilities/thanks/farewell/smalltalk.
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
        self._explicit_similarity_threshold: float | None = similarity_threshold
        self.retriever = PreRetrievalRBACRetriever(
            embedding_provider=self.embedding_provider,
            similarity_threshold=similarity_threshold,
        )
        self.similarity_threshold = self.retriever.similarity_threshold
        self.verifier = CitationVerifier(
            overlap_threshold=(
                quote_overlap_threshold
                if quote_overlap_threshold is not None
                else settings.QUOTE_OVERLAP_THRESHOLD
            )
        )

    def _ensure_suggestions(
        self,
        query: str,
        candidate_suggestions: list[str] | None,
    ) -> list[str]:
        cleaned = [str(s).strip() for s in (candidate_suggestions or []) if str(s).strip()]
        if len(cleaned) >= 2:
            return cleaned[:3]
        fallback = self.llm_provider.suggest_rephrased_queries(query)
        return [str(s).strip() for s in fallback if str(s).strip()][:3]

    def _invoke_generate_grounded_answer(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        history: list[dict[str, str]],
    ) -> RawLLMGeneration:
        """
        Call `llm_provider.generate_grounded_answer` while preserving 100% backward compatibility
        with custom `BaseLLMProvider` subclasses that only accept `(query, chunks)`.
        """
        fn = self.llm_provider.generate_grounded_answer
        try:
            params = inspect.signature(fn).parameters
            accepts_history = "history" in params or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
            )
        except (TypeError, ValueError):
            accepts_history = False

        if accepts_history:
            return fn(query=query, chunks=chunks, history=history)
        return fn(query=query, chunks=chunks)

    def query(
        self,
        db: Session,
        user: UserContext,
        request: ChatQueryRequest,
    ) -> ChatQueryResponse:
        """
        Execute an end-to-end conversational, RBAC-filtered, citation-verified RAG query.
        """
        start_ns = time.perf_counter_ns()
        clean_query = sanitize_user_query(request.query)

        # 1. Resolve or create ChatSession belonging to `user`
        session_obj: ChatSession | None = None
        existing_session = False
        if request.session_id is not None:
            session_obj = db.scalars(
                select(ChatSession).where(
                    ChatSession.id == request.session_id,
                    ChatSession.user_id == user.id,
                )
            ).first()
            existing_session = session_obj is not None

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

        # 2. Load recent conversation history (up to last 6 messages) BEFORE saving new user message
        history: list[dict[str, str]] = []
        if existing_session:
            recent_msgs = list(
                db.scalars(
                    select(ChatMessage)
                    .where(ChatMessage.session_id == session_obj.id)
                    .order_by(ChatMessage.created_at.desc())
                    .limit(6)
                ).all()
            )
            recent_msgs.reverse()
            history = [
                {"role": str(m.role), "content": str(m.content)}
                for m in recent_msgs
                if m.content
            ]

        # 3. Persist user's ChatMessage
        user_msg = ChatMessage(
            session_id=session_obj.id,
            role="user",
            content=request.query,
        )
        db.add(user_msg)
        db.flush()

        authorized_chunks: list[RetrievedChunk] = []
        rejected_chunk_ids: list[str] = []
        conversational_intent: str | None = None
        search_query = request.query

        # Step A: Prompt-Injection Guardrail ALWAYS executes first
        prompt_injected = is_prompt_injection(request.query)
        if prompt_injected:
            found = False
            answer = ABSTENTION_MESSAGE_VI
            citations: list[CitationItem] = []
            suggested_queries = self._ensure_suggestions(request.query, None)
        else:
            # Step B: Conversational Intent Router (greetings, identity, capabilities, thanks, farewell, smalltalk)
            conversational_intent = classify_conversational_intent(request.query)
            if conversational_intent is not None:
                conv_generation = self.llm_provider.generate_conversational_reply(
                    query=request.query,
                    user=user,
                    history=history,
                    intent=conversational_intent,
                )
                if conv_generation.abstained or not conv_generation.answer.strip():
                    found = False
                    answer = ABSTENTION_MESSAGE_VI
                    citations = []
                else:
                    found = True
                    answer = conv_generation.answer.strip()
                    citations = []
                suggested_queries = self._ensure_suggestions(
                    request.query, conv_generation.suggested_queries
                )
            else:
                # Step C: Multi-turn Query Rewriting -> Scenario 3 Pre-Retrieval RBAC -> Grounded Generation -> Scenario 2 Verification
                search_query = self.llm_provider.rewrite_query_with_history(
                    request.query, history=history
                )
                authorized_chunks = self.retriever.retrieve_authorized_chunks(
                    db=db,
                    user=user,
                    query=search_query,
                    top_k=request.top_k,
                    similarity_threshold=self._explicit_similarity_threshold,
                )

                # Gate 1 (Scenario 1 & Scenario 3 Abstention): 0 authorized chunks >= threshold
                if not authorized_chunks:
                    found = False
                    answer = ABSTENTION_MESSAGE_VI
                    citations = []
                    suggested_queries = self._ensure_suggestions(request.query, None)
                else:
                    # Gate 2: Constrained Grounded LLM Generation
                    generation = self._invoke_generate_grounded_answer(
                        query=request.query,
                        chunks=authorized_chunks,
                        history=history,
                    )

                    # Gate 3 (Scenario 2): Deterministic Post-Generation Citation Verification
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
                    "conversational_intent": conversational_intent,
                    "query_rewritten": search_query != request.query,
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
