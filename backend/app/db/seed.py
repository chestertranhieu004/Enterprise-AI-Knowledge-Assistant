# File: backend/app/db/seed.py
from datetime import datetime, timezone
import hashlib
import logging
import math
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import get_password_hash
from app.db.models import (
    Department,
    Document,
    DocumentChunk,
    DocumentPermission,
    User,
)
from app.schemas.common import (
    AccessLevel,
    DepartmentCode,
    DocumentStatus,
    PermissionType,
    UserRole,
)

logger = logging.getLogger(__name__)

DEFAULT_DEMO_PASSWORD = "Password123!"

DEFAULT_DEPARTMENTS: list[dict[str, str]] = [
    {
        "code": DepartmentCode.HR.value,
        "name": "Phòng Nhân sự (Human Resources)",
        "description": "Quản lý tuyển dụng, chính sách phúc lợi, nghỉ phép và lương thưởng.",
    },
    {
        "code": DepartmentCode.FINANCE.value,
        "name": "Phòng Tài chính - Kế toán (Finance)",
        "description": "Quản lý ngân sách, báo cáo tài chính, chi phí và thanh toán.",
    },
    {
        "code": DepartmentCode.LEGAL.value,
        "name": "Phòng Pháp chế & Tuân thủ (Legal)",
        "description": "Quản lý hợp đồng, quy định pháp lý và bảo mật thông tin.",
    },
    {
        "code": DepartmentCode.ENGINEERING.value,
        "name": "Khối Kỹ thuật & Công nghệ (Engineering)",
        "description": "Phát triển sản phẩm, kiến trúc hệ thống, vận hành hạ tầng và bảo mật kỹ thuật.",
    },
    {
        "code": DepartmentCode.GENERAL.value,
        "name": "Toàn công ty (General)",
        "description": "Văn bản, quy chế và thông báo chung áp dụng cho toàn doanh nghiệp.",
    },
]

DEFAULT_USERS: list[dict[str, Any]] = [
    {
        "email": "admin@company.vn",
        "username": "admin",
        "full_name": "Nguyễn Quản Trị (System Admin)",
        "role": UserRole.ADMIN.value,
        "department": DepartmentCode.GENERAL.value,
    },
    {
        "email": "hr.manager@company.vn",
        "username": "hr.manager",
        "full_name": "Trần Thu Hà (HR Manager)",
        "role": UserRole.MANAGER.value,
        "department": DepartmentCode.HR.value,
    },
    {
        "email": "finance.manager@company.vn",
        "username": "finance.manager",
        "full_name": "Lê Minh Tài (Finance Manager)",
        "role": UserRole.MANAGER.value,
        "department": DepartmentCode.FINANCE.value,
    },
    {
        "email": "eng.employee@company.vn",
        "username": "eng.employee",
        "full_name": "Phạm Kỹ Thuật (Engineering Employee)",
        "role": UserRole.EMPLOYEE.value,
        "department": DepartmentCode.ENGINEERING.value,
    },
    {
        "email": "guest@company.vn",
        "username": "guest",
        "full_name": "Khách Thực Tập (Guest User)",
        "role": UserRole.GUEST.value,
        "department": DepartmentCode.GENERAL.value,
    },
    {
        "email": "hr.employee@company.vn",
        "username": "hr.employee",
        "full_name": "Đỗ Nhân Sự (HR Employee)",
        "role": UserRole.EMPLOYEE.value,
        "department": DepartmentCode.HR.value,
    },
    {
        "email": "legal.manager@company.vn",
        "username": "legal.manager",
        "full_name": "Vũ Pháp Chế (Legal Manager)",
        "role": UserRole.MANAGER.value,
        "department": DepartmentCode.LEGAL.value,
    },
]


def _fallback_deterministic_embedding(text: str, dim: int = 768) -> list[float]:
    """Deterministic L2-normalized 768-dim fallback vector if app.rag is not yet loaded."""
    vec = [0.0] * dim
    tokens = text.lower().split()
    if not tokens:
        vec[0] = 1.0
        return vec
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        for i in range(0, len(digest), 4):
            idx = int.from_bytes(digest[i : i + 2], "big") % dim
            sign = 1.0 if (digest[i + 2] % 2 == 0) else -1.0
            vec[idx] += sign * (1.0 + (digest[i + 3] / 255.0))
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 6) for v in vec]


