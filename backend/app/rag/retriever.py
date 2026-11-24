# File: backend/app/rag/retriever.py
import json
import logging
import uuid
from typing import Any

from sqlalchemy import String, and_, cast, exists, func, literal, or_, select, text
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.core.config import settings
from app.db.models import Document, DocumentChunk, DocumentPermission
from app.rag.prompts import is_prompt_injection
from app.rag.providers.base import (
    BaseEmbeddingProvider,
    RetrievedChunk,
    cosine_similarity,
)
from app.rag.providers.factory import get_embedding_provider
from app.schemas.common import (
    AccessLevel,
    DepartmentCode,
    DocumentStatus,
    PermissionType,
    UserRole,
)
from app.schemas.user import UserContext

logger = logging.getLogger(__name__)

try:
    from llama_index.core.schema import NodeWithScore, TextNode
except ImportError:  # pragma: no cover

    class TextNode:  # type: ignore[no-redef]
        """Lightweight LlamaIndex TextNode fallback when llama-index-core is not installed."""

        def __init__(
            self,
            id_: str,
            text: str,
            metadata: dict[str, Any] | None = None,
        ) -> None:
            self.id_ = id_
            self.node_id = id_
            self.text = text
            self.metadata = metadata or {}

        def get_content(self) -> str:
            return self.text

    class NodeWithScore:  # type: ignore[no-redef]
        """Lightweight LlamaIndex NodeWithScore fallback when llama-index-core is not installed."""

        def __init__(self, node: TextNode, score: float) -> None:
            self.node = node
            self.score = score

        @property
        def node_id(self) -> str:
            return self.node.id_

        def get_content(self) -> str:
            return self.node.get_content()

        def get_score(self) -> float:
            return float(self.score)


def build_pre_retrieval_rbac_predicate(
    db: Session,
    user: UserContext,
) -> ColumnElement[bool]:
    """
    Construct the SQLAlchemy SQL WHERE predicate enforcing Scenario 3 (ADR-002):
    - Document.status == 'ready'
    - Document.is_deleted == False
    - Admin role OR (user.role in Document.allowed_roles AND (public OR own department OR explicit DocumentPermission))
    """
    user_role_str = (
        user.role.value if isinstance(user.role, UserRole) else str(user.role)
    )
    user_dept_str = (
        user.department.value
        if isinstance(user.department, DepartmentCode)
        else str(user.department)
    )

    base_ready_filter = and_(
        Document.status == DocumentStatus.READY.value,
        Document.is_deleted.is_(False),
    )

    if user_role_str == UserRole.ADMIN.value:
        return base_ready_filter

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
        base_ready_filter,
        role_allowed_expr,
        or_(
            Document.access_level == AccessLevel.PUBLIC.value,
            and_(
                Document.access_level == AccessLevel.DEPARTMENT.value,
                Document.department == user_dept_str,
            ),
            perm_exists_expr,
        ),
    )


def _parse_embedding_vector(raw_embedding: Any) -> list[float]:
    """Parse a stored vector from PostgreSQL pgvector, Python list, or SQLite JSON string."""
    if raw_embedding is None:
        return []
    if isinstance(raw_embedding, list):
        return [float(x) for x in raw_embedding]
    if isinstance(raw_embedding, tuple):
        return [float(x) for x in raw_embedding]
    if isinstance(raw_embedding, str):
        cleaned = raw_embedding.strip()
        if cleaned.startswith("[") and cleaned.endswith("]"):
            try:
                return [float(x) for x in json.loads(cleaned)]
            except Exception:
                return []
    try:
        return [float(x) for x in list(raw_embedding)]
    except Exception:
        return []


def _register_sqlite_cosine_udf(db: Session, query_embedding: list[float]) -> bool:
    """
    Register a deterministic SQLite UDF `eka_cosine_sim(embedding_json)` on the active connection
    so cosine similarity filtering and sorting happen inside SQLite's SQL WHERE and ORDER BY clauses.
    """
    try:
        sa_conn = db.connection()
        raw_driver_conn = getattr(
            sa_conn.connection, "driver_connection", None
        ) or getattr(sa_conn.connection, "dbapi_connection", None)
        if raw_driver_conn is None or not hasattr(raw_driver_conn, "create_function"):
            return False

        def _sqlite_cosine_sim(raw_emb: Any) -> float:
            vec = _parse_embedding_vector(raw_emb)
            if not vec:
                return -1.0
            return cosine_similarity(query_embedding, vec)

        raw_driver_conn.create_function("eka_cosine_sim", 1, _sqlite_cosine_sim)
        return True
    except Exception as exc:
        logger.debug("SQLite UDF registration skipped: %s", exc)
        return False


