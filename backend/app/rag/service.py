# File: backend/app/rag/service.py
import inspect
import logging
import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import AppException
from app.db.models import AuditLog, ChatMessage, ChatSession
from app.rag.agent_router import classify_conversational_intent, is_out_of_scope_query
from app.rag.prompts import is_prompt_injection, sanitize_user_query
from app.rag.providers.base import (
    AIProviderError,
    BaseEmbeddingProvider,
    BaseLLMProvider,
    RawLLMGeneration,
    RetrievedChunk,
)
from app.rag.providers.factory import get_embedding_provider, get_llm_provider
from app.rag.retriever import PreRetrievalRBACRetriever
from app.rag.verifier import CitationVerifier
from app.schemas.chat import (
    ChatQueryRequest,
    ChatQueryResponse,
    CitationItem,
    RelatedDocumentItem,
)
from app.schemas.common import (
    ABSTENTION_MESSAGE_VI,
    RELATED_DOCUMENTS_INTRO_VI,
    RELATED_DOCUMENTS_LEAD_VI,
    RELATED_DOCUMENTS_OUTRO_VI,
)
from app.schemas.user import UserContext

logger = logging.getLogger(__name__)


class RAGService:
    """
    High-Level Conversational RAG Orchestrator enforcing all 3 Mandatory Enterprise Scenarios
    alongside natural multi-turn social interaction:
    - Security Gate: Prompt-injection & jailbreak blocking pre-intent and pre-retrieval.
    - Conversational Intent Router: Natural greetings/identity/capabilities/thanks/farewell/smalltalk.
    - Scenario 3: Pre-Retrieval RBAC & Department ABAC inside SQL WHERE (PreRetrievalRBACRetriever).
    - Scenario 1 (tiered): similarity threshold & zero-hallucination abstention with 2-3 suggested queries.
        * answered  : chunks >= similarity_threshold AND a verified grounded answer.
        * related   : no verified answer, but authorized chunks >= related_similarity_threshold exist
                      and the query is not general-knowledge/real-time -> point to those documents
                      ("Nội dung bạn đang tìm kiếm có thể liên quan đến ...") with found=False and
                      zero generated facts.
        * not_found : otherwise -> exact ABSTENTION_MESSAGE_VI with found=False.
    - Scenario 2: Post-Generation Citation Verification against retrieved chunk whitelist & quote overlap.
    """

    def __init__(
        self,
        embedding_provider: BaseEmbeddingProvider | None = None,
        llm_provider: BaseLLMProvider | None = None,
        similarity_threshold: float | None = None,
        quote_overlap_threshold: float | None = None,
        related_similarity_threshold: float | None = None,
    ) -> None:
        self.embedding_provider = embedding_provider or get_embedding_provider()
        self.llm_provider = llm_provider or get_llm_provider()
        self._explicit_similarity_threshold: float | None = similarity_threshold
        self.retriever = PreRetrievalRBACRetriever(
            embedding_provider=self.embedding_provider,
            similarity_threshold=similarity_threshold,
        )
        self.similarity_threshold = self.retriever.similarity_threshold
        self.related_similarity_threshold = self._resolve_related_threshold(
            related_similarity_threshold
        )
        self.verifier = CitationVerifier(
            overlap_threshold=(
                quote_overlap_threshold
                if quote_overlap_threshold is not None
                else settings.QUOTE_OVERLAP_THRESHOLD
            )
        )

    def _resolve_related_threshold(self, explicit: float | None) -> float:
        if explicit is not None:
            return float(explicit)
        if settings.RELATED_SIMILARITY_THRESHOLD is not None:
            return float(settings.RELATED_SIMILARITY_THRESHOLD)
        provider_name = getattr(self.embedding_provider, "provider_name", "mock")
        # gemini-embedding-001 scores unrelated text much higher than OpenAI/mock embeddings.
        return 0.60 if provider_name == "gemini" else 0.40

    def _select_related_documents(
        self,
        query: str,
        candidate_chunks: list[RetrievedChunk],
    ) -> list[RelatedDocumentItem]:
        """
        Pick up to RELATED_MAX_DOCUMENTS distinct authorized documents whose best chunk scores
        >= related_similarity_threshold. Candidates come exclusively from the Pre-Retrieval RBAC
        retriever, so no unauthorized title can ever be mentioned.
        """
        if not candidate_chunks or is_out_of_scope_query(query):
            return []
        best_per_doc: dict[str, RetrievedChunk] = {}
        for chunk in sorted(candidate_chunks, key=lambda c: c.score, reverse=True):
            if chunk.score < self.related_similarity_threshold:
                continue
            best_per_doc.setdefault(str(chunk.document_id), chunk)
        max_docs = max(1, int(settings.RELATED_MAX_DOCUMENTS))
        related: list[RelatedDocumentItem] = []
        for chunk in list(best_per_doc.values())[:max_docs]:
            excerpt = " ".join(chunk.content.split())
            if len(excerpt) > 220:
                excerpt = excerpt[:220].rsplit(" ", 1)[0] + "…"
            related.append(
                RelatedDocumentItem(
                    document_id=chunk.document_id,
                    title=(chunk.title or chunk.file_name).strip(),
                    file_name=chunk.file_name,
                    page_or_sheet=chunk.page_or_sheet,
                    excerpt=excerpt,
                    score=round(float(chunk.score), 4),
                )
            )
        return related

    @staticmethod
    def _build_related_answer(related_docs: list[RelatedDocumentItem]) -> str:
        lines = [RELATED_DOCUMENTS_INTRO_VI, RELATED_DOCUMENTS_LEAD_VI]
        for doc in related_docs:
            location = f"{doc.file_name} · {doc.page_or_sheet}" if doc.page_or_sheet else doc.file_name
            lines.append(f"• {doc.title} ({location})")
        lines.append(RELATED_DOCUMENTS_OUTRO_VI)
        return "\n".join(lines)

    def _related_suggestions(
        self,
        query: str,
        related_docs: list[RelatedDocumentItem],
        candidate_suggestions: list[str] | None,
    ) -> list[str]:
        doc_based = [
            f"Tài liệu “{doc.title}” quy định những nội dung chính nào?"
            for doc in related_docs[:2]
        ]
        llm_based = self._ensure_suggestions(query, candidate_suggestions)
        merged: list[str] = []
        for s in doc_based + llm_based:
            if s and s not in merged:
                merged.append(s)
        return merged[:3]

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
        match_level = "not_found"
        related_documents: list[RelatedDocumentItem] = []
        top_score: float | None = None

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
                    match_level = "conversational"
                    answer = conv_generation.answer.strip()
                    citations = []
                suggested_queries = self._ensure_suggestions(
                    request.query, conv_generation.suggested_queries
                )
            else:
                # Step C: Multi-turn Query Rewriting -> Scenario 3 Pre-Retrieval RBAC -> Grounded Generation -> Scenario 2 Verification
                try:
                    search_query = self.llm_provider.rewrite_query_with_history(
                        request.query, history=history
                    )
                    answer_threshold = (
                        self._explicit_similarity_threshold
                        if self._explicit_similarity_threshold is not None
                        else self.similarity_threshold
                    )
                    # One RBAC-filtered retrieval down to the lower of the two thresholds,
                    # then split into "answer" chunks and "related" candidates.
                    candidate_chunks = self.retriever.retrieve_authorized_chunks(
                        db=db,
                        user=user,
                        query=search_query,
                        top_k=max(request.top_k, 3 * max(1, int(settings.RELATED_MAX_DOCUMENTS))),
                        similarity_threshold=min(
                            answer_threshold, self.related_similarity_threshold
                        ),
                    )
                    top_score = max((c.score for c in candidate_chunks), default=None)
                    authorized_chunks = [
                        c for c in candidate_chunks if c.score >= answer_threshold
                    ][: request.top_k]
                    generation_suggestions: list[str] | None = None

                    # Gate 1 (Scenario 1 & Scenario 3 Abstention): 0 authorized chunks >= threshold
                    if not authorized_chunks:
                        found = False
                        answer = ABSTENTION_MESSAGE_VI
                        citations = []
                        suggested_queries = []
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
                            suggested_queries = []
                            generation_suggestions = generation.suggested_queries
                        else:
                            found = True
                            match_level = "answered"
                            answer = verification.answer
                            citations = verification.citations
                            suggested_queries = self._ensure_suggestions(
                                request.query, generation.suggested_queries
                            )

                    # Scenario 1 tiering: no verified answer -> related documents or strict abstention
                    if not found:
                        related_documents = self._select_related_documents(
                            request.query, candidate_chunks
                        )
                        if related_documents:
                            match_level = "related"
                            answer = self._build_related_answer(related_documents)
                            suggested_queries = self._related_suggestions(
                                request.query, related_documents, generation_suggestions
                            )
                        else:
                            suggested_queries = self._ensure_suggestions(
                                request.query, generation_suggestions
                            )
                    logger.info(
                        "RAG tiering: match_level=%s top_score=%s answer_threshold=%.3f related_threshold=%.3f",
                        match_level,
                        f"{top_score:.4f}" if top_score is not None else "none",
                        answer_threshold,
                        self.related_similarity_threshold,
                    )
                except AIProviderError as exc:
                    db.rollback()
                    logger.error(
                        "RAGService aborted due to AI provider failure (provider=%s, status=%s): %s",
                        exc.provider,
                        exc.status_code,
                        exc.reason,
                    )
                    raise AppException(
                        detail=exc.to_user_message(),
                        status_code=503,
                        error_code="AI_PROVIDER_UNAVAILABLE",
                    ) from exc

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
                    "match_level": match_level,
                    "top_score": round(top_score, 4) if top_score is not None else None,
                    "related_document_ids": [str(d.document_id) for d in related_documents],
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
            match_level=match_level,
            related_documents=related_documents,
        )
