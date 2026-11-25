# File: tests/test_scenario1_not_found.py
"""
Mandatory Scenario 1 Verification Suite: AI không tìm thấy thông tin (Zero-Hallucination Abstention).

Proves that when a query has no matching authorized chunks above similarity_threshold (0.65):
1. `found` is strictly `False`.
2. `answer` equals exact `ABSTENTION_MESSAGE_VI`:
   "Không tìm thấy thông tin phù hợp trong các tài liệu bạn có quyền truy cập."
3. `citations` is empty (`[]`) with zero fabricated chunk IDs or source names.
4. `2 <= len(suggested_queries) <= 3` in Vietnamese.
5. Zero hallucinated facts and zero leakage of restricted document titles/numbers in `suggested_queries`.

Tiered behaviour (match_level):
- "related": no chunk answers the query, but authorized chunks >= related_similarity_threshold exist
  and the query is not general-knowledge / real-time -> found=False, citations=[], the answer only
  lists those authorized documents ("Nội dung bạn đang tìm kiếm có thể liên quan đến ...").
- "not_found": everything else -> exact ABSTENTION_MESSAGE_VI.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditLog, ChatMessage, DocumentChunk
from app.rag.providers.base import RawLLMGeneration, RetrievedChunk, cosine_similarity
from app.rag.providers.factory import get_embedding_provider
from app.rag.service import RAGService
from app.schemas.chat import ChatQueryRequest
from app.schemas.common import ABSTENTION_MESSAGE_VI, RELATED_DOCUMENTS_LEAD_VI
from app.schemas.user import UserContext

OUT_OF_DOMAIN_QUERIES: list[str] = [
    "Công thức nấu phở bò truyền thống trên sao Hỏa?",
    "Dự báo giá cổ phiếu Bitcoin năm 2099?",
    "Hướng dẫn chế tạo động cơ tên lửa hạt nhân liên thiên hà?",
    "Danh sách cầu thủ ghi bàn chung kết World Cup năm 2090?",
]

RESTRICTED_SECRETS_BLACKLIST: tuple[str, ...] = (
    "Bang_luong_mat_Ban_Giam_doc_HR_2026.xlsx",
    "Bang_Luong_Cap_Cao",
    "Bảng Lương Mật Ban Giám Đốc",
    "120.000.000",
    "180.000.000",
    "190.000.000",
    "250.000.000",
    "Executive Bonus Pool",
)


@pytest.mark.parametrize("query_text", OUT_OF_DOMAIN_QUERIES)
def test_scenario1_out_of_domain_queries_abstain_cleanly(
    db_session: Session,
    rag_service: RAGService,
    user_contexts: dict[str, UserContext],
    query_text: str,
) -> None:
    """Out-of-domain queries must return found=False, exact ABSTENTION_MESSAGE_VI, citations=[], and 2-3 suggestions."""
    for persona in ("admin", "hr.manager", "eng.employee", "guest"):
        user_ctx = user_contexts[persona]
        response = rag_service.query(
            db=db_session,
            user=user_ctx,
            request=ChatQueryRequest(query=query_text, top_k=5),
        )

        assert response.found is False, (
            f"Expected found=False for out-of-domain query '{query_text}' as '{persona}'"
        )
        assert response.answer == ABSTENTION_MESSAGE_VI
        assert response.citations == []
        assert 2 <= len(response.suggested_queries) <= 3

        for suggestion in response.suggested_queries:
            assert isinstance(suggestion, str) and len(suggestion.strip()) >= 10
            for secret in RESTRICTED_SECRETS_BLACKLIST:
                assert secret.lower() not in suggestion.lower(), (
                    f"Restricted secret '{secret}' leaked inside suggested_query: {suggestion}"
                )


def test_scenario1_api_endpoint_abstention_and_zero_leakage(
    client: TestClient,
    auth_headers: dict[str, dict[str, str]],
    db_session: Session,
) -> None:
    """POST /api/v1/chat/query enforces Scenario 1 contract and persists abstention state in DB."""
    query_text = "Công thức nấu phở bò truyền thống trên sao Hỏa?"
    resp = client.post(
        "/api/v1/chat/query",
        headers=auth_headers["eng.employee"],
        json={"query": query_text, "top_k": 5},
    )
    assert resp.status_code == 200
    body = resp.json()

    assert body["found"] is False
    assert body["answer"] == ABSTENTION_MESSAGE_VI
    assert body["citations"] == []
    assert 2 <= len(body["suggested_queries"]) <= 3
    assert body["latency_ms"] >= 1

    # Verify zero hallucinated facts or restricted titles in answer or suggestions
    combined_output = body["answer"] + " " + " ".join(body["suggested_queries"])
    for secret in RESTRICTED_SECRETS_BLACKLIST:
        assert secret.lower() not in combined_output.lower()
    for hallucinated_term in ("sao hỏa", "phở bò", "nước dùng", "quế hồi"):
        assert hallucinated_term not in body["answer"].lower()

    # Verify ChatMessage and AuditLog persistence
    msg_row = db_session.scalars(
        select(ChatMessage).where(ChatMessage.id == uuid.UUID(body["message_id"]))
    ).first()
    assert msg_row is not None
    assert msg_row.found is False
    assert msg_row.content == ABSTENTION_MESSAGE_VI
    assert msg_row.citations_json == []
    assert 2 <= len(msg_row.suggested_queries_json) <= 3

    audit_row = db_session.scalars(
        select(AuditLog)
        .where(
            AuditLog.action == "CHAT_QUERY",
            AuditLog.resource_id == body["session_id"],
        )
        .order_by(AuditLog.created_at.desc())
    ).first()
    assert audit_row is not None
    assert audit_row.details_json["found"] is False
    assert audit_row.details_json["citation_count"] == 0
    assert audit_row.details_json["retrieved_chunk_count"] == 0


def test_scenario1_embedding_orthogonality_below_similarity_threshold(
    db_session: Session,
) -> None:
    """Out-of-domain queries must have cosine similarity < 0.65 against every chunk in the database."""
    emb_provider = get_embedding_provider()
    all_chunks = db_session.scalars(select(DocumentChunk)).all()
    assert len(all_chunks) >= 5

    for query_text in OUT_OF_DOMAIN_QUERIES:
        q_vec = emb_provider.embed_query(query_text)
        for chunk in all_chunks:
            sim = cosine_similarity(q_vec, list(chunk.embedding))
            assert sim < 0.65, (
                f"Out-of-domain query '{query_text}' had unexpectedly high similarity {sim:.4f} "
                f"with chunk {chunk.id} ({chunk.page_or_sheet})"
            )


def test_scenario1_strict_threshold_gate_abstains_when_below_cutoff(
    db_session: Session,
    user_contexts: dict[str, UserContext],
) -> None:
    """When both thresholds are above the highest chunk score (e.g. 0.999), RAGService must abstain."""
    strict_rag = RAGService(similarity_threshold=0.999, related_similarity_threshold=0.999)
    response = strict_rag.query(
        db=db_session,
        user=user_contexts["admin"],
        request=ChatQueryRequest(
            query="Chính sách nghỉ phép của công ty như thế nào?",
            top_k=5,
        ),
    )
    assert response.found is False
    assert response.match_level == "not_found"
    assert response.related_documents == []
    assert response.answer == ABSTENTION_MESSAGE_VI
    assert response.citations == []
    assert 2 <= len(response.suggested_queries) <= 3


# ---------------------------------------------------------------------------
# Tiered Scenario 1: "related documents" vs strict abstention
# ---------------------------------------------------------------------------
_LEAVE_DOC_ID = uuid.uuid4()
_PAYMENT_DOC_ID = uuid.uuid4()
_CICD_DOC_ID = uuid.uuid4()


def _fake_chunk(doc_id: uuid.UUID, title: str, file_name: str, page: str, content: str, score: float) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid4(),
        document_id=doc_id,
        file_name=file_name,
        title=title,
        department="General",
        access_level="public",
        page_or_sheet=page,
        chunk_index=0,
        content=content,
        score=score,
    )


def _patched_service(
    monkeypatch: pytest.MonkeyPatch,
    scores: tuple[float, float, float],
    llm_abstains: bool = False,
) -> tuple[RAGService, dict[str, Any]]:
    service = RAGService(similarity_threshold=0.65, related_similarity_threshold=0.50)
    calls: dict[str, Any] = {"retrieval_thresholds": [], "generate_calls": 0}
    leave, payment, cicd = scores
    chunks = [
        _fake_chunk(_LEAVE_DOC_ID, "Chính sách nghỉ phép 2026", "Chinh_sach_nghi_phep_2026.pdf", "Page 1",
                    "Nhân viên chính thức được hưởng 12 ngày phép năm có lương.", leave),
        _fake_chunk(_LEAVE_DOC_ID, "Chính sách nghỉ phép 2026", "Chinh_sach_nghi_phep_2026.pdf", "Page 2",
                    "Thời gian làm việc từ 8h30 đến 17h30.", leave - 0.02),
        _fake_chunk(_PAYMENT_DOC_ID, "Quy trình phê duyệt thanh toán", "Quy_trinh_phe_duyet_thanh_toan_Finance.docx",
                    "Section 1", "Hóa đơn trên 50,000,000 VNĐ cần CFO phê duyệt.", payment),
        _fake_chunk(_CICD_DOC_ID, "Tiêu chuẩn CI/CD", "Tieu_chuan_CI_CD_Engineering.md", "Section 1",
                    "Độ phủ pytest tối thiểu 80%.", cicd),
    ]

    def fake_retrieve(db: Any, user: Any, query: str, top_k: int = 5, similarity_threshold: float | None = None) -> list[RetrievedChunk]:
        calls["retrieval_thresholds"].append(similarity_threshold)
        floor = similarity_threshold if similarity_threshold is not None else 0.0
        kept = [c for c in chunks if c.score >= floor]
        return sorted(kept, key=lambda c: c.score, reverse=True)[:top_k]

    monkeypatch.setattr(service.retriever, "retrieve_authorized_chunks", fake_retrieve)

    original_generate = service.llm_provider.generate_grounded_answer

    def fake_generate(query: str, chunks: list[RetrievedChunk], history: list[dict[str, str]] | None = None) -> RawLLMGeneration:
        calls["generate_calls"] += 1
        if llm_abstains:
            return RawLLMGeneration(answer=ABSTENTION_MESSAGE_VI, abstained=True)
        return original_generate(query=query, chunks=chunks, history=history or [])

    monkeypatch.setattr(service.llm_provider, "generate_grounded_answer", fake_generate)
    return service, calls


def test_scenario1_related_tier_points_to_related_documents_without_fabrication(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
    user_contexts: dict[str, UserContext],
) -> None:
    """Mid-similarity query: found=False, no citations, answer lists ONLY related authorized documents."""
    service, calls = _patched_service(monkeypatch, scores=(0.58, 0.53, 0.31))
    response = service.query(
        db=db_session,
        user=user_contexts["eng.employee"],
        request=ChatQueryRequest(query="Chính sách nghỉ thai sản cho nhân viên nữ như thế nào?", top_k=5),
    )

    assert calls["retrieval_thresholds"] == [0.50]
    assert calls["generate_calls"] == 0, "No chunk >= answer threshold, so the LLM must not be asked to answer"
    assert response.found is False
    assert response.match_level == "related"
    assert response.citations == []
    assert RELATED_DOCUMENTS_LEAD_VI in response.answer
    assert "Chính sách nghỉ phép 2026" in response.answer
    assert "Quy trình phê duyệt thanh toán" in response.answer
    assert "Tiêu chuẩn CI/CD" not in response.answer, "Docs below related threshold must not be suggested"
    # Zero generated facts: the answer must not repeat any number/fact from the chunks
    for fact in ("12 ngày", "50,000,000", "8h30"):
        assert fact not in response.answer

    assert [d.document_id for d in response.related_documents] == [_LEAVE_DOC_ID, _PAYMENT_DOC_ID]
    assert response.related_documents[0].page_or_sheet == "Page 1"
    assert 2 <= len(response.suggested_queries) <= 3


def test_scenario1_related_tier_when_llm_abstains_on_retrieved_chunks(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
    user_contexts: dict[str, UserContext],
) -> None:
    """Chunks pass the answer threshold but the LLM finds no direct answer -> related documents, not a guess."""
    service, calls = _patched_service(monkeypatch, scores=(0.72, 0.40, 0.30), llm_abstains=True)
    response = service.query(
        db=db_session,
        user=user_contexts["eng.employee"],
        request=ChatQueryRequest(query="Nghỉ không lương được tối đa bao lâu?", top_k=5),
    )
    assert calls["generate_calls"] == 1
    assert response.found is False
    assert response.match_level == "related"
    assert response.citations == []
    assert [d.document_id for d in response.related_documents] == [_LEAVE_DOC_ID]
    assert "Quy trình phê duyệt thanh toán" not in response.answer


@pytest.mark.parametrize(
    "query_text",
    [
        "hôm nay là ngày bao nhiêu",
        "Thời tiết Hà Nội hôm nay thế nào?",
        "1 cộng 1 bằng mấy?",
        "Viết cho tôi một bài thơ về mùa thu",
    ],
)
def test_scenario1_out_of_scope_queries_never_get_related_documents(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
    user_contexts: dict[str, UserContext],
    query_text: str,
) -> None:
    """General-knowledge / real-time queries keep the strict abstention even if embeddings score mid-range."""
    service, _ = _patched_service(monkeypatch, scores=(0.58, 0.55, 0.52))
    response = service.query(
        db=db_session,
        user=user_contexts["eng.employee"],
        request=ChatQueryRequest(query=query_text, top_k=5),
    )
    assert response.found is False
    assert response.match_level == "not_found"
    assert response.answer == ABSTENTION_MESSAGE_VI
    assert response.related_documents == []
    assert response.citations == []
    assert 2 <= len(response.suggested_queries) <= 3


def test_scenario1_low_similarity_keeps_strict_abstention(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
    user_contexts: dict[str, UserContext],
) -> None:
    """All chunks below the related threshold -> exact abstention message with 2-3 suggestions."""
    service, calls = _patched_service(monkeypatch, scores=(0.45, 0.40, 0.30))
    response = service.query(
        db=db_session,
        user=user_contexts["eng.employee"],
        request=ChatQueryRequest(query="Quy định về đồng phục công ty?", top_k=5),
    )
    assert calls["generate_calls"] == 0
    assert response.found is False
    assert response.match_level == "not_found"
    assert response.answer == ABSTENTION_MESSAGE_VI
    assert response.related_documents == []
    assert 2 <= len(response.suggested_queries) <= 3


def test_scenario1_direct_answer_still_answered(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
    user_contexts: dict[str, UserContext],
) -> None:
    """High-similarity query keeps the normal grounded, verified answer path."""
    service, calls = _patched_service(monkeypatch, scores=(0.82, 0.40, 0.30))
    response = service.query(
        db=db_session,
        user=user_contexts["eng.employee"],
        request=ChatQueryRequest(query="Nhân viên chính thức được bao nhiêu ngày phép năm?", top_k=5),
    )
    assert calls["generate_calls"] == 1
    assert response.found is True
    assert response.match_level == "answered"
    assert response.related_documents == []
    assert len(response.citations) >= 1