class PreRetrievalRBACRetriever:
    """
    Scenario 3 Compliant Pre-Retrieval RBAC & Department ABAC Retriever.
    Executes authorization filtering inside the SQL WHERE clause BEFORE vector similarity ranking
    so unauthorized documents/chunks NEVER enter application retrieval memory.
    """

    def __init__(
        self,
        embedding_provider: BaseEmbeddingProvider | None = None,
        similarity_threshold: float | None = None,
    ) -> None:
        self.embedding_provider = embedding_provider or get_embedding_provider()
        self._explicit_similarity_threshold: float | None = similarity_threshold
        if similarity_threshold is not None:
            self.similarity_threshold = similarity_threshold
        elif getattr(self.embedding_provider, "provider_name", "mock") in (
            "openai",
            "gemini",
        ):
            self.similarity_threshold = min(settings.SIMILARITY_THRESHOLD, 0.48)
        else:
            self.similarity_threshold = settings.SIMILARITY_THRESHOLD

    @staticmethod
    def _wrap_as_llamaindex_node(
        chunk_id: uuid.UUID,
        document_id: uuid.UUID,
        file_name: str,
        title: str,
        department: str,
        access_level: str,
        page_or_sheet: str,
        chunk_index: int,
        content: str,
        score: float,
        metadata_json: dict[str, Any] | None,
    ) -> NodeWithScore:
        """Wrap an authorized database chunk row into a LlamaIndex TextNode + NodeWithScore."""
        merged_metadata: dict[str, Any] = dict(metadata_json or {})
        merged_metadata.update(
            {
                "chunk_id": str(chunk_id),
                "document_id": str(document_id),
                "file_name": file_name,
                "title": title,
                "department": department,
                "access_level": access_level,
                "page_or_sheet": page_or_sheet,
                "chunk_index": int(chunk_index),
            }
        )
        text_node = TextNode(
            id_=str(chunk_id),
            text=content,
            metadata=merged_metadata,
        )
        return NodeWithScore(node=text_node, score=round(float(score), 6))

    @staticmethod
    def _node_to_retrieved_chunk(node_with_score: NodeWithScore) -> RetrievedChunk:
        """Convert a LlamaIndex NodeWithScore into the contract RetrievedChunk schema."""
        node = node_with_score.node
        meta = dict(getattr(node, "metadata", {}) or {})
        return RetrievedChunk(
            chunk_id=uuid.UUID(str(meta["chunk_id"])),
            document_id=uuid.UUID(str(meta["document_id"])),
            file_name=str(meta["file_name"]),
            title=str(meta["title"]),
            department=str(meta["department"]),
            access_level=str(meta["access_level"]),
            page_or_sheet=str(meta["page_or_sheet"]),
            chunk_index=int(meta["chunk_index"]),
            content=node.get_content(),
            score=float(node_with_score.get_score()),
            metadata_json=meta,
        )

    def _retrieve_postgresql_pgvector(
        self,
        db: Session,
        user: UserContext,
        query_embedding: list[float],
        top_k: int,
        threshold: float,
    ) -> list[NodeWithScore]:
        """
        Execute the single parameterized PostgreSQL + pgvector SQL query from ADR-002.
        Enforces RBAC + Department ABAC inside WHERE prior to cosine distance (<=>) evaluation.
        """
        user_role_str = (
            user.role.value if isinstance(user.role, UserRole) else str(user.role)
        )
        user_dept_str = (
            user.department.value
            if isinstance(user.department, DepartmentCode)
            else str(user.department)
        )
        vector_literal = "[" + ",".join(f"{v:.7f}" for v in query_embedding) + "]"

        sql = text(
            """
            SELECT
                dc.id AS chunk_id,
                dc.document_id AS document_id,
                d.file_name AS file_name,
                d.title AS title,
                d.department AS department,
                d.access_level AS access_level,
                dc.page_or_sheet AS page_or_sheet,
                dc.chunk_index AS chunk_index,
                dc.content AS content,
                dc.metadata_json AS metadata_json,
                (1.0 - (dc.embedding <=> CAST(:query_embedding AS vector))) AS similarity_score
            FROM document_chunks dc
            JOIN documents d ON d.id = dc.document_id
            WHERE d.status = 'ready'
              AND d.is_deleted = FALSE
              AND (
                  :user_role = 'admin'
                  OR (
                      (:user_role = ANY(d.allowed_roles))
                      AND (
                          d.access_level = 'public'
                          OR (d.access_level = 'department' AND d.department = :user_department)
                          OR EXISTS (
                              SELECT 1
                              FROM document_permissions dp
                              WHERE dp.document_id = d.id
                                AND dp.permission_type IN ('read', 'manage')
                                AND (
                                    dp.user_id = :user_id
                                    OR (dp.department = :user_department AND (dp.role IS NULL OR dp.role = :user_role))
                                    OR (dp.department IS NULL AND dp.role = :user_role)
                                )
                          )
                      )
                  )
              )
              AND (1.0 - (dc.embedding <=> CAST(:query_embedding AS vector))) >= :similarity_threshold
            ORDER BY dc.embedding <=> CAST(:query_embedding AS vector) ASC
            LIMIT :top_k
            """
        )

        rows = db.execute(
            sql,
            {
                "query_embedding": vector_literal,
                "user_id": user.id,
                "user_role": user_role_str,
                "user_department": user_dept_str,
                "similarity_threshold": float(threshold),
                "top_k": int(top_k),
            },
        ).mappings().all()

        nodes: list[NodeWithScore] = []
        for row in rows:
            raw_meta = row["metadata_json"]
            if isinstance(raw_meta, str):
                try:
                    raw_meta = json.loads(raw_meta)
                except Exception:
                    raw_meta = {}
            nodes.append(
                self._wrap_as_llamaindex_node(
                    chunk_id=uuid.UUID(str(row["chunk_id"])),
                    document_id=uuid.UUID(str(row["document_id"])),
                    file_name=str(row["file_name"]),
                    title=str(row["title"]),
                    department=str(row["department"]),
                    access_level=str(row["access_level"]),
                    page_or_sheet=str(row["page_or_sheet"]),
                    chunk_index=int(row["chunk_index"]),
                    content=str(row["content"]),
                    score=float(row["similarity_score"]),
                    metadata_json=raw_meta if isinstance(raw_meta, dict) else {},
                )
            )
        return nodes

    def _retrieve_sqlite_pre_filtered(
        self,
        db: Session,
        user: UserContext,
        query_embedding: list[float],
        top_k: int,
        threshold: float,
    ) -> list[NodeWithScore]:
        """
        Execute Pre-Retrieval RBAC + Department ABAC inside the SQLite SQL WHERE clause.
        When SQLite UDF registration succeeds, cosine similarity threshold and LIMIT top_k
        also run inside SQLite SQL. Unauthorized documents/chunks NEVER leave SQLite.
        """
        rbac_predicate = build_pre_retrieval_rbac_predicate(db, user)
        udf_registered = _register_sqlite_cosine_udf(db, query_embedding)

        if udf_registered:
            score_expr = func.eka_cosine_sim(DocumentChunk.embedding).label(
                "similarity_score"
            )
            stmt = (
                select(
                    DocumentChunk.id.label("chunk_id"),
                    DocumentChunk.document_id.label("document_id"),
                    Document.file_name.label("file_name"),
                    Document.title.label("title"),
                    Document.department.label("department"),
                    Document.access_level.label("access_level"),
                    DocumentChunk.page_or_sheet.label("page_or_sheet"),
                    DocumentChunk.chunk_index.label("chunk_index"),
                    DocumentChunk.content.label("content"),
                    DocumentChunk.metadata_json.label("metadata_json"),
                    score_expr,
                )
                .join(Document, Document.id == DocumentChunk.document_id)
                .where(
                    rbac_predicate,
                    score_expr >= float(threshold),
                )
                .order_by(score_expr.desc())
                .limit(int(top_k))
            )
            rows = db.execute(stmt).mappings().all()
            return [
                self._wrap_as_llamaindex_node(
                    chunk_id=uuid.UUID(str(row["chunk_id"])),
                    document_id=uuid.UUID(str(row["document_id"])),
                    file_name=str(row["file_name"]),
                    title=str(row["title"]),
                    department=str(row["department"]),
                    access_level=str(row["access_level"]),
                    page_or_sheet=str(row["page_or_sheet"]),
                    chunk_index=int(row["chunk_index"]),
                    content=str(row["content"]),
                    score=float(row["similarity_score"]),
                    metadata_json=(
                        row["metadata_json"]
                        if isinstance(row["metadata_json"], dict)
                        else {}
                    ),
                )
                for row in rows
            ]

        # Fallback if SQLite connection does not support UDF registration:
        # RBAC predicate is STILL executed strictly inside the SQL WHERE clause.
        stmt_fallback = (
            select(
                DocumentChunk.id.label("chunk_id"),
                DocumentChunk.document_id.label("document_id"),
                Document.file_name.label("file_name"),
                Document.title.label("title"),
                Document.department.label("department"),
                Document.access_level.label("access_level"),
                DocumentChunk.page_or_sheet.label("page_or_sheet"),
                DocumentChunk.chunk_index.label("chunk_index"),
                DocumentChunk.content.label("content"),
                DocumentChunk.metadata_json.label("metadata_json"),
                DocumentChunk.embedding.label("embedding"),
            )
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(rbac_predicate)
        )
        authorized_rows = db.execute(stmt_fallback).mappings().all()
        scored_nodes: list[NodeWithScore] = []
        for row in authorized_rows:
            chunk_vec = _parse_embedding_vector(row["embedding"])
            score = cosine_similarity(query_embedding, chunk_vec)
            if score >= float(threshold):
                scored_nodes.append(
                    self._wrap_as_llamaindex_node(
                        chunk_id=uuid.UUID(str(row["chunk_id"])),
                        document_id=uuid.UUID(str(row["document_id"])),
                        file_name=str(row["file_name"]),
                        title=str(row["title"]),
                        department=str(row["department"]),
                        access_level=str(row["access_level"]),
                        page_or_sheet=str(row["page_or_sheet"]),
                        chunk_index=int(row["chunk_index"]),
                        content=str(row["content"]),
                        score=score,
                        metadata_json=(
                            row["metadata_json"]
                            if isinstance(row["metadata_json"], dict)
                            else {}
                        ),
                    )
                )
        scored_nodes.sort(key=lambda n: n.get_score(), reverse=True)
        return scored_nodes[: int(top_k)]

    def retrieve_authorized_chunks(
        self,
        db: Session,
        user: UserContext,
        query: str,
        top_k: int = 5,
        similarity_threshold: float | None = None,
    ) -> list[RetrievedChunk]:
        """
        1. Validates active user and checks for prompt-injection bypass attempts.
        2. Computes 768-dim query embedding via self.embedding_provider.embed_query(query).
        3. Executes the single parameterized SQL query (PostgreSQL pgvector or SQLite) that filters
           documents & document_permissions by (user.id, user.role, user.department) inside WHERE
           BEFORE computing cosine similarity and applying >= similarity_threshold and LIMIT top_k.
        4. Wraps results into LlamaIndex NodeWithScore internally and returns list[RetrievedChunk].
        5. Unauthorized documents/chunks NEVER enter Python memory.
        """
        if not user.is_active or not query or not query.strip():
            return []

        if is_prompt_injection(query):
            logger.warning(
                "Prompt injection attempt blocked pre-retrieval for user_id=%s",
                user.id,
            )
            return []

        effective_threshold = (
            similarity_threshold
            if similarity_threshold is not None
            else self.similarity_threshold
        )
        effective_top_k = max(1, min(int(top_k), 50))
        query_embedding = self.embedding_provider.embed_query(query)

        dialect_name = db.get_bind().dialect.name
        if dialect_name == "postgresql":
            nodes = self._retrieve_postgresql_pgvector(
                db=db,
                user=user,
                query_embedding=query_embedding,
                top_k=effective_top_k,
                threshold=effective_threshold,
            )
        else:
            nodes = self._retrieve_sqlite_pre_filtered(
                db=db,
                user=user,
                query_embedding=query_embedding,
                top_k=effective_top_k,
                threshold=effective_threshold,
            )

        return [self._node_to_retrieved_chunk(node) for node in nodes]
