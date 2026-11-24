-- ============================================================================
-- Enterprise Knowledge Assistant — PostgreSQL 16 + pgvector Initialization DDL
-- Reference: docs/ERD.md
-- ============================================================================

-- ============================================================================
-- EXTENSIONS
-- ============================================================================
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";

-- ============================================================================
-- 1. TABLE: departments
-- ============================================================================
CREATE TABLE IF NOT EXISTS departments (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    code VARCHAR(50) NOT NULL UNIQUE
        CHECK (code IN ('HR', 'Finance', 'Legal', 'Engineering', 'General')),
    name VARCHAR(150) NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- 2. TABLE: users
-- ============================================================================
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email VARCHAR(255) NOT NULL UNIQUE,
    username VARCHAR(100) NOT NULL UNIQUE,
    hashed_password VARCHAR(255) NOT NULL,
    full_name VARCHAR(200) NOT NULL,
    role VARCHAR(32) NOT NULL DEFAULT 'employee'
        CHECK (role IN ('admin', 'manager', 'employee', 'guest')),
    department VARCHAR(50) NOT NULL
        REFERENCES departments(code) ON UPDATE CASCADE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_role_dept ON users (role, department) WHERE is_active = TRUE;

-- ============================================================================
-- 3. TABLE: documents
-- ============================================================================
CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title VARCHAR(500) NOT NULL,
    file_name VARCHAR(500) NOT NULL,
    file_path VARCHAR(1024) NOT NULL,
    file_type VARCHAR(32) NOT NULL
        CHECK (file_type IN ('pdf', 'docx', 'xlsx', 'txt', 'md')),
    file_size_bytes BIGINT NOT NULL CHECK (file_size_bytes > 0 AND file_size_bytes <= 52428800),
    content_hash VARCHAR(64) NOT NULL,
    department VARCHAR(50) NOT NULL
        REFERENCES departments(code) ON UPDATE CASCADE,
    access_level VARCHAR(32) NOT NULL DEFAULT 'department'
        CHECK (access_level IN ('public', 'department', 'restricted')),
    allowed_roles TEXT[] NOT NULL DEFAULT ARRAY['admin', 'manager', 'employee']::TEXT[],
    uploaded_by UUID NOT NULL
        REFERENCES users(id) ON DELETE RESTRICT,
    status VARCHAR(32) NOT NULL DEFAULT 'processing'
        CHECK (status IN ('processing', 'ready', 'failed')),
    error_message TEXT,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    embedding_provider VARCHAR(32) NOT NULL DEFAULT 'mock',
    embedding_model VARCHAR(128) NOT NULL DEFAULT 'mock-768',
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- B-Tree & GIN indexes for fast Pre-Retrieval RBAC filtering (Scenario 3)
CREATE INDEX IF NOT EXISTS idx_documents_rbac_filter
    ON documents (status, is_deleted, access_level, department);
CREATE INDEX IF NOT EXISTS idx_documents_allowed_roles_gin
    ON documents USING GIN (allowed_roles);

-- ============================================================================
-- 4. TABLE: document_permissions
-- ============================================================================
CREATE TABLE IF NOT EXISTS document_permissions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID NOT NULL
        REFERENCES documents(id) ON DELETE CASCADE,
    user_id UUID
        REFERENCES users(id) ON DELETE CASCADE,
    department VARCHAR(50)
        REFERENCES departments(code) ON UPDATE CASCADE ON DELETE CASCADE,
    role VARCHAR(32)
        CHECK (role IS NULL OR role IN ('admin', 'manager', 'employee', 'guest')),
    permission_type VARCHAR(32) NOT NULL DEFAULT 'read'
        CHECK (permission_type IN ('read', 'manage')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- At least one principal selector must be non-null
    CONSTRAINT chk_document_permission_target
        CHECK (user_id IS NOT NULL OR department IS NOT NULL OR role IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_doc_perms_lookup
    ON document_permissions (document_id, permission_type, user_id, department, role);

-- ============================================================================
-- 5. TABLE: document_chunks
-- ============================================================================
CREATE TABLE IF NOT EXISTS document_chunks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID NOT NULL
        REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
    page_or_sheet VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    token_count INTEGER NOT NULL DEFAULT 0,
    metadata_json JSONB NOT NULL DEFAULT '{}'::JSONB,
    embedding vector(768) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_document_chunk_index UNIQUE (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_document_chunks_doc_id
    ON document_chunks (document_id);

-- HNSW Index for fast cosine similarity search in pgvector
CREATE INDEX IF NOT EXISTS idx_document_chunks_embedding_hnsw
    ON document_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ============================================================================
-- 6. TABLE: chat_sessions
-- ============================================================================
CREATE TABLE IF NOT EXISTS chat_sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL
        REFERENCES users(id) ON DELETE CASCADE,
    title VARCHAR(300) NOT NULL DEFAULT 'Cuộc hội thoại mới',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_sessions_user_id
    ON chat_sessions (user_id, updated_at DESC);

-- ============================================================================
-- 7. TABLE: chat_messages
-- ============================================================================
CREATE TABLE IF NOT EXISTS chat_messages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id UUID NOT NULL
        REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role VARCHAR(32) NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    found BOOLEAN,
    citations_json JSONB NOT NULL DEFAULT '[]'::JSONB,
    suggested_queries_json JSONB NOT NULL DEFAULT '[]'::JSONB,
    latency_ms INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_session_id
    ON chat_messages (session_id, created_at ASC);

-- ============================================================================
-- 8. TABLE: audit_logs
-- ============================================================================
CREATE TABLE IF NOT EXISTS audit_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID
        REFERENCES users(id) ON DELETE SET NULL,
    action VARCHAR(64) NOT NULL,
    resource_type VARCHAR(64) NOT NULL,
    resource_id VARCHAR(128),
    details_json JSONB NOT NULL DEFAULT '{}'::JSONB,
    ip_address VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_user_action
    ON audit_logs (user_id, action, created_at DESC);

-- ============================================================================
-- IDEMPOTENT BASELINE DEPARTMENTS SEED
-- ============================================================================
INSERT INTO departments (code, name, description)
VALUES
    ('HR', 'Nhân sự (Human Resources)', 'Phòng Nhân sự và Chính sách phúc lợi'),
    ('Finance', 'Tài chính - Kế toán (Finance)', 'Phòng Tài chính, Ngân sách và Kế toán'),
    ('Legal', 'Pháp chế (Legal & Compliance)', 'Phòng Pháp chế và Tuân thủ doanh nghiệp'),
    ('Engineering', 'Kỹ thuật - Công nghệ (Engineering)', 'Khối Kỹ thuật, Phát triển phần mềm và Hạ tầng'),
    ('General', 'Hành chính / Toàn công ty (General)', 'Quy chế chung và Thông tin toàn doanh nghiệp')
ON CONFLICT (code) DO NOTHING;