def _embed_seed_texts(texts: list[str]) -> tuple[list[list[float]], str, str]:
    """Use configured EmbeddingProvider from app.rag if available, else fallback."""
    try:
        from app.rag.providers.factory import get_embedding_provider

        provider = get_embedding_provider()
        vectors = provider.embed_texts(texts)
        return (
            vectors,
            getattr(provider, "provider_name", "mock"),
            getattr(provider, "model_name", "mock-768"),
        )
    except Exception:
        return (
            [_fallback_deterministic_embedding(t) for t in texts],
            "mock",
            "mock-768",
        )


def _reindex_mismatched_documents(db: Session) -> int:
    """
    Re-embed chunks for any existing ready, non-deleted Document whose
    `embedding_provider` differs from the currently active embedding provider
    (e.g. after switching EMBEDDING_PROVIDER between mock / gemini / openai).

    Wrapped safely so an invalid/missing API key or offline network logs a clear
    warning and leaves existing vectors untouched without crashing startup.
    """
    try:
        from app.rag.providers.factory import get_embedding_provider

        provider = get_embedding_provider()
    except Exception as exc:
        logger.warning(
            "Skipping auto-reindex: active embedding provider could not be initialized (%s).",
            exc,
        )
        return 0

    active_provider = str(getattr(provider, "provider_name", "mock"))
    active_model = str(getattr(provider, "model_name", f"{active_provider}-768"))

    mismatched_docs = db.scalars(
        select(Document).where(
            Document.status == DocumentStatus.READY.value,
            Document.is_deleted.is_(False),
            Document.embedding_provider != active_provider,
        )
    ).all()

    if not mismatched_docs:
        return 0

    logger.info(
        "Auto-reindexing %d ready document(s) for active embedding provider '%s' (%s).",
        len(mismatched_docs),
        active_provider,
        active_model,
    )

    reindexed_count = 0
    for doc in mismatched_docs:
        chunks = db.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == doc.id)
            .order_by(DocumentChunk.chunk_index.asc())
        ).all()

        if not chunks:
            doc.embedding_provider = active_provider
            doc.embedding_model = active_model
            doc.updated_at = datetime.now(timezone.utc)
            reindexed_count += 1
            continue

        chunk_texts = [chunk.content for chunk in chunks]
        try:
            new_vectors = provider.embed_texts(chunk_texts)
            if len(new_vectors) != len(chunks):
                raise ValueError(
                    f"Embedding count mismatch for document {doc.id}: "
                    f"expected {len(chunks)}, got {len(new_vectors)}"
                )
        except Exception as exc:
            logger.warning(
                "Auto-reindex aborted for document '%s' (%s) with provider '%s': %s. "
                "Existing embeddings preserved so startup continues safely.",
                doc.file_name,
                doc.id,
                active_provider,
                exc,
            )
            break

        for chunk_obj, new_vec in zip(chunks, new_vectors):
            chunk_obj.embedding = new_vec

        doc.embedding_provider = active_provider
        doc.embedding_model = active_model
        doc.updated_at = datetime.now(timezone.utc)
        reindexed_count += 1

    return reindexed_count


