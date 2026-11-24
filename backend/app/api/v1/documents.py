# File: backend/app/api/v1/documents.py
import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from sqlalchemy import String, and_, cast, delete, exists, func, literal, or_, select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.sql.elements import ColumnElement

from app.api.deps import get_current_user_context, require_roles
from app.core.config import settings
from app.core.exceptions import (
    AppException,
    ForbiddenException,
    NotFoundException,
    PayloadTooLargeException,
)
from app.db.models import (
    AuditLog,
    Department,
    Document,
    DocumentChunk,
    DocumentPermission,
    User,
)
from app.db.session import get_db
from app.schemas.common import (
    ALLOWED_FILE_EXTENSIONS,
    AccessLevel,
    DepartmentCode,
    DocumentStatus,
    PermissionType,
    UserRole,
)
from app.schemas.document import (
    DocumentACLUpdateRequest,
    DocumentDeleteResponse,
    DocumentPermissionEntry,
    DocumentRead,
)
from app.schemas.user import UserContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["Documents"])


def _build_document_read_rbac_filter(
    db: Session,
    user: UserContext,
) -> ColumnElement[bool]:
    """
    Build the SQL WHERE clause predicate enforcing Scenario 3 Pre-Retrieval RBAC + Department ABAC.
    Executed inside PostgreSQL (or SQLite fallback) before rows are fetched into Python memory.
    """
    user_role_str = user.role.value if isinstance(user.role, UserRole) else str(user.role)
    user_dept_str = (
        user.department.value
        if isinstance(user.department, DepartmentCode)
        else str(user.department)
    )

    if user_role_str == UserRole.ADMIN.value:
        return Document.is_deleted.is_(False)

    dialect_name = db.get_bind().dialect.name
    if dialect_name == "postgresql":
        role_allowed_expr: ColumnElement[bool] = (
            literal(user_role_str) == func.any(Document.allowed_roles)
        )
    else:
        role_allowed_expr = cast(Document.allowed_roles, String).like(
            f'%"{user_role_str}"%'
        )

    perm_exists_expr = exists(
        select(1).where(
            DocumentPermission.document_id == Document.id,
            DocumentPermission.permission_type.in_(
                [PermissionType.READ.value, PermissionType.MANAGE.value]
            ),
            or_(
                DocumentPermission.user_id == user.id,
                and_(
                    DocumentPermission.department == user_dept_str,
                    or_(
                        DocumentPermission.role.is_(None),
                        DocumentPermission.role == user_role_str,
                    ),
                ),
                and_(
                    DocumentPermission.department.is_(None),
                    DocumentPermission.role == user_role_str,
                ),
            ),
        )
    )

    return and_(
        Document.is_deleted.is_(False),
        or_(
            Document.uploaded_by == user.id,
            and_(
                role_allowed_expr,
                or_(
                    Document.access_level == AccessLevel.PUBLIC.value,
                    and_(
                        Document.access_level == AccessLevel.DEPARTMENT.value,
                        Document.department == user_dept_str,
                    ),
                    perm_exists_expr,
                ),
            ),
        ),
    )


def _user_can_manage_document(user: UserContext, doc: Document) -> bool:
    """Check whether a user who is already authorized to view `doc` can modify its ACL or delete it."""
    if user.role == UserRole.ADMIN:
        return True
    if doc.uploaded_by == user.id:
        return True
    if user.role == UserRole.MANAGER and user.department.value == doc.department:
        return True
    for perm in doc.permissions or []:
        if perm.permission_type == PermissionType.MANAGE.value:
            if perm.user_id == user.id:
                return True
            if perm.department == user.department.value and (
                perm.role is None or perm.role == user.role.value
            ):
                return True
    return False


