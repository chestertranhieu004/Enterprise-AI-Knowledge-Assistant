# File: tests/conftest.py
"""
Pytest configuration and isolated enterprise test harness for Phase 5 QA & Security verification.
Seeds 5 departments, 7 user personas, creates real .pdf, .docx, .xlsx, and .md documents on disk,
and ingests them via IngestionService into an isolated SQLAlchemy test database.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import uuid
import xml.sax.saxutils as saxutils
import zipfile
import zlib
from collections.abc import Generator
from pathlib import Path
from typing import Any

# Bootstrap cross-interpreter pure-Python imports and backend path before third-party imports
try:
    from tests import bootstrap_test_environment
except ImportError:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from tests import bootstrap_test_environment

bootstrap_test_environment()

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import Engine, create_engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.security import create_access_token, get_password_hash  # noqa: E402
from app.db.models import (  # noqa: E402
    Base,
    Department,
    Document,
    DocumentPermission,
    User,
)
from app.db.session import get_db  # noqa: E402
from app.ingestion.service import IngestionService  # noqa: E402
from app.main import app  # noqa: E402
from app.rag.service import RAGService  # noqa: E402
from app.schemas.common import (  # noqa: E402
    AccessLevel,
    DepartmentCode,
    DocumentStatus,
    PermissionType,
    UserRole,
)
from app.schemas.user import UserContext  # noqa: E402

# Prevent app lifespan from overwriting or re-seeding the default production engine during tests
settings.SEED_ON_STARTUP = False
TEST_PASSWORD: str = "Password123!"


# ============================================================================
# Real Enterprise Document File Generators (.pdf, .docx, .xlsx, .md)
# ============================================================================


def create_real_pdf_file(
    file_path: Path,
    pages: list[str],
    compress: bool = False,
) -> Path:
    """
    Create a spec-compliant multi-page PDF-1.4 file on disk.
    Encodes each page line as UTF-16BE hex strings (<FEFF...>) inside BT ... ET text blocks
    so both UTF-16BE hex decoders and FlateDecode stream readers extract exact Vietnamese text.
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)
    objects: list[bytes] = []

    # Object 1: Catalog
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")

    # Object 2: Pages placeholder (filled after page objects are allocated)
    page_count = max(1, len(pages))
    page_obj_indices = [3 + (i * 2) for i in range(page_count)]
    kids_refs = " ".join(f"{idx} 0 R" for idx in page_obj_indices)
    objects.append(
        f"<< /Type /Pages /Kids [{kids_refs}] /Count {page_count} >>".encode("ascii")
    )

    for i, page_text in enumerate(pages or [""]):
        page_obj_num = 3 + (i * 2)
        content_obj_num = page_obj_num + 1

        lines = [line.strip() for line in page_text.splitlines() if line.strip()]
        stream_commands: list[str] = ["BT", "/F1 11 Tf", "50 760 Td"]
        for line_idx, line in enumerate(lines):
            if line_idx > 0:
                stream_commands.append("0 -16 Td")
            utf16_hex = (b"\xfe\xff" + line.encode("utf-16-be")).hex().upper()
            stream_commands.append(f"<{utf16_hex}> Tj")
        stream_commands.append("ET")
        raw_stream = "\n".join(stream_commands).encode("ascii")

        if compress:
            compressed = zlib.compress(raw_stream)
            content_obj = (
                f"<< /Length {len(compressed)} /Filter /FlateDecode >>\nstream\n".encode(
                    "ascii"
                )
                + compressed
                + b"\nendstream"
            )
        else:
            content_obj = (
                f"<< /Length {len(raw_stream)} >>\nstream\n".encode("ascii")
                + raw_stream
                + b"\nendstream"
            )

        page_obj = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Contents {content_obj_num} 0 R >>"
        ).encode("ascii")

        objects.append(page_obj)
        objects.append(content_obj)

    pdf_chunks: list[bytes] = [b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"]
    offsets: list[int] = [0]
    current_offset = len(pdf_chunks[0])

    for obj_idx, obj_body in enumerate(objects, start=1):
        offsets.append(current_offset)
        block = f"{obj_idx} 0 obj\n".encode("ascii") + obj_body + b"\nendobj\n"
        pdf_chunks.append(block)
        current_offset += len(block)

    xref_offset = current_offset
    xref_lines = [f"xref\n0 {len(objects) + 1}\n", "0000000000 65535 f \n"]
    for off in offsets[1:]:
        xref_lines.append(f"{off:010d} 00000 n \n")
    trailer = (
        "".join(xref_lines)
        + f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        + f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode("ascii")
    pdf_chunks.append(trailer)

    file_path.write_bytes(b"".join(pdf_chunks))
    return file_path


def create_real_docx_file(
    file_path: Path,
    sections: list[tuple[str | None, list[str], list[list[str]] | None]],
) -> Path:
    """
    Create a genuine OOXML (.docx) ZIP archive on disk containing word/styles.xml
    and word/document.xml with heading styles, paragraphs, and Word tables (<w:tbl>).
    Each tuple in `sections` is: (heading_title_or_None, paragraphs_list, optional_table_rows).
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)

    content_types_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>"""

    rels_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

    styles_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:styleId="Heading1">
    <w:name w:val="Heading 1"/>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading2">
    <w:name w:val="Heading 2"/>
  </w:style>
</w:styles>"""

    body_elements: list[str] = []
    for heading_title, paragraphs, table_rows in sections:
        if heading_title:
            escaped_h = saxutils.escape(heading_title)
            body_elements.append(
                f"<w:p><w:pPr><w:pStyle w:val=\"Heading1\"/></w:pPr>"
                f"<w:r><w:t>{escaped_h}</w:t></w:r></w:p>"
            )
        for para_text in paragraphs:
            escaped_p = saxutils.escape(para_text)
            body_elements.append(f"<w:p><w:r><w:t>{escaped_p}</w:t></w:r></w:p>")
        if table_rows:
            tr_xml_list: list[str] = []
            for row in table_rows:
                tc_xml_list = [
                    f"<w:tc><w:p><w:r><w:t>{saxutils.escape(str(cell))}</w:t></w:r></w:p></w:tc>"
                    for cell in row
                ]
                tr_xml_list.append(f"<w:tr>{''.join(tc_xml_list)}</w:tr>")
            body_elements.append(f"<w:tbl>{''.join(tr_xml_list)}</w:tbl>")

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{''.join(body_elements)}</w:body>"
        "</w:document>"
    )

    with zipfile.ZipFile(file_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types_xml)
        zf.writestr("_rels/.rels", rels_xml)
        zf.writestr("word/styles.xml", styles_xml)
        zf.writestr("word/document.xml", document_xml)

    return file_path


def create_real_xlsx_file(
    file_path: Path,
    sheets: dict[str, list[list[Any]]],
) -> Path:
    """
    Create a genuine Excel (.xlsx) workbook on disk using openpyxl (with OOXML fallback).
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from openpyxl import Workbook

        wb = Workbook()
        default_sheet = wb.active
        for idx, (sheet_title, rows) in enumerate(sheets.items()):
            if idx == 0 and default_sheet is not None:
                ws = default_sheet
                ws.title = sheet_title
            else:
                ws = wb.create_sheet(title=sheet_title)
            for row in rows:
                ws.append(list(row))
        wb.save(str(file_path))
        wb.close()
        return file_path
    except Exception:
        # Pure-Python OOXML spreadsheet fallback
        with zipfile.ZipFile(file_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            sheet_entries: list[str] = []
            rel_entries: list[str] = []
            for idx, (sheet_title, rows) in enumerate(sheets.items(), start=1):
                sheet_entries.append(
                    f'<sheet name="{saxutils.escape(sheet_title)}" sheetId="{idx}" r:id="rId{idx}"/>'
                )
                rel_entries.append(
                    f'<Relationship Id="rId{idx}" '
                    f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                    f'Target="worksheets/sheet{idx}.xml"/>'
                )
                row_xmls: list[str] = []
                for r_idx, row in enumerate(rows, start=1):
                    c_xmls: list[str] = []
                    for c_idx, val in enumerate(row):
                        col_letter = chr(ord("A") + (c_idx % 26))
                        escaped_val = saxutils.escape(str(val))
                        c_xmls.append(
                            f'<c r="{col_letter}{r_idx}" t="inlineStr"><is><t>{escaped_val}</t></is></c>'
                        )
                    row_xmls.append(f'<row r="{r_idx}">{"".join(c_xmls)}</row>')
                ws_xml = (
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                    f'<sheetData>{"".join(row_xmls)}</sheetData></worksheet>'
                )
                zf.writestr(f"xl/worksheets/sheet{idx}.xml", ws_xml)

            wb_xml = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                f'<sheets>{"".join(sheet_entries)}</sheets></workbook>'
            )
            wb_rels_xml = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                f'{"".join(rel_entries)}</Relationships>'
            )
            zf.writestr("xl/workbook.xml", wb_xml)
            zf.writestr("xl/_rels/workbook.xml.rels", wb_rels_xml)
        return file_path


# ============================================================================
# Database Seeding & Ingestion Helper for Test Harness
# ============================================================================

DEPARTMENTS_SEED_SPECS: list[dict[str, str]] = [
    {
        "code": DepartmentCode.HR.value,
        "name": "Phòng Nhân sự (Human Resources)",
        "description": "Quản lý tuyển dụng, chính sách phúc lợi, nghỉ phép và lương thưởng.",
    },
    {
        "code": DepartmentCode.FINANCE.value,
        "name": "Phòng Tài chính - Kế toán (Finance)",
        "description": "Quản lý ngân sách, báo cáo tài chính, chi phí và phê duyệt thanh toán.",
    },
    {
        "code": DepartmentCode.LEGAL.value,
        "name": "Phòng Pháp chế & Tuân thủ (Legal)",
        "description": "Quản lý hợp đồng năm 2025, quy định pháp lý và bảo mật thông tin.",
    },
    {
        "code": DepartmentCode.ENGINEERING.value,
        "name": "Khối Kỹ thuật & Công nghệ (Engineering)",
        "description": "Phát triển sản phẩm, CI/CD, kiến trúc hệ thống và vận hành hạ tầng.",
    },
    {
        "code": DepartmentCode.GENERAL.value,
        "name": "Toàn công ty (General)",
        "description": "Văn bản, quy chế và chính sách nghỉ phép chung toàn doanh nghiệp.",
    },
]

USERS_SEED_SPECS: list[dict[str, str]] = [
    {
        "username": "admin",
        "email": "admin@company.vn",
        "full_name": "Nguyễn Quản Trị (System Admin)",
        "role": UserRole.ADMIN.value,
        "department": DepartmentCode.GENERAL.value,
    },
    {
        "username": "hr.manager",
        "email": "hr.manager@company.vn",
        "full_name": "Trần Thu Hà (HR Manager)",
        "role": UserRole.MANAGER.value,
        "department": DepartmentCode.HR.value,
    },
    {
        "username": "hr.employee",
        "email": "hr.employee@company.vn",
        "full_name": "Đỗ Nhân Sự (HR Employee)",
        "role": UserRole.EMPLOYEE.value,
        "department": DepartmentCode.HR.value,
    },
    {
        "username": "eng.employee",
        "email": "eng.employee@company.vn",
        "full_name": "Phạm Kỹ Thuật (Engineering Employee)",
        "role": UserRole.EMPLOYEE.value,
        "department": DepartmentCode.ENGINEERING.value,
    },
    {
        "username": "finance.manager",
        "email": "finance.manager@company.vn",
        "full_name": "Lê Minh Tài (Finance Manager)",
        "role": UserRole.MANAGER.value,
        "department": DepartmentCode.FINANCE.value,
    },
    {
        "username": "legal.manager",
        "email": "legal.manager@company.vn",
        "full_name": "Vũ Pháp Chế (Legal Manager)",
        "role": UserRole.MANAGER.value,
        "department": DepartmentCode.LEGAL.value,
    },
    {
        "username": "guest",
        "email": "guest@company.vn",
        "full_name": "Khách Thực Tập (Guest User)",
        "role": UserRole.GUEST.value,
        "department": DepartmentCode.GENERAL.value,
    },
]


def generate_enterprise_fixture_files(base_dir: Path) -> dict[str, Path]:
    """Create the 5 canonical enterprise fixture files (.pdf, .docx, .xlsx, .md) on disk."""
    base_dir.mkdir(parents=True, exist_ok=True)

    # 1. Public Leave Policy PDF (General Department)
    leave_pdf = create_real_pdf_file(
        base_dir / "Chinh_sach_nghi_phep_2026.pdf",
        pages=[
            (
                "Chính sách nghỉ phép năm 2026 của công ty (Trang 1): Nhân viên chính thức được nghỉ phép "
                "12 ngày phép hưởng nguyên lương mỗi năm dương lịch.\n"
                "Cứ mỗi 5 năm thâm niên làm việc tại công ty, nhân viên được cộng thêm "
                "1 ngày phép thâm niên.\n"
                "Đơn xin nghỉ phép hoặc nghỉ ốm cần đăng ký trên hệ thống HR trước ít nhất 2 ngày làm việc."
            ),
            (
                "Quy định thời gian làm việc tiêu chuẩn và hybrid năm 2026 (Trang 2): Nhân viên toàn thời gian làm việc "
                "từ thứ Hai đến thứ Sáu, buổi sáng từ 08:30 đến 12:00, buổi chiều từ 13:30 đến 17:30.\n"
                "Công ty hỗ trợ làm việc linh hoạt hybrid tối đa 2 ngày mỗi tuần khi có phê duyệt của quản lý."
            ),
        ],
    )

    # 2. Department Finance Payment Approval DOCX (Finance Department)
    finance_docx = create_real_docx_file(
        base_dir / "Quy_trinh_phe_duyet_thanh_toan_Finance.docx",
        sections=[
            (
                "Quy trình phê duyệt thanh toán",
                [
                    (
                        "Quy trình xin phê duyệt thanh toán chi phí và hóa đơn kế toán nội bộ năm 2026 (Mục 1): "
                        "Mọi đề nghị phê duyệt thanh toán hóa đơn chi phí dưới 50,000,000 VNĐ do "
                        "Trưởng phòng Tài chính Kế toán phê duyệt trong vòng 2 ngày làm việc. "
                        "Các khoản thanh toán ngân sách từ 50,000,000 VNĐ trở lên bắt buộc phải có "
                        "phê duyệt của Tổng Giám đốc trong 3 ngày làm việc."
                    )
                ],
                [
                    ["Hạn mức thanh toán", "Cấp phê duyệt", "Thời gian xử lý"],
                    ["Dưới 50,000,000 VNĐ", "Trưởng phòng Tài chính", "2 ngày làm việc"],
                    ["Từ 50,000,000 VNĐ trở lên", "Tổng Giám đốc", "3 ngày làm việc"],
                ],
            )
        ],
    )

    # 3. Department Legal Contract 2025 PDF (Legal Department)
    legal_pdf = create_real_pdf_file(
        base_dir / "Hop_dong_thuong_mai_nam_2025_Legal.pdf",
        pages=[
            (
                "Tài liệu liên quan đến hợp đồng năm 2025 và quy định pháp chế (Trang 1): "
                "Mọi hợp đồng thương mại năm 2025, thỏa thuận cam kết bảo mật NDA và điều khoản "
                "sở hữu trí tuệ phải được Phòng Pháp chế thẩm định pháp lý tối thiểu 5 ngày làm việc "
                "trước khi ký kết chính thức."
            )
        ],
    )

    # 4. Department Engineering CI/CD Markdown (Engineering Department)
    eng_md = base_dir / "Tieu_chuan_CI_CD_Engineering.md"
    eng_md.write_text(
        "# Quy trình Triển khai CI/CD\n"
        "Tiêu chuẩn triển khai hệ thống CI/CD khối Engineering (Mục 1): Mọi dịch vụ microservices backend "
        "sử dụng Python 3.11, FastAPI và cơ sở dữ liệu PostgreSQL 16 tích hợp pgvector. "
        "Trước khi merge vào nhánh main, toàn bộ pipeline CI phải vượt qua kiểm thử pytest "
        "với độ phủ tối thiểu 80%.\n\n"
        "# Xử lý Sự cố Production\n"
        "Quy trình ứng cứu sự cố mức độ P1 trên production (Mục 2): Kỹ sư trực on-call phải phản hồi "
        "cảnh báo trong vòng 15 phút và thực hiện rollback container nếu tỷ lệ lỗi 5xx vượt ngưỡng 1%.\n",
        encoding="utf-8",
    )

    # 5. Restricted HR Executive Salary XLSX (HR Department - Admin & HR Manager only)
    hr_xlsx = create_real_xlsx_file(
        base_dir / "Bang_luong_mat_Ban_Giam_doc_HR_2026.xlsx",
        sheets={
            "Bang_Luong_Cap_Cao": [
                [
                    "Chuc_Danh",
                    "Khung_Luong_Thang_VND",
                    "Quy_Thuong_Nam",
                    "Ghi_Chu_Bao_Mat",
                ],
                [
                    "Bậc 1 - Giám đốc Khối (Director) năm 2026",
                    "120,000,000 - 180,000,000 VNĐ/tháng",
                    "Executive Bonus Pool tối đa 6 tháng lương cơ bản",
                    "Bảng lương thưởng và đãi ngộ cấp cao mật năm 2026 (Bậc 1 đến Bậc 2) chỉ dành cho HR Manager và Admin",
                ],
                [
                    "Bậc 2 - Phó Tổng Giám đốc (VP) năm 2026",
                    "190,000,000 - 250,000,000 VNĐ/tháng",
                    "Executive Bonus Pool tối đa 8 tháng lương cơ bản",
                    "Tài liệu mật Ban Giám đốc phòng HR năm 2026",
                ],
            ]
        },
    )

    return {
        "leave_policy_pdf": leave_pdf,
        "finance_payment_docx": finance_docx,
        "legal_contract_2025_pdf": legal_pdf,
        "engineering_cicd_md": eng_md,
        "hr_restricted_salary_xlsx": hr_xlsx,
    }


def seed_and_ingest_test_database(
    db: Session,
    fixture_files: dict[str, Path],
) -> tuple[dict[str, User], dict[str, Document]]:
    """
    Seed all 5 departments, 7 user personas, and 5 enterprise documents (.pdf, .docx, .xlsx, .md),
    then ingest each document using IngestionService.ingest_document.
    """
    # 1. Seed 5 Departments
    for dept_spec in DEPARTMENTS_SEED_SPECS:
        db.add(Department(**dept_spec))
    db.flush()

    # 2. Seed 7 Users
    shared_pw_hash = get_password_hash(TEST_PASSWORD)
    users_by_username: dict[str, User] = {}
    for user_spec in USERS_SEED_SPECS:
        user_obj = User(
            id=uuid.uuid4(),
            email=user_spec["email"],
            username=user_spec["username"],
            hashed_password=shared_pw_hash,
            full_name=user_spec["full_name"],
            role=user_spec["role"],
            department=user_spec["department"],
            is_active=True,
        )
        db.add(user_obj)
        users_by_username[user_spec["username"]] = user_obj
    db.flush()

    admin_user = users_by_username["admin"]
    hr_manager = users_by_username["hr.manager"]
    finance_manager = users_by_username["finance.manager"]
    legal_manager = users_by_username["legal.manager"]

    # 3. Create Document metadata rows & permissions for the 5 fixture files
    doc_definitions: list[dict[str, Any]] = [
        {
            "key": "leave_policy_pdf",
            "title": "Chính sách Nghỉ phép & Thời gian Làm việc Toàn Công ty 2026",
            "path": fixture_files["leave_policy_pdf"],
            "file_type": "pdf",
            "department": DepartmentCode.GENERAL.value,
            "access_level": AccessLevel.PUBLIC.value,
            "allowed_roles": [
                UserRole.ADMIN.value,
                UserRole.MANAGER.value,
                UserRole.EMPLOYEE.value,
                UserRole.GUEST.value,
            ],
            "uploaded_by": admin_user.id,
            "permissions": [],
        },
        {
            "key": "finance_payment_docx",
            "title": "Quy trình Phê duyệt Thanh toán & Tạm ứng Phòng Tài chính 2026",
            "path": fixture_files["finance_payment_docx"],
            "file_type": "docx",
            "department": DepartmentCode.FINANCE.value,
            "access_level": AccessLevel.DEPARTMENT.value,
            "allowed_roles": [
                UserRole.ADMIN.value,
                UserRole.MANAGER.value,
                UserRole.EMPLOYEE.value,
            ],
            "uploaded_by": finance_manager.id,
            "permissions": [
                {
                    "user_id": None,
                    "department": DepartmentCode.FINANCE.value,
                    "role": None,
                    "permission_type": PermissionType.READ.value,
                }
            ],
        },
        {
            "key": "legal_contract_2025_pdf",
            "title": "Quy định Thẩm định Hợp đồng Thương mại năm 2025 Phòng Pháp chế",
            "path": fixture_files["legal_contract_2025_pdf"],
            "file_type": "pdf",
            "department": DepartmentCode.LEGAL.value,
            "access_level": AccessLevel.DEPARTMENT.value,
            "allowed_roles": [
                UserRole.ADMIN.value,
                UserRole.MANAGER.value,
                UserRole.EMPLOYEE.value,
            ],
            "uploaded_by": legal_manager.id,
            "permissions": [
                {
                    "user_id": None,
                    "department": DepartmentCode.LEGAL.value,
                    "role": None,
                    "permission_type": PermissionType.READ.value,
                }
            ],
        },
        {
            "key": "engineering_cicd_md",
            "title": "Tiêu chuẩn Kỹ thuật CI/CD & Vận hành Hệ thống Khối Engineering",
            "path": fixture_files["engineering_cicd_md"],
            "file_type": "md",
            "department": DepartmentCode.ENGINEERING.value,
            "access_level": AccessLevel.DEPARTMENT.value,
            "allowed_roles": [
                UserRole.ADMIN.value,
                UserRole.MANAGER.value,
                UserRole.EMPLOYEE.value,
            ],
            "uploaded_by": admin_user.id,
            "permissions": [
                {
                    "user_id": None,
                    "department": DepartmentCode.ENGINEERING.value,
                    "role": None,
                    "permission_type": PermissionType.READ.value,
                }
            ],
        },
        {
            "key": "hr_restricted_salary_xlsx",
            "title": "Bảng Lương Mật Ban Giám Đốc & Đãi Ngộ Cấp Cao HR 2026",
            "path": fixture_files["hr_restricted_salary_xlsx"],
            "file_type": "xlsx",
            "department": DepartmentCode.HR.value,
            "access_level": AccessLevel.RESTRICTED.value,
            "allowed_roles": [
                UserRole.ADMIN.value,
                UserRole.MANAGER.value,
            ],
            "uploaded_by": hr_manager.id,
            "permissions": [
                {
                    "user_id": hr_manager.id,
                    "department": DepartmentCode.HR.value,
                    "role": UserRole.MANAGER.value,
                    "permission_type": PermissionType.READ.value,
                }
            ],
        },
    ]

    ingestion_service = IngestionService()
    docs_by_key: dict[str, Document] = {}

    for spec in doc_definitions:
        path_obj: Path = spec["path"]
        raw_bytes = path_obj.read_bytes()
        doc_obj = Document(
            id=uuid.uuid4(),
            title=spec["title"],
            file_name=path_obj.name,
            file_path=str(path_obj),
            file_type=spec["file_type"],
            file_size_bytes=len(raw_bytes),
            content_hash=hashlib.sha256(raw_bytes).hexdigest(),
            department=spec["department"],
            access_level=spec["access_level"],
            allowed_roles=spec["allowed_roles"],
            uploaded_by=spec["uploaded_by"],
            status=DocumentStatus.PROCESSING.value,
            chunk_count=0,
            is_deleted=False,
        )
        db.add(doc_obj)
        db.flush()

        for perm in spec["permissions"]:
            db.add(
                DocumentPermission(
                    document_id=doc_obj.id,
                    user_id=perm["user_id"],
                    department=perm["department"],
                    role=perm["role"],
                    permission_type=perm["permission_type"],
                )
            )
        db.commit()

        result = ingestion_service.ingest_document(
            db=db,
            document_id=doc_obj.id,
            file_path=path_obj,
        )
        assert result.status == DocumentStatus.READY, (
            f"Ingestion failed for {path_obj.name}: {result.error_message}"
        )
        assert result.chunk_count >= 1
        db.refresh(doc_obj)
        docs_by_key[spec["key"]] = doc_obj

    return users_by_username, docs_by_key


# ============================================================================
# Pytest Fixtures
# ============================================================================


@pytest.fixture()
def tmp_path() -> Generator[Path, None, None]:
    """Windows sandbox-safe replacement for pytest's built-in tmp_path fixture."""
    path = Path(tempfile.mkdtemp(prefix="eka_test_tmp_"))
    yield path


@pytest.fixture(scope="session")
def fixture_files_dir() -> Generator[Path, None, None]:
    """Temporary directory holding real .pdf, .docx, .xlsx, and .md fixture files."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="eka_phase5_fixtures_"))
    settings.UPLOAD_DIR = str(tmp_dir / "uploads")
    Path(settings.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
    yield tmp_dir


@pytest.fixture(scope="session")
def fixture_files(fixture_files_dir: Path) -> dict[str, Path]:
    """Generate and return paths to all 5 real enterprise fixture documents."""
    return generate_enterprise_fixture_files(fixture_files_dir)


@pytest.fixture(scope="session")
def test_engine() -> Generator[Engine, None, None]:
    """Shared in-memory SQLite engine with StaticPool so all sessions share the seeded schema."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture(scope="session")
def _seeded_state(
    test_engine: Engine,
    fixture_files: dict[str, Path],
) -> tuple[dict[str, uuid.UUID], dict[str, uuid.UUID]]:
    """Seed the test database once per session and return user/document ID maps."""
    SessionMaker = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    with SessionMaker() as db:
        users_map, docs_map = seed_and_ingest_test_database(db, fixture_files)
        user_ids = {k: u.id for k, u in users_map.items()}
        doc_ids = {k: d.id for k, d in docs_map.items()}
    return user_ids, doc_ids


@pytest.fixture()
def db_session(
    test_engine: Engine,
    _seeded_state: tuple[dict[str, uuid.UUID], dict[str, uuid.UUID]],
) -> Generator[Session, None, None]:
    """Provide a database session connected to the seeded test engine."""
    SessionMaker = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    session = SessionMaker()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def seeded_users(
    db_session: Session,
    _seeded_state: tuple[dict[str, uuid.UUID], dict[str, uuid.UUID]],
) -> dict[str, User]:
    """Return all 7 seeded ORM User objects keyed by username."""
    user_ids, _ = _seeded_state
    return {
        username: db_session.get(User, uid)  # type: ignore[misc]
        for username, uid in user_ids.items()
    }


@pytest.fixture()
def user_contexts(seeded_users: dict[str, User]) -> dict[str, UserContext]:
    """Return Pydantic UserContext principals for all 7 seeded user personas."""
    return {
        username: UserContext.model_validate(user_obj)
        for username, user_obj in seeded_users.items()
    }


@pytest.fixture()
def auth_headers(seeded_users: dict[str, User]) -> dict[str, dict[str, str]]:
    """Return Bearer JWT Authorization headers for all 7 seeded user personas."""
    headers: dict[str, dict[str, str]] = {}
    for username, user_obj in seeded_users.items():
        token = create_access_token(
            subject=user_obj.id,
            extra_claims={
                "email": user_obj.email,
                "username": user_obj.username,
                "role": user_obj.role,
                "department": user_obj.department,
            },
        )
        headers[username] = {"Authorization": f"Bearer {token}"}
    return headers


@pytest.fixture()
def seeded_documents(
    db_session: Session,
    _seeded_state: tuple[dict[str, uuid.UUID], dict[str, uuid.UUID]],
) -> dict[str, Document]:
    """Return all 5 ingested ORM Document objects keyed by logical fixture key."""
    _, doc_ids = _seeded_state
    return {
        key: db_session.get(Document, did)  # type: ignore[misc]
        for key, did in doc_ids.items()
    }


@pytest.fixture()
def rag_service() -> RAGService:
    """Return a configured RAGService instance using deterministic Mock providers."""
    return RAGService()


@pytest.fixture()
def client(db_session: Session) -> Generator[TestClient, None, None]:
    """FastAPI TestClient wired to the isolated test db_session."""

    def _override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
