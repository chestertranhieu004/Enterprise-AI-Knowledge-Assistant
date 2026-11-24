# File: tests/test_ingestion_parsers.py
"""
Ingestion Pipeline & Format Parser Verification Suite (`backend/app/ingestion/`).

Tests:
1. `parse_pdf`: Page-by-page extraction (`Page 1`, `Page 2`), FlateDecode compression support.
2. `parse_docx`: Heading tracking (`Section 1: ...`) + pipe-delimited Markdown table extraction (`| Col1 | Col2 |`).
3. `parse_xlsx`: Sheet coordinates (`Sheet: <title> (Rows start-end)`), prepended column headers, and row batching.
4. `parse_text_or_markdown`: Markdown heading section splitting.
5. `LlamaIndexChunker`: Node splitting with overlap while preserving `page_or_sheet` and sequential `chunk_index`.
6. `IngestionService.ingest_document`: 768-dim vector generation, DB persistence, re-ingestion chunk replacement,
   and failure state recording (`status = "failed"`).
"""
from __future__ import annotations

import hashlib
import math
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Document, DocumentChunk, User
from app.ingestion.chunker import (
    LlamaIndexChunker,
    ParsedSection,
    parse_text_or_markdown,
)
from app.ingestion.parsers.docx_parser import parse_docx
from app.ingestion.parsers.pdf_parser import parse_pdf
from app.ingestion.parsers.xlsx_parser import parse_xlsx
from app.ingestion.service import IngestionService
from app.schemas.common import AccessLevel, DepartmentCode, DocumentStatus, UserRole
from tests.conftest import (
    create_real_docx_file,
    create_real_pdf_file,
    create_real_xlsx_file,
)


def test_parse_pdf_multi_page_and_flate_compressed(tmp_path: Path) -> None:
    """parse_pdf extracts 1-indexed Page coordinates from both uncompressed and FlateDecode PDFs."""
    pdf_path = create_real_pdf_file(
        tmp_path / "test_policy.pdf",
        pages=[
            "Trang 1: Quy định nghỉ phép năm 12 ngày hưởng nguyên lương.",
            "Trang 2: Quy định làm việc hybrid tối đa 2 ngày mỗi tuần.",
        ],
        compress=True,
    )
    sections = parse_pdf(pdf_path)

    assert len(sections) == 2
    assert sections[0].page_or_sheet == "Page 1"
    assert "12 ngày hưởng nguyên lương" in sections[0].text
    assert sections[0].metadata["page_number"] == 1
    assert sections[0].metadata["source_format"] == "pdf"

    assert sections[1].page_or_sheet == "Page 2"
    assert "hybrid tối đa 2 ngày" in sections[1].text
    assert sections[1].metadata["page_number"] == 2


def test_parse_docx_headings_and_pipe_delimited_markdown_table(tmp_path: Path) -> None:
    """parse_docx preserves heading hierarchy and converts Word tables to pipe-delimited Markdown rows."""
    docx_path = create_real_docx_file(
        tmp_path / "test_finance_procedure.docx",
        sections=[
            (
                "Quy trình Phê duyệt Thanh toán",
                ["Đề nghị thanh toán phải kèm hóa đơn GTGT hợp lệ."],
                [
                    ["Hạn mức", "Người phê duyệt", "SLA"],
                    ["Dưới 50 triệu", "Trưởng phòng Tài chính", "2 ngày"],
                    ["Trên 50 triệu", "Tổng Giám đốc", "3 ngày"],
                ],
            ),
            (
                "Quy định Hoàn ứng Công tác phí",
                ["Nhân viên hoàn ứng trong vòng 5 ngày làm việc sau chuyến công tác."],
                None,
            ),
        ],
    )

    sections = parse_docx(docx_path)
    assert len(sections) == 2

    assert sections[0].page_or_sheet == "Section 1: Quy trình Phê duyệt Thanh toán"
    assert "hóa đơn GTGT hợp lệ" in sections[0].text
    assert "| Hạn mức | Người phê duyệt | SLA |" in sections[0].text
    assert "| --- | --- | --- |" in sections[0].text
    assert "| Dưới 50 triệu | Trưởng phòng Tài chính | 2 ngày |" in sections[0].text

    assert sections[1].page_or_sheet == "Section 2: Quy định Hoàn ứng Công tác phí"
    assert "5 ngày làm việc" in sections[1].text


def test_parse_xlsx_sheet_coordinates_prepended_headers_and_row_batches(
    tmp_path: Path,
) -> None:
    """
    parse_xlsx groups rows into batches of `rows_per_section`, prepends column headers to every batch,
    formats cells as `Header: Value`, and sets accurate `Sheet: <name> (Rows start-end)` coordinates.
    """
    rows: list[list[str | int]] = [["Ma_NV", "Chuc_Danh", "Muc_Luong_VND", "Phong_Ban"]]
    for i in range(1, 31):
        rows.append([f"NV_{i:03d}", f"Chuyên viên bậc {i}", 20_000_000 + (i * 1_000_000), "HR"])

    xlsx_path = create_real_xlsx_file(
        tmp_path / "test_salary_batches.xlsx",
        sheets={"Bang_Luong_2026": rows},
    )

    sections = parse_xlsx(xlsx_path, rows_per_section=25)
    assert len(sections) == 2

    # Batch 1: Rows 1-25
    assert sections[0].page_or_sheet == "Sheet: Bang_Luong_2026 (Rows 1-25)"
    assert "Columns: Ma_NV | Chuc_Danh | Muc_Luong_VND | Phong_Ban" in sections[0].text
    assert "Ma_NV: NV_001 | Chuc_Danh: Chuyên viên bậc 1 | Muc_Luong_VND: 21000000 | Phong_Ban: HR" in sections[0].text
    assert "Ma_NV: NV_025" in sections[0].text

    # Batch 2: Rows 26-30 (must also have prepended column headers!)
    assert sections[1].page_or_sheet == "Sheet: Bang_Luong_2026 (Rows 26-30)"
    assert "Columns: Ma_NV | Chuc_Danh | Muc_Luong_VND | Phong_Ban" in sections[1].text
    assert "Ma_NV: NV_026" in sections[1].text
    assert "Ma_NV: NV_030" in sections[1].text


