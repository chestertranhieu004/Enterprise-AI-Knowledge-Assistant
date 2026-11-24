"""Initial schema with pgvector extension, 8 tables, HNSW index, and RBAC indexes.

Revision ID: 001_initial_schema
Revises: None
Create Date: 2026-11-24 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.db.models import JSONBType, StringListType, UUIDType, Vector768Type

# revision identifiers, used by Alembic.
revision: str = "001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    if is_postgres:
        op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";')
        op.execute('CREATE EXTENSION IF NOT EXISTS "vector";')

    # 1. departments
    op.create_table(
        "departments",
        sa.Column("id", UUIDType, primary_key=True, nullable=False),
        sa.Column("code", sa.String(50), unique=True, nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # 2. users
    op.create_table(
        "users",
        sa.Column("id", UUIDType, primary_key=True, nullable=False),
        sa.Column("email", sa.String(255), unique=True, nullable=False),
        sa.Column("username", sa.String(100), unique=True, nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, server_default="employee"),
        sa.Column(
            "department",
            sa.String(50),
            sa.ForeignKey("departments.code", onupdate="CASCADE"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("idx_users_role_dept", "users", ["role", "department"])

    # 3. documents
    op.create_table(
        "documents",
        sa.Column("id", UUIDType, primary_key=True, nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("file_name", sa.String(500), nullable=False),
        sa.Column("file_path", sa.String(1024), nullable=False),
        sa.Column("file_type", sa.String(32), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column(
            "department",
            sa.String(50),
            sa.ForeignKey("departments.code", onupdate="CASCADE"),
            nullable=False,
        ),
        sa.Column("access_level", sa.String(32), nullable=False, server_default="department"),
        sa.Column("allowed_roles", StringListType(), nullable=False),
        sa.Column(
            "uploaded_by",
            UUIDType,
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="processing"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("embedding_provider", sa.String(32), nullable=False, server_default="mock"),
        sa.Column("embedding_model", sa.String(128), nullable=False, server_default="mock-768"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_documents_rbac_filter",
        "documents",
        ["status", "is_deleted", "access_level", "department"],
    )
    if is_postgres:
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_documents_allowed_roles_gin "
            "ON documents USING GIN (allowed_roles);"
        )

    # 4. document_permissions
    op.create_table(
        "document_permissions",
        sa.Column("id", UUIDType, primary_key=True, nullable=False),
        sa.Column(
            "document_id",
            UUIDType,
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            UUIDType,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "department",
            sa.String(50),
            sa.ForeignKey("departments.code", onupdate="CASCADE", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("role", sa.String(32), nullable=True),
        sa.Column("permission_type", sa.String(32), nullable=False, server_default="read"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_doc_perms_lookup",
        "document_permissions",
        ["document_id", "permission_type", "user_id", "department", "role"],
    )

    # 5. document_chunks
    op.create_table(
        "document_chunks",
        sa.Column("id", UUIDType, primary_key=True, nullable=False),
        sa.Column(
            "document_id",
            UUIDType,
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("page_or_sheet", sa.String(255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metadata_json", JSONBType, nullable=False),
        sa.Column("embedding", Vector768Type, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_document_chunk_index"),
    )
    op.create_index("idx_document_chunks_doc_id", "document_chunks", ["document_id"])
    if is_postgres:
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_document_chunks_embedding_hnsw "
            "ON document_chunks USING hnsw (embedding vector_cosine_ops) "
            "WITH (m = 16, ef_construction = 64);"
        )

    # 6. chat_sessions
    op.create_table(
        "chat_sessions",
        sa.Column("id", UUIDType, primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            UUIDType,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "title",
            sa.String(300),
            nullable=False,
            server_default="Cuộc hội thoại mới",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("idx_chat_sessions_user_id", "chat_sessions", ["user_id", "updated_at"])

    # 7. chat_messages
    op.create_table(
        "chat_messages",
        sa.Column("id", UUIDType, primary_key=True, nullable=False),
        sa.Column(
            "session_id",
            UUIDType,
            sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("found", sa.Boolean(), nullable=True),
        sa.Column("citations_json", JSONBType, nullable=False),
        sa.Column("suggested_queries_json", JSONBType, nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("idx_chat_messages_session_id", "chat_messages", ["session_id", "created_at"])

    # 8. audit_logs
    op.create_table(
        "audit_logs",
        sa.Column("id", UUIDType, primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            UUIDType,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.String(128), nullable=True),
        sa.Column("details_json", JSONBType, nullable=False),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("idx_audit_logs_user_action", "audit_logs", ["user_id", "action", "created_at"])


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("chat_messages")
    op.drop_table("chat_sessions")
    op.drop_table("document_chunks")
    op.drop_table("document_permissions")
    op.drop_table("documents")
    op.drop_table("users")
    op.drop_table("departments")