def _parse_allowed_roles(raw_roles: str) -> list[str]:
    """Parse comma-separated or JSON list of UserRole strings."""
    cleaned = (raw_roles or "").strip()
    if not cleaned:
        return [UserRole.ADMIN.value, UserRole.MANAGER.value, UserRole.EMPLOYEE.value]

    items: list[str]
    if cleaned.startswith("["):
        try:
            parsed = json.loads(cleaned)
            if not isinstance(parsed, list):
                raise ValueError("allowed_roles JSON must be an array")
            items = [str(x).strip() for x in parsed if str(x).strip()]
        except Exception as exc:
            raise AppException(
                detail=f"Invalid allowed_roles JSON format: {exc}",
                status_code=status.HTTP_400_BAD_REQUEST,
                error_code="INVALID_ALLOWED_ROLES",
            ) from exc
    else:
        items = [part.strip() for part in cleaned.split(",") if part.strip()]

    valid_values = {r.value for r in UserRole}
    normalized: list[str] = []
    for item in items:
        if item not in valid_values:
            raise AppException(
                detail=f"Invalid role '{item}' in allowed_roles. Valid roles: {sorted(valid_values)}",
                status_code=status.HTTP_400_BAD_REQUEST,
                error_code="INVALID_ALLOWED_ROLES",
            )
        if item not in normalized:
            normalized.append(item)

    if not normalized:
        normalized = [UserRole.ADMIN.value]
    return normalized


def _parse_permissions_json(
    permissions_json: str | None,
) -> list[DocumentPermissionEntry]:
    if not permissions_json or not permissions_json.strip():
        return []
    try:
        raw_list = json.loads(permissions_json)
        if not isinstance(raw_list, list):
            raise ValueError("permissions_json must be a JSON array")
        return [DocumentPermissionEntry.model_validate(item) for item in raw_list]
    except Exception as exc:
        raise AppException(
            detail=f"Invalid permissions_json payload: {exc}",
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="INVALID_PERMISSIONS_JSON",
        ) from exc


def _sanitize_filename(filename: str) -> str:
    base = Path(filename).name
    sanitized = re.sub(r"[^A-Za-z0-9._-\u00C0-\u1EF9 ]+", "_", base).strip()
    return sanitized or "document.bin"


@router.post(
    "/upload",
    response_model=DocumentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload and ingest a document with RBAC & Department ACL metadata",
)
def upload_document(
    request: Request,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    department: DepartmentCode = Form(default=DepartmentCode.GENERAL),
    access_level: AccessLevel = Form(default=AccessLevel.DEPARTMENT),
    allowed_roles: str = Form(default="admin,manager,employee"),
    permissions_json: str | None = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.MANAGER)),
) -> DocumentRead:
    original_filename = _sanitize_filename(file.filename or "")
    ext = Path(original_filename).suffix.lower().lstrip(".")
    if ext not in ALLOWED_FILE_EXTENSIONS:
        raise AppException(
            detail=f"Unsupported file format '.{ext}'. Allowed formats: {sorted(ALLOWED_FILE_EXTENSIONS)}",
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="UNSUPPORTED_FILE_FORMAT",
        )

    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    content_bytes = file.file.read(max_bytes + 1)
    if len(content_bytes) > max_bytes:
        raise PayloadTooLargeException(
            detail=f"File exceeds {settings.MAX_UPLOAD_SIZE_MB}MB maximum size limit"
        )
    if len(content_bytes) == 0:
        raise AppException(
            detail="Uploaded file is empty",
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="EMPTY_FILE",
        )

    parsed_roles = _parse_allowed_roles(allowed_roles)
    parsed_permissions = _parse_permissions_json(permissions_json)

    # Ensure department row exists
    dept_row = db.scalars(
        select(Department).where(Department.code == department.value)
    ).first()
    if dept_row is None:
        db.add(Department(code=department.value, name=f"Department {department.value}"))
        db.flush()

    doc_id = uuid.uuid4()
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved_file_path = upload_dir / f"{doc_id}_{original_filename}"
    saved_file_path.write_bytes(content_bytes)

    content_hash = hashlib.sha256(content_bytes).hexdigest()
    doc_title = (title or "").strip() or original_filename

    doc = Document(
        id=doc_id,
        title=doc_title,
        file_name=original_filename,
        file_path=str(saved_file_path),
        file_type=ext,
        file_size_bytes=len(content_bytes),
        content_hash=content_hash,
        department=department.value,
        access_level=access_level.value,
        allowed_roles=parsed_roles,
        uploaded_by=current_user.id,
        status=DocumentStatus.PROCESSING.value,
        chunk_count=0,
        embedding_provider=settings.EMBEDDING_PROVIDER,
        embedding_model=f"{settings.EMBEDDING_PROVIDER}-{settings.EMBEDDING_DIM}",
        is_deleted=False,
    )
    db.add(doc)
    db.flush()

    if parsed_permissions:
        for perm_entry in parsed_permissions:
            if (
                perm_entry.user_id is None
                and perm_entry.department is None
                and perm_entry.role is None
            ):
                continue
            db.add(
                DocumentPermission(
                    document_id=doc.id,
                    user_id=perm_entry.user_id,
                    department=(
                        perm_entry.department.value
                        if perm_entry.department is not None
                        else None
                    ),
                    role=(
                        perm_entry.role.value
                        if perm_entry.role is not None
                        else None
                    ),
                    permission_type=perm_entry.permission_type.value,
                )
            )
    elif access_level == AccessLevel.RESTRICTED:
        # Grant default read/manage ACL to uploading user and department managers
        db.add(
            DocumentPermission(
                document_id=doc.id,
                user_id=current_user.id,
                department=department.value,
                role=UserRole.MANAGER.value,
                permission_type=PermissionType.READ.value,
            )
        )

    db.commit()
    db.refresh(doc)

    # Lazily invoke IngestionService (Phase 3 module owned by @ingestion)
    try:
        from app.ingestion.service import IngestionService

        ingestion_service = IngestionService()
        ingestion_service.ingest_document(
            db=db,
            document_id=doc.id,
            file_path=saved_file_path,
        )
        db.refresh(doc)
    except ImportError:
        logger.warning(
            "app.ingestion.service.IngestionService not available yet; document %s stored in '%s' state.",
            doc.id,
            doc.status,
        )
    except Exception as exc:
        logger.exception("Error during document ingestion for %s: %s", doc.id, exc)
        doc.status = DocumentStatus.FAILED.value
        doc.error_message = str(exc)
        db.commit()
        db.refresh(doc)

    client_ip = request.client.host if request.client else None
    db.add(
        AuditLog(
            user_id=current_user.id,
            action="DOCUMENT_UPLOAD",
            resource_type="document",
            resource_id=str(doc.id),
            details_json={
                "file_name": doc.file_name,
                "department": doc.department,
                "access_level": doc.access_level,
                "allowed_roles": doc.allowed_roles,
                "status": doc.status,
                "chunk_count": doc.chunk_count,
            },
            ip_address=client_ip,
        )
    )
    db.commit()

    stmt = (
        select(Document)
        .options(selectinload(Document.permissions))
        .where(Document.id == doc.id)
    )
    loaded_doc = db.scalars(stmt).first() or doc
    return DocumentRead.model_validate(loaded_doc)


