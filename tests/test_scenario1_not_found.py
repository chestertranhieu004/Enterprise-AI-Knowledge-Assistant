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
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditLog, ChatMessage, DocumentChunk
from app.rag.providers.base import cosine_similarity
from app.rag.providers.factory import get_embedding_provider
from app.rag.service import RAGService
from app.schemas.chat import ChatQueryRequest
from app.schemas.common import ABSTENTION_MESSAGE_VI
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
    """When similarity_threshold is set above the highest chunk score (e.g. 0.999), RAGService must abstain."""
    strict_rag = RAGService(similarity_threshold=0.999)
    response = strict_rag.query(
        db=db_session,
        user=user_contexts["admin"],
        request=ChatQueryRequest(
            query="Chính sách nghỉ phép của công ty như thế nào?",
            top_k=5,
        ),
    )
    assert response.found is False
    assert response.answer == ABSTENTION_MESSAGE_VI
    assert response.citations == []
    assert 2 <= len(response.suggested_queries) <= 3
