# File: tests/test_scenario2_citation_verification.py
"""
Mandatory Scenario 2 Verification Suite: Chống trả lời sai nguồn (Post-Generation Citation Verification).

Proves that:
1. Grounded queries ("Chính sách nghỉ phép của công ty như thế nào?",
   "Quy trình xin phê duyệt thanh toán?", "Tìm tài liệu liên quan đến hợp đồng năm 2025.")
   return `found=True`, `len(citations) >= 1`, valid DB `chunk_id`, exact `file_name`,
   accurate `page_or_sheet`, `verification_status == "verified"`, and a verbatim `quote`
   verified against `DocumentChunk.content`.
2. `CitationVerifier` deterministically rejects:
   - Fabricated `chunk_id` UUIDs not in `authorized_chunks`.
   - Real `chunk_id`s from unauthorized documents not in `authorized_chunks`.
   - Real authorized `chunk_id`s when the LLM fabricates numbers/claims ("Nhân viên được nghỉ 365 ngày phép/năm")
     that conflict with `chunk.content`, falling back to `found=False` and `ABSTENTION_MESSAGE_VI`.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Document, DocumentChunk
from app.rag.providers.base import (
     BaseLLMProvider,
    RawCitationCandidate,
    RawLLMGeneration,
    RetrievedChunk,
)
from app.rag.service import RAGService
from app.rag.verifier import CitationVerifier
from app.schemas.chat import ChatQueryRequest
from app.schemas.common import ABSTENTION_MESSAGE_VI, VerificationStatus
from app.schemas.user import UserContext


@pytest.mark.parametrize(
    ("persona", "query_text", "expected_file_name", "expected_page_prefix", "expected_keyword"),
    [
        (
            "eng.employee",
            "Chính sách nghỉ phép của công ty như thế nào?",
            "Chinh_sach_nghi_phep_2026.pdf",
            "Page 1",
            "12 ngày phép",
        ),
        (
            "finance.manager",
            "Quy trình xin phê duyệt thanh toán?",
            "Quy_trinh_phe_duyet_thanh_toan_Finance.docx",
            "Section 1: Quy trình phê duyệt thanh toán",
            "50,000,000",
        ),
        (
            "legal.manager",
            "Tìm tài liệu liên quan đến hợp đồng năm 2025.",
            "Hop_dong_thuong_mai_nam_2025_Legal.pdf",
            "Page 1",
            "hợp đồng thương mại năm 2025",
        ),
        (
            "hr.manager",
            "Bảng khung lương Giám đốc Khối và quỹ thưởng điều hành cấp cao năm 2026?",
            "Bang_luong_mat_Ban_Giam_doc_HR_2026.xlsx",
            "Sheet: Bang_Luong_Cap_Cao (Rows 1-2)",
            "120,000,000",
        ),
    ],
)
def test_scenario2_seeded_queries_return_verified_db_citations(
    db_session: Session,
    rag_service: RAGService,
    user_contexts: dict[str, UserContext],
    persona: str,
    query_text: str,
    expected_file_name: str,
    expected_page_prefix: str,
    expected_keyword: str,
) -> None:
    """Every grounded query must return verified citations matching authoritative DB DocumentChunk rows."""
    verifier = CitationVerifier()
    response = rag_service.query(
        db=db_session,
        user=user_contexts[persona],
        request=ChatQueryRequest(query=query_text, top_k=5),
    )

    assert response.found is True, f"Expected found=True for '{query_text}' as '{persona}'"
    assert len(response.citations) >= 1
    assert response.answer != ABSTENTION_MESSAGE_VI
    assert expected_keyword.lower() in response.answer.lower()

    matching_file_found = False
    for citation in response.citations:
        assert citation.verification_status == VerificationStatus.VERIFIED
        assert f"[{citation.chunk_id}]" in response.answer

        # Lookup authoritative chunk and document in DB
        db_chunk = db_session.get(DocumentChunk, citation.chunk_id)
        assert db_chunk is not None, f"Cited chunk_id {citation.chunk_id} does not exist in DB"
        db_doc = db_session.get(Document, citation.document_id)
        assert db_doc is not None
        assert db_chunk.document_id == db_doc.id

        # Metadata must match DB record exactly
        assert citation.file_name == db_doc.file_name
        assert citation.page_or_sheet == db_chunk.page_or_sheet
        assert 0.65 <= citation.score <= 1.0

        # Quote must be a verified substring / token match of db_chunk.content
        overlap = verifier.compute_quote_overlap(citation.quote, db_chunk.content)
        assert overlap >= 0.50, (
            f"Citation quote overlap {overlap:.2f} below threshold for quote: {citation.quote}"
        )
        assert citation.quote.strip() in db_chunk.content

        if citation.file_name == expected_file_name:
            matching_file_found = True
            assert citation.page_or_sheet.startswith(expected_page_prefix)

    assert matching_file_found, (
        f"Expected citation from '{expected_file_name}', got {[c.file_name for c in response.citations]}"
    )


def test_scenario2_verifier_rejects_fabricated_chunk_id_uuid(
    db_session: Session,
    seeded_documents: dict[str, Document],
) -> None:
    """CitationVerifier must reject a fabricated UUID not present in authorized_chunks."""
    leave_doc = seeded_documents["leave_policy_pdf"]
    real_chunk = db_session.scalars(
        select(DocumentChunk).where(DocumentChunk.document_id == leave_doc.id)
    ).first()
    assert real_chunk is not None

    authorized_chunks = [
        RetrievedChunk(
            chunk_id=real_chunk.id,
            document_id=leave_doc.id,
            file_name=leave_doc.file_name,
            title=leave_doc.title,
            department=leave_doc.department,
            access_level=leave_doc.access_level,
            page_or_sheet=real_chunk.page_or_sheet,
            chunk_index=real_chunk.chunk_index,
            content=real_chunk.content,
            score=0.89,
        )
    ]

    fabricated_id = uuid.uuid4()
    adversarial_generation = RawLLMGeneration(
        answer=f"Nhân viên chính thức được nghỉ phép 12 ngày phép hưởng nguyên lương [{fabricated_id}].",
        abstained=False,
        citations=[
            RawCitationCandidate(
                chunk_id=fabricated_id,
                quote="Nhân viên chính thức được nghỉ phép 12 ngày phép hưởng nguyên lương",
            )
        ],
    )

    verifier = CitationVerifier()
    result = verifier.verify(
        generation=adversarial_generation,
        authorized_chunks=authorized_chunks,
    )

    assert result.found is False
    assert result.answer == ABSTENTION_MESSAGE_VI
    assert result.citations == []
    assert str(fabricated_id) in result.rejected_chunk_ids


def test_scenario2_verifier_rejects_real_unauthorized_chunk_id_not_in_whitelist(
    db_session: Session,
    seeded_documents: dict[str, Document],
) -> None:
    """
    If the LLM attempts to cite a real database chunk_id belonging to an unauthorized document
    that was NOT in `authorized_chunks`, CitationVerifier must reject it.
    """
    leave_doc = seeded_documents["leave_policy_pdf"]
    hr_doc = seeded_documents["hr_restricted_salary_xlsx"]

    leave_chunk = db_session.scalars(
        select(DocumentChunk).where(DocumentChunk.document_id == leave_doc.id)
    ).first()
    hr_restricted_chunk = db_session.scalars(
        select(DocumentChunk).where(DocumentChunk.document_id == hr_doc.id)
    ).first()
    assert leave_chunk is not None and hr_restricted_chunk is not None

    # Only the public leave policy chunk was retrieved for this user
    authorized_chunks = [
        RetrievedChunk(
            chunk_id=leave_chunk.id,
            document_id=leave_doc.id,
            file_name=leave_doc.file_name,
            title=leave_doc.title,
            department=leave_doc.department,
            access_level=leave_doc.access_level,
            page_or_sheet=leave_chunk.page_or_sheet,
            chunk_index=leave_chunk.chunk_index,
            content=leave_chunk.content,
            score=0.88,
        )
    ]

    # LLM tries to cite the restricted HR chunk ID
    spoofed_generation = RawLLMGeneration(
        answer=f"Khung lương Giám đốc Khối từ 120.000.000 đến 180.000.000 VNĐ/tháng [{hr_restricted_chunk.id}].",
        abstained=False,
        citations=[
            RawCitationCandidate(
                chunk_id=hr_restricted_chunk.id,
                quote=hr_restricted_chunk.content,
            )
        ],
    )

    verifier = CitationVerifier()
    result = verifier.verify(
        generation=spoofed_generation,
        authorized_chunks=authorized_chunks,
    )

    assert result.found is False
    assert result.answer == ABSTENTION_MESSAGE_VI
    assert result.citations == []
    assert str(hr_restricted_chunk.id) in result.rejected_chunk_ids


def test_scenario2_verifier_rejects_fabricated_numbers_on_authorized_chunk_id(
    db_session: Session,
    seeded_documents: dict[str, Document],
    user_contexts: dict[str, UserContext],
) -> None:
    """
    When the LLM cites a real authorized chunk_id but fabricates numbers/claims
    ("Nhân viên được nghỉ 365 ngày phép/năm") that conflict with chunk.content ("12 ngày phép"),
    CitationVerifier must reject the citation and RAGService must fall back to Scenario 1 abstention.
    """
    leave_doc = seeded_documents["leave_policy_pdf"]
    leave_chunk = db_session.scalars(
        select(DocumentChunk)
        .where(
            DocumentChunk.document_id == leave_doc.id,
            DocumentChunk.page_or_sheet == "Page 1",
        )
    ).first()
    assert leave_chunk is not None

    class _HallucinatingLLMProvider(BaseLLMProvider):
        provider_name = "adversarial-hallucinator"
        model_name = "hallucinator-v1"

        def generate_grounded_answer(
            self,
            query: str,
            chunks: list[RetrievedChunk],
        ) -> RawLLMGeneration:
            target = chunks[0]
            return RawLLMGeneration(
                answer=f"Theo quy định mới, nhân viên được nghỉ 365 ngày phép/năm hưởng nguyên lương [{target.chunk_id}].",
                abstained=False,
                citations=[
                    RawCitationCandidate(
                        chunk_id=target.chunk_id,
                        quote="Nhân viên được nghỉ 365 ngày phép/năm hưởng nguyên lương.",
                    )
                ],
                suggested_queries=[
                    "Chính sách nghỉ phép năm của công ty quy định bao nhiêu ngày?",
                    "Thủ tục đăng ký nghỉ phép trên hệ thống HR như thế nào?",
                ],
            )

        def suggest_rephrased_queries(self, query: str) -> list[str]:
            return [
                "Chính sách nghỉ phép năm của công ty quy định bao nhiêu ngày?",
                "Thủ tục đăng ký nghỉ phép trên hệ thống HR như thế nào?",
            ]

    adversarial_rag = RAGService(llm_provider=_HallucinatingLLMProvider())
    response = adversarial_rag.query(
        db=db_session,
        user=user_contexts["eng.employee"],
        request=ChatQueryRequest(
            query="Chính sách nghỉ phép của công ty như thế nào?",
            top_k=5,
        ),
    )

    assert response.found is False
    assert response.answer == ABSTENTION_MESSAGE_VI
    assert response.citations == []
    assert "365" not in response.answer
    assert 2 <= len(response.suggested_queries) <= 3


def test_scenario2_verifier_strips_unverified_claim_while_preserving_valid_citation(
    db_session: Session,
    seeded_documents: dict[str, Document],
) -> None:
    """
    In a multi-sentence answer where Sentence 1 cites a verified chunk and Sentence 2 cites
    a fabricated chunk_id, CitationVerifier strips Sentence 2 and retains Sentence 1.
    """
    leave_doc = seeded_documents["leave_policy_pdf"]
    leave_chunk = db_session.scalars(
        select(DocumentChunk)
        .where(
            DocumentChunk.document_id == leave_doc.id,
            DocumentChunk.page_or_sheet == "Page 1",
        )
    ).first()
    assert leave_chunk is not None

    auth_chunk = RetrievedChunk(
        chunk_id=leave_chunk.id,
        document_id=leave_doc.id,
        file_name=leave_doc.file_name,
        title=leave_doc.title,
        department=leave_doc.department,
        access_level=leave_doc.access_level,
        page_or_sheet=leave_chunk.page_or_sheet,
        chunk_index=leave_chunk.chunk_index,
        content=leave_chunk.content,
        score=0.91,
    )
    bogus_id = uuid.uuid4()

    mixed_generation = RawLLMGeneration(
        answer=(
            f"Nhân viên chính thức được nghỉ phép 12 ngày phép hưởng nguyên lương mỗi năm dương lịch [{leave_chunk.id}]. "
            f"Ngoài ra mỗi nhân viên được tặng một chuyến du lịch sao Hỏa miễn phí [{bogus_id}]."
        ),
        abstained=False,
        citations=[
            RawCitationCandidate(
                chunk_id=leave_chunk.id,
                quote="Nhân viên chính thức được nghỉ phép 12 ngày phép hưởng nguyên lương mỗi năm dương lịch.",
            ),
            RawCitationCandidate(
                chunk_id=bogus_id,
                quote="Mỗi nhân viên được tặng một chuyến du lịch sao Hỏa miễn phí.",
            ),
        ],
    )

    verifier = CitationVerifier()
    result = verifier.verify(generation=mixed_generation, authorized_chunks=[auth_chunk])

    assert result.found is True
    assert len(result.citations) == 1
    assert result.citations[0].chunk_id == leave_chunk.id
    assert str(bogus_id) in result.rejected_chunk_ids
    assert "12 ngày phép" in result.answer
    assert "sao Hỏa" not in result.answer
    assert str(bogus_id) not in result.answer