def seed_database(db: Session) -> dict[str, int]:
    """
    Idempotently seed default departments, demo users across roles/departments,
    and initial sample documents (public, department, restricted) matching docs/ERD.md.
    Also auto-reindexes existing ready documents whenever the active embedding provider changes.
    """
    created_counts = {
        "departments": 0,
        "users": 0,
        "documents": 0,
        "reindexed_documents": 0,
    }

    # 1. Seed Departments
    existing_dept_codes = set(db.scalars(select(Department.code)).all())
    for dept_data in DEFAULT_DEPARTMENTS:
        if dept_data["code"] not in existing_dept_codes:
            db.add(Department(**dept_data))
            created_counts["departments"] += 1
    db.flush()

    # 2. Seed Users
    users_by_email: dict[str, User] = {
        u.email: u for u in db.scalars(select(User)).all()
    }
    shared_password_hash: str | None = None
    for user_data in DEFAULT_USERS:
        email = user_data["email"]
        if email not in users_by_email:
            if shared_password_hash is None:
                shared_password_hash = get_password_hash(DEFAULT_DEMO_PASSWORD)
            user_obj = User(
                email=email,
                username=user_data["username"],
                hashed_password=shared_password_hash,
                full_name=user_data["full_name"],
                role=user_data["role"],
                department=user_data["department"],
                is_active=True,
            )
            db.add(user_obj)
            users_by_email[email] = user_obj
            created_counts["users"] += 1
    db.flush()

    # 3. Seed Sample Documents covering Public, Department, and Restricted ACL tiers
    existing_doc_names = set(db.scalars(select(Document.file_name)).all())
    admin_user = users_by_email.get("admin@company.vn")
    hr_manager = users_by_email.get("hr.manager@company.vn")

    if admin_user is not None and hr_manager is not None:
        sample_docs: list[dict[str, Any]] = [
            {
                "title": "Quy chế làm việc & Nghỉ phép chung 2026",
                "file_name": "Quy_che_lam_viec_chung.pdf",
                "file_path": "./data/uploads/seed_Quy_che_lam_viec_chung.pdf",
                "file_type": "pdf",
                "file_size_bytes": 148200,
                "department": DepartmentCode.GENERAL.value,
                "access_level": AccessLevel.PUBLIC.value,
                "allowed_roles": [
                    UserRole.ADMIN.value,
                    UserRole.MANAGER.value,
                    UserRole.EMPLOYEE.value,
                    UserRole.GUEST.value,
                ],
                "uploaded_by": admin_user.id,
                "chunks": [
                    {
                        "page_or_sheet": "Page 1",
                        "content": (
                            "Quy định thời gian làm việc tiêu chuẩn: Nhân viên toàn thời gian làm việc "
                            "từ thứ Hai đến thứ Sáu, buổi sáng từ 08:30 đến 12:00, buổi chiều từ 13:30 đến 17:30. "
                            "Công ty hỗ trợ làm việc linh hoạt (hybrid) tối đa 2 ngày mỗi tuần khi có phê duyệt của quản lý."
                        ),
                    },
                    {
                        "page_or_sheet": "Page 4",
                        "content": (
                            "Chính sách nghỉ phép năm: Nhân viên chính thức được nghỉ phép năm 12 ngày hưởng nguyên lương "
                            "mỗi năm dương lịch. Cứ mỗi 5 năm thâm niên làm việc tại công ty, nhân viên được cộng thêm "
                            "1 ngày phép thâm niên. Đơn xin nghỉ phép cần đăng ký trên hệ thống HR trước ít nhất 2 ngày làm việc."
                        ),
                    },
                ],
                "permissions": [],
            },
            {
                "title": "Hướng dẫn Kỹ thuật & Vận hành Hệ thống Microservices",
                "file_name": "Huong_dan_ky_thuat_he_thong.docx",
                "file_path": "./data/uploads/seed_Huong_dan_ky_thuat_he_thong.docx",
                "file_type": "docx",
                "file_size_bytes": 215400,
                "department": DepartmentCode.ENGINEERING.value,
                "access_level": AccessLevel.DEPARTMENT.value,
                "allowed_roles": [
                    UserRole.ADMIN.value,
                    UserRole.MANAGER.value,
                    UserRole.EMPLOYEE.value,
                ],
                "uploaded_by": admin_user.id,
                "chunks": [
                    {
                        "page_or_sheet": "Section 1: Quy trình Triển khai CI/CD",
                        "content": (
                            "Tiêu chuẩn triển khai hệ thống Engineering: Mọi dịch vụ backend sử dụng Python 3.11, FastAPI "
                            "và cơ sở dữ liệu PostgreSQL 16 tích hợp pgvector. Trước khi merge vào nhánh main, toàn bộ "
                            "pipeline CI phải vượt qua kiểm thử pytest với độ phủ tối thiểu 80% và kiểm tra bảo mật RBAC."
                        ),
                    },
                    {
                        "page_or_sheet": "Section 2: Xử lý Sự cố Production",
                        "content": (
                            "Quy trình ứng cứu sự cố mức độ P1 của khối Engineering: Kỹ sư trực on-call phải phản hồi "
                            "cảnh báo trong vòng 15 phút, khởi tạo kênh incident và thực hiện rollback container nếu "
                            "tỷ lệ lỗi 5xx vượt quá ngưỡng 1% trong 5 phút liên tiếp."
                        ),
                    },
                ],
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
                "title": "Chính sách Lương Thưởng & Đãi ngộ Cấp cao HR 2026",
                "file_name": "Chinh_sach_luong_thuong_cap_cao_HR.xlsx",
                "file_path": "./data/uploads/seed_Chinh_sach_luong_thuong_cap_cao_HR.xlsx",
                "file_type": "xlsx",
                "file_size_bytes": 96400,
                "department": DepartmentCode.HR.value,
                "access_level": AccessLevel.RESTRICTED.value,
                "allowed_roles": [
                    UserRole.ADMIN.value,
                    UserRole.MANAGER.value,
                ],
                "uploaded_by": hr_manager.id,
                "chunks": [
                    {
                        "page_or_sheet": "Sheet: Bang_Luong_Cap_Cao (Rows 1-15)",
                        "content": (
                            "Bảng khung lương và quỹ thưởng điều hành cấp cao năm 2026 (Tài liệu Mật - Chỉ dành cho HR Manager & Admin): "
                            "Khung lương Giám đốc Khối (Director) từ 120.000.000 VNĐ đến 180.000.000 VNĐ/tháng; "
                            "Thưởng hiệu quả kinh doanh cuối năm (Executive Bonus Pool) tối đa 6 tháng lương cơ bản."
                        ),
                    }
                ],
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

        for doc_spec in sample_docs:
            if doc_spec["file_name"] in existing_doc_names:
                continue

            chunk_texts = [c["content"] for c in doc_spec["chunks"]]
            vectors, emb_provider, emb_model = _embed_seed_texts(chunk_texts)
            content_hash = hashlib.sha256(
                "\n".join(chunk_texts).encode("utf-8")
            ).hexdigest()

            doc_obj = Document(
                title=doc_spec["title"],
                file_name=doc_spec["file_name"],
                file_path=doc_spec["file_path"],
                file_type=doc_spec["file_type"],
                file_size_bytes=doc_spec["file_size_bytes"],
                content_hash=content_hash,
                department=doc_spec["department"],
                access_level=doc_spec["access_level"],
                allowed_roles=doc_spec["allowed_roles"],
                uploaded_by=doc_spec["uploaded_by"],
                status=DocumentStatus.READY.value,
                chunk_count=len(doc_spec["chunks"]),
                embedding_provider=emb_provider,
                embedding_model=emb_model,
                is_deleted=False,
            )
            db.add(doc_obj)
            db.flush()

            for perm_spec in doc_spec["permissions"]:
                db.add(
                    DocumentPermission(
                        document_id=doc_obj.id,
                        user_id=perm_spec["user_id"],
                        department=perm_spec["department"],
                        role=perm_spec["role"],
                        permission_type=perm_spec["permission_type"],
                    )
                )

            for idx, (chunk_spec, vector) in enumerate(zip(doc_spec["chunks"], vectors)):
                token_est = max(1, len(chunk_spec["content"].split()))
                db.add(
                    DocumentChunk(
                        document_id=doc_obj.id,
                        chunk_index=idx,
                        page_or_sheet=chunk_spec["page_or_sheet"],
                        content=chunk_spec["content"],
                        token_count=token_est,
                        metadata_json={
                            "file_name": doc_obj.file_name,
                            "page_or_sheet": chunk_spec["page_or_sheet"],
                            "department": doc_obj.department,
                            "access_level": doc_obj.access_level,
                        },
                        embedding=vector,
                    )
                )

            created_counts["documents"] += 1

    db.flush()

    # 4. Auto-reindex existing ready documents if the active embedding provider changed
    created_counts["reindexed_documents"] = _reindex_mismatched_documents(db)

    db.commit()
    return created_counts