def test_llamaindex_chunker_splits_long_sections_preserving_coordinates() -> None:
    """LlamaIndexChunker splits long sections into sequential chunks while preserving page_or_sheet."""
    long_paragraph = " ".join(
        f"Câu quy định số {i}: Nhân viên tuân thủ quy trình bảo mật thông tin doanh nghiệp."
        for i in range(1, 35)
    )
    sections = [
        ParsedSection(
            text=long_paragraph,
            page_or_sheet="Page 1",
            metadata={"page_number": 1, "source_format": "pdf"},
        ),
        ParsedSection(
            text="Nội dung ngắn tại trang 2 về chính sách nghỉ phép 12 ngày.",
            page_or_sheet="Page 2",
            metadata={"page_number": 2, "source_format": "pdf"},
        ),
    ]

    chunker = LlamaIndexChunker(chunk_size=80, chunk_overlap=16)
    chunks = chunker.split_sections(sections, file_name="Quy_che_bao_mat.pdf")

    assert len(chunks) >= 4
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))

    page1_chunks = [c for c in chunks if c.page_or_sheet == "Page 1"]
    page2_chunks = [c for c in chunks if c.page_or_sheet == "Page 2"]
    assert len(page1_chunks) >= 3
    assert len(page2_chunks) == 1
    assert all(c.token_count > 0 for c in chunks)
    assert all(c.metadata_json["file_name"] == "Quy_che_bao_mat.pdf" for c in chunks)


def test_ingestion_service_end_to_end_and_failure_handling(
    db_session: Session,
    seeded_users: dict[str, User],
    tmp_path: Path,
) -> None:
    """
    IngestionService.ingest_document parses, chunks, embeds (768-dim L2-normalized),
    persists DocumentChunk rows, and sets status='failed' if a file is missing.
    """
    md_file = tmp_path / "So_tay_an_toan_thong_tin.md"
    md_file.write_text(
        "# Chính sách Mật khẩu\n"
        "Mật khẩu tài khoản nội bộ phải có tối thiểu 12 ký tự và thay đổi định kỳ mỗi 90 ngày.\n\n"
        "# Phân quyền Truy cập\n"
        "Mọi truy cập tài liệu mật đều được kiểm tra RBAC trước khi truy xuất vector.\n",
        encoding="utf-8",
    )
    raw_bytes = md_file.read_bytes()

    md_sections = parse_text_or_markdown(md_file)
    assert len(md_sections) == 2
    assert md_sections[0].page_or_sheet == "Section 1: Chính sách Mật khẩu"
    assert md_sections[1].page_or_sheet == "Section 2: Phân quyền Truy cập"

    doc = Document(
        id=uuid.uuid4(),
        title="Sổ tay An toàn Thông tin 2026",
        file_name=md_file.name,
        file_path=str(md_file),
        file_type="md",
        file_size_bytes=len(raw_bytes),
        content_hash=hashlib.sha256(raw_bytes).hexdigest(),
        department=DepartmentCode.GENERAL.value,
        access_level=AccessLevel.PUBLIC.value,
        allowed_roles=[UserRole.ADMIN.value, UserRole.MANAGER.value, UserRole.EMPLOYEE.value],
        uploaded_by=seeded_users["admin"].id,
        status=DocumentStatus.PROCESSING.value,
        chunk_count=0,
    )
    db_session.add(doc)
    db_session.commit()

    service = IngestionService()
    res = service.ingest_document(db=db_session, document_id=doc.id, file_path=md_file)
    assert res.status == DocumentStatus.READY
    assert res.chunk_count == 2
    assert res.error_message is None

    chunks = db_session.scalars(
        select(DocumentChunk)
        .where(DocumentChunk.document_id == doc.id)
        .order_by(DocumentChunk.chunk_index.asc())
    ).all()
    assert len(chunks) == 2
    for chunk in chunks:
        vec = list(chunk.embedding)
        assert len(vec) == 768
        l2_norm = math.sqrt(sum(v * v for v in vec))
        assert abs(l2_norm - 1.0) < 1e-3

    # Verify failure handling when file does not exist
    missing_doc = Document(
        id=uuid.uuid4(),
        title="Broken Document",
        file_name="missing_file.pdf",
        file_path=str(tmp_path / "does_not_exist.pdf"),
        file_type="pdf",
        file_size_bytes=100,
        content_hash="0" * 64,
        department=DepartmentCode.GENERAL.value,
        access_level=AccessLevel.PUBLIC.value,
        allowed_roles=[UserRole.ADMIN.value],
        uploaded_by=seeded_users["admin"].id,
        status=DocumentStatus.PROCESSING.value,
        chunk_count=0,
    )
    db_session.add(missing_doc)
    db_session.commit()

    fail_res = service.ingest_document(db=db_session, document_id=missing_doc.id)
    assert fail_res.status == DocumentStatus.FAILED
    assert fail_res.chunk_count == 0
    assert fail_res.error_message is not None
    db_session.refresh(missing_doc)
    assert missing_doc.status == DocumentStatus.FAILED.value
    assert missing_doc.error_message is not None