@router.get(
    "",
    response_model=list[DocumentRead],
    summary="List documents accessible to the current user under Pre-Retrieval RBAC rules",
)
def list_documents(
    department: DepartmentCode | None = Query(default=None),
    access_level: AccessLevel | None = Query(default=None),
    db: Session = Depends(get_db),
    user_ctx: UserContext = Depends(get_current_user_context),
) -> list[DocumentRead]:
    stmt = (
        select(Document)
        .options(selectinload(Document.permissions))
        .where(_build_document_read_rbac_filter(db, user_ctx))
    )
    if department is not None:
        stmt = stmt.where(Document.department == department.value)
    if access_level is not None:
        stmt = stmt.where(Document.access_level == access_level.value)

    stmt = stmt.order_by(Document.created_at.desc())
    docs = db.scalars(stmt).all()
    return [DocumentRead.model_validate(d) for d in docs]


@router.get(
    "/{doc_id}",
    response_model=DocumentRead,
    summary="Get document details if authorized under Pre-Retrieval RBAC rules (else 404)",
)
def get_document(
    doc_id: uuid.UUID,
    db: Session = Depends(get_db),
    user_ctx: UserContext = Depends(get_current_user_context),
) -> DocumentRead:
    stmt = (
        select(Document)
        .options(selectinload(Document.permissions))
        .where(
            Document.id == doc_id,
            _build_document_read_rbac_filter(db, user_ctx),
        )
    )
    doc = db.scalars(stmt).first()
    if doc is None:
        # Scenario 3 Zero-Leakage: return 404 (never 403 or document title/filename)
        raise NotFoundException(
            detail="Document not found",
            error_code="DOCUMENT_NOT_FOUND",
        )
    return DocumentRead.model_validate(doc)


@router.patch(
    "/{doc_id}/acl",
    response_model=DocumentRead,
    summary="Update a document's department, access_level, allowed_roles, and explicit permissions",
)
def update_document_acl(
    doc_id: uuid.UUID,
    payload: DocumentACLUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    user_ctx: UserContext = Depends(get_current_user_context),
) -> DocumentRead:
    # Step 1: Enforce Scenario 3 SQL RBAC read filter first so unauthorized users get 404 Not Found
    stmt = (
        select(Document)
        .options(selectinload(Document.permissions))
        .where(
            Document.id == doc_id,
            _build_document_read_rbac_filter(db, user_ctx),
        )
    )
    doc = db.scalars(stmt).first()
    if doc is None:
        raise NotFoundException(
            detail="Document not found",
            error_code="DOCUMENT_NOT_FOUND",
        )

    # Step 2: Among users authorized to view the document, verify manage privileges
    if not _user_can_manage_document(user_ctx, doc):
        raise ForbiddenException(
            detail="Insufficient permissions to modify document ACL",
            error_code="DOCUMENT_ACL_FORBIDDEN",
        )

    changes: dict[str, Any] = {}
    if payload.department is not None:
        dept_row = db.scalars(
            select(Department).where(Department.code == payload.department.value)
        ).first()
        if dept_row is None:
            db.add(
                Department(
                    code=payload.department.value,
                    name=f"Department {payload.department.value}",
                )
            )
            db.flush()
        doc.department = payload.department.value
        changes["department"] = doc.department

    if payload.access_level is not None:
        doc.access_level = payload.access_level.value
        changes["access_level"] = doc.access_level

    if payload.allowed_roles is not None:
        doc.allowed_roles = [r.value for r in payload.allowed_roles]
        changes["allowed_roles"] = doc.allowed_roles

    if payload.permissions is not None:
        db.execute(
            delete(DocumentPermission).where(DocumentPermission.document_id == doc.id)
        )
        new_perms: list[DocumentPermission] = []
        for perm_entry in payload.permissions:
            if (
                perm_entry.user_id is None
                and perm_entry.department is None
                and perm_entry.role is None
            ):
                continue
            perm_obj = DocumentPermission(
                document_id=doc.id,
                user_id=perm_entry.user_id,
                department=(
                    perm_entry.department.value
                    if perm_entry.department is not None
                    else None
                ),
                role=(
                    perm_entry.role.value if perm_entry.role is not None else None
                ),
                permission_type=perm_entry.permission_type.value,
            )
            db.add(perm_obj)
            new_perms.append(perm_obj)
        doc.permissions = new_perms
        changes["permissions_count"] = len(new_perms)

    doc.updated_at = datetime.now(timezone.utc)

    client_ip = request.client.host if request.client else None
    db.add(
        AuditLog(
            user_id=user_ctx.id,
            action="ACL_UPDATE",
            resource_type="document",
            resource_id=str(doc.id),
            details_json=changes,
            ip_address=client_ip,
        )
    )
    db.commit()
    db.refresh(doc)

    return DocumentRead.model_validate(doc)


@router.delete(
    "/{doc_id}",
    response_model=DocumentDeleteResponse,
    summary="Delete a document and its indexed chunks (Admin or owning Department Manager)",
)
def delete_document(
    doc_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    user_ctx: UserContext = Depends(get_current_user_context),
) -> DocumentDeleteResponse:
    # Step 1: Enforce Scenario 3 SQL RBAC read filter first so unauthorized users get 404 Not Found
    stmt = (
        select(Document)
        .options(selectinload(Document.permissions))
        .where(
            Document.id == doc_id,
            _build_document_read_rbac_filter(db, user_ctx),
        )
    )
    doc = db.scalars(stmt).first()
    if doc is None:
        raise NotFoundException(
            detail="Document not found",
            error_code="DOCUMENT_NOT_FOUND",
        )

    # Step 2: Among users authorized to view the document, verify delete/manage privileges
    if not _user_can_manage_document(user_ctx, doc):
        raise ForbiddenException(
            detail="Insufficient permissions to delete document",
            error_code="DOCUMENT_DELETE_FORBIDDEN",
        )

    doc.is_deleted = True
    doc.updated_at = datetime.now(timezone.utc)
    db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == doc.id))

    client_ip = request.client.host if request.client else None
    db.add(
        AuditLog(
            user_id=user_ctx.id,
            action="DOCUMENT_DELETE",
            resource_type="document",
            resource_id=str(doc.id),
            details_json={"deleted": True},
            ip_address=client_ip,
        )
    )
    db.commit()

    return DocumentDeleteResponse(deleted=True, document_id=doc.id)
