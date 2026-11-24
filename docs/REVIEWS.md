# Phase Gate Reviews & Definition of Done Sign-Off (`docs/REVIEWS.md`)

> **Project**: Enterprise Knowledge Assistant (Hệ thống Trợ lý Tri thức Doanh nghiệp)  
> **Owner**: `@reviewer`  
> **Purpose**: Formal cross-review checklists and Definition of Done (DoD) verification records for each project phase.

---

## Phase 0 & Phase 1 Review — Architecture, Schemas, OpenAPI & Module Contracts

- **Reviewed Author**: `@architect`
- **Review Date**: 2026-11-24
- **Status**: **PASSED / APPROVED**

### 1. Artifact Verification Checklist

| # | Deliverable / Criterion | Target File(s) | Status | Verification Notes |
| :- | :--- | :--- | :--- | :--- |
| 1 | **Phase 0 Engineering Defaults & Open Questions** | `ASSUMPTIONS.md` | **PASS** | Defines hybrid RBAC + Department ABAC model (`admin`, `manager`, `employee`, `guest`; `HR`, `Finance`, `Legal`, `Engineering`, `General`; `public`, `department`, `restricted`), 50MB upload limit (`.pdf`, `.docx`, `.xlsx`, `.txt`, `.md`), 512/64 chunking rules with structural metadata preservation, unified 768-dim embeddings, 0.65 similarity threshold, and 5 stakeholder questions with chosen engineering defaults. |
| 2 | **Architectural Decision Records (ADRs)** | `docs/ADR.md` | **PASS** | **ADR-001** explicitly selects **LlamaIndex** over LangChain with a 5-axis technical comparison table. **ADR-002** defines Pre-Retrieval RBAC & Department ABAC in PostgreSQL + `pgvector` SQL `WHERE` clause. **ADR-003** specifies the Provider-Agnostic LLM & Embedding Abstraction Layer (`openai`, `gemini`, `mock`) standardized on 768 dimensions. **ADR-004** specifies the Post-Generation Citation Verification & Abstention Guardrail algorithm. |
| 3 | **System Architecture, Data Flows & Ownership Boundaries** | `docs/ARCHITECTURE.md` | **PASS** | Contains Mermaid system architecture diagram, ingestion sequence diagram, RAG query pipeline flowchart covering Scenarios 1/2/3, repository tree, and strict non-overlapping directory ownership matrix across `@architect`, `@reviewer`, `@backend_core`, `@ingestion`, `@rag_engineer`, `@frontend`, `@devops`, and `@qa_security`. |
| 4 | **Entity-Relationship Diagram & SQL DDL** | `docs/ERD.md` | **PASS** | Includes Mermaid `erDiagram` and complete PostgreSQL 16 + `pgvector` DDL for `departments`, `users`, `documents`, `document_permissions`, `document_chunks` (`vector(768)` + HNSW cosine index `m=16, ef_construction=64`), `chat_sessions`, `chat_messages`, and `audit_logs`, plus B-Tree and GIN indexes for pre-retrieval RBAC filtering and default seed data specs. |
| 5 | **OpenAPI 3.1.0 REST Specification** | `docs/openapi.yaml` | **PASS** | Valid OpenAPI 3.1.0 spec defining `/api/v1/health`, `/api/v1/auth/login`, `/api/v1/auth/me`, `/api/v1/users`, `/api/v1/users/{user_id}/permissions`, `/api/v1/documents/upload`, `/api/v1/documents`, `/api/v1/documents/{doc_id}`, `/api/v1/documents/{doc_id}/acl`, `/api/v1/chat/query`, `/api/v1/chat/sessions`, and `/api/v1/chat/sessions/{session_id}`, including `CitationItem` (`document_id`, `file_name`, `page_or_sheet`, `chunk_id`, `quote`, `score`, `verification_status`) and `ChatQueryResponse` (`found`, `answer`, `citations`, `suggested_queries`, `latency_ms`). |
| 6 | **Cross-Module Python Interface Contracts** | `docs/CONTRACTS.md` | **PASS** | Specifies exact Python module paths, enums, `ABSTENTION_MESSAGE_VI` constant, `Settings`, SQLAlchemy 2.0 ORM models, Pydantic v2 schemas, `IngestionService`, `LlamaIndexChunker`, format parsers (`parse_pdf`, `parse_docx`, `parse_xlsx`), `BaseEmbeddingProvider`, `BaseLLMProvider`, `PreRetrievalRBACRetriever`, `CitationVerifier`, `RAGService`, and FastAPI auth/RBAC dependencies. |

### 2. Three Mandatory High-Priority Scenarios Alignment Check

| Scenario | Priority Requirement | Verification Across Design & Contracts | Status |
| :--- | :--- | :--- | :--- |
| **Scenario 1: AI không tìm thấy thông tin (Abstention)** | When no authorized chunk meets `similarity >= 0.65`, return `found: false`, exact message `"Không tìm thấy thông tin phù hợp trong các tài liệu bạn có quyền truy cập."`, zero hallucination, `citations: []`, and 2–3 `suggested_queries`. | Enforced in `ADR-004`, `ARCHITECTURE.md` (Gate 2), `ERD.md` (`chat_messages.found`, `suggested_queries_json`), `openapi.yaml` (`ChatQueryResponse`), and `CONTRACTS.md` (`ABSTENTION_MESSAGE_VI`, `RAGService.query`, `BaseLLMProvider.suggest_rephrased_queries`). | **PASS** |
| **Scenario 2: Chống trả lời sai nguồn (Citation Verification)** | Every factual claim cites `[chunk_id]`. Post-generation verifier checks `chunk_id` against retrieved authorized whitelist, verifies quote lexical/token overlap against `chunk.content`, binds authoritative `file_name` and `page_or_sheet` from DB, and strips/rejects unverified citations. | Enforced in `ADR-004`, `ARCHITECTURE.md` (Gate 3), `ERD.md` (`document_chunks.page_or_sheet`, `content`), `openapi.yaml` (`CitationItem`), and `CONTRACTS.md` (`CitationVerifier.verify`). | **PASS** |
| **Scenario 3: Người dùng không có quyền (Pre-Retrieval RBAC)** | Access control (`user_id`, `role`, `department`, `allowed_roles`, `access_level`, `document_permissions`) executes in PostgreSQL/pgvector SQL `WHERE` clause **before** vector similarity ranking. Zero leakage of unauthorized document existence, filenames, counts, or content. | Enforced in `ADR-002` (parameterized SQL query), `ARCHITECTURE.md` (Gate 1), `ERD.md` (composite RBAC + GIN indexes), `openapi.yaml`, and `CONTRACTS.md` (`PreRetrievalRBACRetriever.retrieve_authorized_chunks`). | **PASS** |

### 3. Sign-Off Decision
**APPROVED.** The swarm is cleared to transition to Phase 2 parallel implementation.

---

## Phase 2 Review — Backend Core Foundation & DevOps Infrastructure

- **Reviewed Authors**: `@backend_core`, `@devops`
- **Review Date**: 2026-11-24
- **Status**: **PASSED / APPROVED**

### 1. `@backend_core` Verification Checklist

| # | Deliverable / Criterion | Target File(s) | Status | Verification Notes |
| :- | :--- | :--- | :--- | :--- |
| 1 | **Dependencies & Packaging** | `backend/requirements.txt`, `backend/pyproject.toml` | **PASS** | Declares FastAPI, Uvicorn, Pydantic v2, SQLAlchemy 2.0, `psycopg`, `pgvector`, Alembic, `PyJWT`, `python-jose`, `bcrypt`, `passlib`, `llama-index-core`, `pypdf`, `pdfplumber`, `python-docx`, `openpyxl`, `openai`, `google-generativeai`, and `pytest`. |
| 2 | **Core Config, JWT Security & Exception Handlers** | `backend/app/core/config.py`, `security.py`, `exceptions.py` | **PASS** | `Settings` matches `docs/CONTRACTS.md` Section 3 identically. `security.py` implements `get_password_hash`, `verify_password`, `create_access_token`, and `decode_access_token` with `bcrypt`/`PyJWT`/`python-jose` + deterministic stdlib fallbacks. `exceptions.py` registers structured JSON handlers (`{"detail": ..., "error_code": ...}`) for 400/401/403/404/409/413/422. |
| 3 | **Database Session, 8 ORM Models, Idempotent Seed & Alembic** | `backend/app/db/session.py`, `models.py`, `seed.py`, `backend/alembic/**` | **PASS** | Defines all 8 ORM models (`Department`, `User`, `Document`, `DocumentPermission`, `DocumentChunk`, `ChatSession`, `ChatMessage`, `AuditLog`) with PostgreSQL native `UUID`, `JSONB`, `ARRAY(Text)`, and `Vector(768)` plus SQLite-safe `.with_variant()` / `TypeDecorator` fallbacks. `seed.py` idempotently seeds 5 departments, 7 demo users across `admin`/`manager`/`employee`/`guest`, and 3 sample documents (`public`, `department`, `restricted`). Alembic `001_initial_schema.py` creates `uuid-ossp` & `vector` extensions, all 8 tables, B-Tree RBAC indexes, GIN `allowed_roles` index, and HNSW `vector(768)` index. |
| 4 | **Shared Pydantic v2 Schemas** | `backend/app/schemas/{common,user,auth,document,chat}.py` | **PASS** | Defines exact constants (`ABSTENTION_MESSAGE_VI`), enums (`UserRole`, `DepartmentCode`, `AccessLevel`, `DocumentStatus`, `PermissionType`, `VerificationStatus`), and Pydantic v2 request/response models matching `docs/CONTRACTS.md` Sections 2 & 5 and `docs/openapi.yaml`. |
| 5 | **FastAPI Routers, Lazy Phase 3 Wiring & Scenario 3 Zero-Leakage** | `backend/app/main.py`, `backend/app/api/deps.py`, `backend/app/api/v1/{router,health,auth,users,documents,chat}.py` | **PASS** | Implements all OpenAPI v1 endpoints. `v1/documents.py` and `v1/chat.py` lazily import `IngestionService` and `RAGService` inside route handlers for clean Phase 2 boot and Phase 3 integration. `_build_document_read_rbac_filter(db, user_ctx)` enforces pre-retrieval SQL RBAC + Department ABAC filtering and returns `404 Not Found` (zero leakage of existence or filename) whenever an unauthorized user probes `GET /api/v1/documents/{doc_id}`, `PATCH /api/v1/documents/{doc_id}/acl`, or `DELETE /api/v1/documents/{doc_id}`. |

### 2. `@devops` Verification Checklist

| # | Deliverable / Criterion | Target File(s) | Status | Verification Notes |
| :- | :--- | :--- | :--- | :--- |
| 1 | **Docker Compose 3-Service Orchestration** | `docker-compose.yml` | **PASS** | Configures `db` (`pgvector/pgvector:pg16` with `init.sql` mount and `pg_isready` healthcheck), `backend` (`./infra/backend.Dockerfile`, depends on healthy `db`, mounts `upload_data`, defaults `LLM_PROVIDER=mock` & `EMBEDDING_PROVIDER=mock`, probes `/api/v1/health`), and `frontend` (`./infra/frontend.Dockerfile`, depends on healthy `backend`, exposes `3000:80`, probes `/healthz`). |
| 2 | **Environment Template & Git Ignore** | `.env.example`, `.gitignore` | **PASS** | `.env.example` documents all `Settings` fields from `docs/CONTRACTS.md` Section 3. `.gitignore` excludes `.env` secrets, Python caches/venvs, Node artifacts, and runtime upload files. |
| 3 | **PostgreSQL + `pgvector` Init DDL** | `infra/postgres/init.sql` | **PASS** | Enables `uuid-ossp` and `vector` extensions, creates all 8 tables matching `docs/ERD.md`, creates B-Tree RBAC, GIN `allowed_roles`, and HNSW `vector_cosine_ops` (`m=16, ef_construction=64`) indexes, and idempotently seeds the 5 baseline departments. |
| 4 | **Container Dockerfiles, Nginx Proxy & Scripts** | `infra/backend.Dockerfile`, `infra/frontend.Dockerfile`, `infra/nginx.conf`, `infra/scripts/{entrypoint,healthcheck}.sh` | **PASS** | Multi-stage builds, Nginx SPA fallback + `/api/` reverse proxy to `backend:8000` with `client_max_body_size 55M`, DB readiness wait script, and `/api/v1/health` probe script. |
| 5 | **CI Workflow** | `.github/workflows/ci.yml` | **PASS** | GitHub Actions workflow validating backend Ruff lint + Pytest against `pgvector/pgvector:pg16` service container, frontend TypeScript `tsc --noEmit` + Vite build, and `docker compose config`. |

### 3. Directory Ownership Boundary Check
- **PASS**: Neither `@backend_core` nor `@devops` created or modified any files outside their assigned paths (`backend/app/ingestion/**`, `backend/app/rag/**`, `frontend/**`, and `tests/**` remain untouched for Phase 3/4/5).

### 4. Sign-Off Decision
**APPROVED.** Phase 2 is complete and verified. The swarm is cleared to transition to Phase 3 (`@ingestion` and `@rag_engineer`).

---

## Phase 3 Review — LlamaIndex Document Ingestion & RAG + Guardrail Engine

- **Reviewed Authors**: `@ingestion`, `@rag_engineer`
- **Review Date**: 2026-11-24
- **Status**: **PASSED / APPROVED**

### 1. `@ingestion` Verification Checklist (`backend/app/ingestion/**`)

| # | Deliverable / Criterion | Target File(s) | Status | Verification Notes |
| :- | :--- | :--- | :--- | :--- |
| 1 | **PDF Page-Aware Parser** | `backend/app/ingestion/parsers/pdf_parser.py` | **PASS** | Implements `parse_pdf(file_path) -> list[ParsedSection]` using `pypdf` primary extraction, `pdfplumber` secondary fallback, and stdlib PDF stream/FlateDecode fallback. Preserves paragraph breaks and 1-indexed `page_or_sheet = f"Page {page_number}"` coordinates. |
| 2 | **DOCX Section & Table Parser** | `backend/app/ingestion/parsers/docx_parser.py` | **PASS** | Implements `parse_docx(file_path) -> list[ParsedSection]` using `python-docx` (with stdlib OOXML `word/document.xml` fallback). Extracts paragraphs and tables in document order, tracks heading hierarchy into `Section N: <Heading>`, and converts Word tables into pipe-delimited Markdown rows (`| Col1 | Col2 |`). |
| 3 | **XLSX Sheet & Row-Batch Parser** | `backend/app/ingestion/parsers/xlsx_parser.py` | **PASS** | Implements `parse_xlsx(file_path, rows_per_section=25) -> list[ParsedSection]` using `openpyxl` (`load_workbook(..., data_only=True)`, with stdlib OOXML fallback). Detects header row, groups rows in batches of 25, prepends column headers to every batch, formats rows as `Header1: Val1 | Header2: Val2`, and sets `page_or_sheet = f"Sheet: {sheet.title} (Rows {start_row}-{end_row})"`. |
| 4 | **LlamaIndex Chunker & Text/Markdown Parser** | `backend/app/ingestion/chunker.py` | **PASS** | Defines `ParsedSection`, `ChunkData`, `LlamaIndexChunker(chunk_size=512, chunk_overlap=64)`, and `parse_text_or_markdown`. Wraps `ParsedSection` into LlamaIndex `Document` and splits via `SentenceSplitter` into sequential `ChunkData` objects preserving `page_or_sheet`, `token_count`, and `metadata_json`. |
| 5 | **IngestionService & 768-dim Embedding Wiring** | `backend/app/ingestion/service.py`, `__init__.py`, `parsers/__init__.py` | **PASS** | Implements `IngestionResult` and `IngestionService.ingest_document(db, document_id, file_path=None) -> IngestionResult` matching `docs/CONTRACTS.md` Section 6.2. Lazily resolves `get_embedding_provider()` from `app.rag.providers.factory`, replaces old `DocumentChunk` rows, bulk inserts 768-dim `DocumentChunk` records, and updates `Document.status` (`ready` / `failed`). |

### 2. `@rag_engineer` Verification Checklist (`backend/app/rag/**`)

| # | Deliverable / Criterion | Target File(s) | Status | Verification Notes |
| :- | :--- | :--- | :--- | :--- |
| 1 | **Provider Base Contracts & Factory** | `backend/app/rag/providers/{base,factory,__init__}.py` | **PASS** | Defines `RetrievedChunk`, `RawCitationCandidate`, `RawLLMGeneration`, `BaseEmbeddingProvider`, `BaseLLMProvider`, `get_embedding_provider`, and `get_llm_provider` matching `docs/CONTRACTS.md` Sections 7.1 & 7.2. |
| 2 | **768-dim Semantic/Lexical Mock, OpenAI & Gemini Providers** | `backend/app/rag/providers/{mock_provider,openai_provider,gemini_provider}.py` | **PASS** | `MockEmbeddingProvider` implements deterministic Vietnamese-aware concept subspace + content token + non-diacritic token + bigram + char 4-gram feature hashing into 768 L2-normalized dimensions (`sim >= 0.78` on matching topics, `sim < 0.02` on unrelated topics). `OpenAIEmbeddingProvider` (`dimensions=768`) and `GeminiEmbeddingProvider` (`outputDimensionality=768`) standardize on 768-dim L2-normalized vectors. |
| 3 | **Scenario 3: Pre-Retrieval RBAC SQL Retriever** | `backend/app/rag/retriever.py` | **PASS** | `PreRetrievalRBACRetriever.retrieve_authorized_chunks` enforces RBAC + Department ABAC inside the SQL `WHERE` clause prior to cosine similarity scoring in both PostgreSQL (`pgvector` `<=>` cosine distance matching ADR-002) and SQLite (`eka_cosine_sim` SQLite UDF + SQL `WHERE` RBAC predicate), wrapping results in LlamaIndex `TextNode`/`NodeWithScore`. Zero unauthorized chunks ever enter Python memory. |
| 4 | **Scenario 2: Post-Generation Citation Verifier** | `backend/app/rag/verifier.py` | **PASS** | `CitationVerifier.verify` validates every candidate `chunk_id` against the retrieved `authorized_chunks` whitelist, verifies lexical/token containment (`>= 0.50`) and numeric consistency against `chunk.content`, binds `document_id`, `file_name`, `page_or_sheet`, and `score` deterministically from the DB chunk, strips unverified claims, and falls back to Scenario 1 (`found=False`, `ABSTENTION_MESSAGE_VI`) if zero verified citations remain. |
| 5 | **Scenario 1, Prompt-Injection Guardrail & RAGService** | `backend/app/rag/prompts.py`, `service.py`, `__init__.py` | **PASS** | `RAGService.query` blocks prompt-injection attempts pre-retrieval and pre-generation, returns `found=False`, exact `ABSTENTION_MESSAGE_VI`, `citations=[]`, and 2–3 Vietnamese `suggested_queries` when 0 authorized chunks meet `similarity >= 0.65`, persists `ChatSession`, `ChatMessage`, and zero-leakage `AuditLog`, and integrates seamlessly with `backend/app/api/v1/chat.py` and `documents.py`. |

### 3. Directory Ownership Boundary Check
- **PASS**: `@ingestion` modified only `backend/app/ingestion/**` (7 files) and `@rag_engineer` modified only `backend/app/rag/**` (11 files). Cross-module imports between `backend_core`, `ingestion`, and `rag_engineer` match `docs/CONTRACTS.md` without discrepancy.

### 4. Sign-Off Decision
**APPROVED.** Phase 3 is complete and verified across all 3 mandatory scenarios. The swarm is cleared to transition to Phase 4 (`@frontend` and `@qa_security`).

---

## Phase 4 Review — React 18 + TypeScript Frontend Application

- **Reviewed Author**: `@frontend`
- **Review Date**: 2026-11-24
- **Status**: **PASSED / APPROVED**

### 1. `@frontend` Verification Checklist (`frontend/**`)

| # | Deliverable / Criterion | Target File(s) | Status | Verification Notes |
| :- | :--- | :--- | :--- | :--- |
| 1 | **Vite, TypeScript & Build Configuration** | `frontend/package.json`, `tsconfig.json`, `tsconfig.node.json`, `vite.config.ts`, `index.html`, `src/vite-env.d.ts` | **PASS** | Configures React 18 + TypeScript 5 + Vite 5 with strict typechecking (`tsc --noEmit`), dev server port `5173` with `/api` proxy to `http://localhost:8000`, and `build.outDir: 'dist'` matching `infra/frontend.Dockerfile` and `infra/nginx.conf`. |
| 2 | **OpenAPI 3.1.0 TypeScript Contracts & Typed REST Client** | `frontend/src/types/api.ts`, `frontend/src/api/client.ts` | **PASS** | `api.ts` defines exact interfaces for every schema in `docs/openapi.yaml` (`UserRole`, `DepartmentCode`, `AccessLevel`, `DocumentStatus`, `VerificationStatus`, `UserRead`, `LoginRequest`, `TokenResponse`, `DocumentPermissionEntry`, `DocumentRead`, `DocumentACLUpdateRequest`, `CitationItem`, `ChatQueryRequest`, `ChatQueryResponse`, `ChatMessageRead`, `ChatSessionSummary`, `ChatSessionDetail`, `HealthResponse`, `ABSTENTION_MESSAGE_VI`). `client.ts` implements typed methods for all 14 REST endpoints with Bearer JWT header injection, multipart `FormData` upload, 401 session invalidation, and `@company.vn`/`@enterprise.vn` demo alias support. |
| 3 | **Auth Context, Top Navbar & Demo Account Switcher** | `frontend/src/context/AuthContext.tsx`, `frontend/src/components/Navbar.tsx`, `frontend/src/pages/LoginPage.tsx` | **PASS** | Manages JWT authentication lifecycle (`POST /api/v1/auth/login`, `GET /api/v1/auth/me`), displays live backend health (`GET /api/v1/health`) and current user role/department badges, and provides 1-click demo login cards + navbar quick account switcher across all 7 seeded personas (`admin`, `hr.manager`, `hr.employee`, `eng.employee`, `finance.manager`, `legal.manager`, `guest`). |
| 4 | **Scenario 1 & Scenario 2 RAG Chat UI** | `frontend/src/pages/ChatPage.tsx`, `frontend/src/components/CitationCard.tsx` | **PASS** | Includes session history sidebar, starter prompt buttons (`"Chính sách nghỉ phép của công ty như thế nào?"`, `"Quy trình xin phê duyệt thanh toán?"`, `"Tìm tài liệu liên quan đến hợp đồng năm 2025."`, plus Scenario 3 probes). **Scenario 1 (`found === false`)**: renders `scenario1-abstention-alert` with exact `ABSTENTION_MESSAGE_VI`, zero fabricated citations, and clickable `suggested_queries` chips. **Scenario 2 (`found === true`)**: renders grounded answer with interactive `[chunk_id]` tags and `CitationCard` list showing `file_name`, `page_or_sheet` badge, verbatim `quote`, similarity `score` percentage bar, `✓ VERIFIED` badge, and copyable `chunk_id`. |
| 5 | **Scenario 3 Document Upload, ACL Editor & Admin RBAC Matrix** | `frontend/src/pages/DocumentsPage.tsx`, `frontend/src/pages/AdminPermissionsPage.tsx`, `frontend/src/{App.tsx,main.tsx,index.css}` | **PASS** | `DocumentsPage.tsx` supports `.pdf`, `.docx`, `.xlsx`, `.txt`, `.md` uploads up to 50MB with `department`, `access_level`, and `allowed_roles`, plus an RBAC-filtered document table, inline ACL permission editor modal (`PATCH /api/v1/documents/{doc_id}/acl`), and delete action (`DELETE /api/v1/documents/{doc_id}`). `AdminPermissionsPage.tsx` provides user creation (`POST /api/v1/users`), inline role/department/active status updates (`PATCH /api/v1/users/{user_id}/permissions`), and the Scenario 3 Pre-Retrieval SQL Isolation Matrix visualization. |

### 2. Directory Ownership Boundary Check
- **PASS**: `@frontend` created and modified files strictly inside `d:/enterprise-knowledge-assistant/frontend/**` (18 files). No backend, infrastructure, or documentation files were touched.

### 3. Sign-Off Decision
**APPROVED.** Phase 4 is complete and verified. The swarm is cleared to transition to Phase 5 (`@qa_security`).

---

## Phase 5 Review — QA Test Suite, Mandatory Scenario Proofs, Red-Team Security & RAG Benchmark

- **Reviewed Author**: `@qa_security`
- **Review Date**: 2026-11-24
- **Status**: **PASSED / APPROVED**

### 1. `@qa_security` Artifact Verification Checklist (`tests/**`)

| # | Deliverable / Criterion | Target File(s) | Status | Verification Notes |
| :- | :--- | :--- | :--- | :--- |
| 1 | **Test Bootstrap & Isolated Enterprise Harness** | `tests/__init__.py`, `tests/conftest.py` | **PASS** | Configures isolated SQLAlchemy `StaticPool` in-memory DB + FastAPI `TestClient` overriding `get_db`, seeds 5 departments (`HR`, `Finance`, `Legal`, `Engineering`, `General`) and 7 user personas (`admin`, `hr.manager`, `hr.employee`, `eng.employee`, `finance.manager`, `legal.manager`, `guest`), generates real `.pdf`, `.docx`, `.xlsx`, and `.md` enterprise fixture documents on disk, and ingests them via `IngestionService.ingest_document`. |
| 2 | **Scenario 1: Zero-Hallucination Abstention Suite** | `tests/test_scenario1_not_found.py` | **PASS** | **7/7 passed.** Verifies out-of-domain queries return `found=False`, exact `ABSTENTION_MESSAGE_VI`, `citations=[]`, 2–3 Vietnamese `suggested_queries`, cosine similarity `< 0.65`, strict cutoff abstention, DB persistence (`ChatMessage`, `AuditLog`), and zero restricted secret leakage. |
| 3 | **Scenario 2: Post-Generation Citation Verification Suite** | `tests/test_scenario2_citation_verification.py` | **PASS** | **8/8 passed.** Verifies grounded queries (`Chính sách nghỉ phép`, `Quy trình phê duyệt thanh toán`, `Hợp đồng năm 2025`, `Bảng khung lương Giám đốc Khối`) return `found=True`, DB-verified `chunk_id`, exact `file_name`, accurate `page_or_sheet`, `verification_status="verified"`, and verbatim `quote`. Adversarially verifies rejection of fabricated UUIDs, real unauthorized `chunk_id`s not in whitelist, fabricated numbers (`365 ngày phép`), and mixed-sentence unverified claim stripping. |
| 4 | **Scenario 3: Pre-Retrieval RBAC Zero-Leakage Suite** | `tests/test_scenario3_rbac_unauthorized.py` | **PASS** | **9/9 passed.** Attaches SQLAlchemy `before_cursor_execute` listener proving `WHERE` filters on `access_level`, `department`, `allowed_roles`, and `document_permissions` (`EXISTS`) run inside SQL before retrieval. Verifies cross-department isolation (`eng.employee`, `hr.employee`, `finance.manager`, `legal.manager`, `guest`) and proves `GET`/`PATCH`/`DELETE /api/v1/documents/{restricted_doc_id}` returns `404 Not Found` identically to a random non-existent UUID. |
| 5 | **Ingestion Parsers & LlamaIndex Chunker Suite** | `tests/test_ingestion_parsers.py` | **PASS** | **5/5 passed.** Verifies `parse_pdf` (multi-page + FlateDecode), `parse_docx` (headings + pipe-delimited Markdown tables), `parse_xlsx` (sheet coordinates, prepended column headers, 25-row batching), `LlamaIndexChunker`, and `IngestionService` (768-dim L2-normalized vectors + `failed` error handling). |
| 6 | **End-to-End FastAPI REST Endpoints Suite** | `tests/test_api_endpoints.py` | **PASS** | **5/5 passed.** Covers `/api/v1/health`, `/api/v1/auth/login` + `/me`, `/api/v1/users` RBAC lifecycle, `/api/v1/documents/upload` (multipart PDF/DOCX/XLSX) + ACL + delete, and `/api/v1/chat/query` + `/sessions` isolation. |
| 7 | **Red-Team Adversarial Security Suite** | `tests/test_redteam_security.py` | **PASS** | **17/17 passed.** Blocks 6 Vietnamese/English prompt-injection & jailbreak payloads, neutralizes 5 SQL injection payloads in login and chat queries, rejects missing/expired/forged/unknown-subject JWTs (401), blocks horizontal & vertical privilege escalation (403), and rejects executable/script uploads (`.exe`, `.bat`, `.sh`, `.php`) and 0-byte uploads (400). |
| 8 | **Executable RAG Evaluation & Security Benchmark** | `tests/eval_rag.py`, `tests/rag_eval_report.json` | **PASS** | Executes an 18-query benchmark across all 7 personas and 5 departments, saving real structured results to `tests/rag_eval_report.json`. |

### 2. Independent Verification Execution Output (Reviewer Re-Run)

#### Pytest Execution (`51 passed in 2.71s`, exit code `0`)
- `tests/test_api_endpoints.py`: **5 passed**
- `tests/test_ingestion_parsers.py`: **5 passed**
- `tests/test_redteam_security.py`: **17 passed**
- `tests/test_scenario1_not_found.py`: **7 passed**
- `tests/test_scenario2_citation_verification.py`: **8 passed**
- `tests/test_scenario3_rbac_unauthorized.py`: **9 passed**
- **Total**: **51 / 51 passed (100.00%, 0 failed, 0 skipped)**

#### RAG Evaluation & Security Benchmark (`python tests/eval_rag.py`, exit code `0`, `431.56 ms`)

| Benchmark Metric | Result | Passed / Total |
| :--- | :---: | :---: |
| **Scenario 1 Abstention Accuracy (%)** | **100.00%** | 4 / 4 queries |
| **Scenario 2 Citation Precision & Verification Accuracy (%)** | **100.00%** | 6 / 6 citations (6 / 6 queries) |
| **Scenario 3 RBAC Pre-Retrieval Zero-Leakage Rate (%)** | **100.00%** | 5 / 5 queries |
| **Red-Team Prompt Injection Block Rate (%)** | **100.00%** | 3 / 3 queries |
| **Overall Answer Groundedness (%)** | **100.00%** | 18 / 18 queries |

### 3. Directory Ownership Boundary Check
- **PASS**: `@qa_security` created and modified files strictly inside `d:/enterprise-knowledge-assistant/tests/**` (10 files). No application or infrastructure files outside `tests/**` were modified.

### 4. Sign-Off Decision
**APPROVED.** Phase 5 is complete and independently verified with 51/51 Pytest passes and 100.00% across all RAG & Security benchmark metrics. The project meets all Definition of Done criteria across Phases 0–5.

---

## Phase 6 Review — Packaging, Runbooks & Known Limitations

- **Reviewed Authors**: `@devops`, `@architect`
- **Review Date**: 2026-11-24
- **Status**: **PASSED / APPROVED**

### 1. Phase 6 Packaging Verification Checklist

| # | Deliverable / Criterion | Target File(s) | Status | Verification Notes |
| :- | :--- | :--- | :--- | :--- |
| 1 | **Project Overview, Quick Start, Demo Accounts & Verified Test Results** | `README.md` (`@devops`) | **PASS** | Vietnamese-first README covering full tech stack, detailed explanation of the 3 Mandatory Scenarios, Docker Compose (`cp .env.example .env && docker compose up --build`) & Local Dev instructions, provider switching (`mock` \| `openai` \| `gemini`), complete table of all 7 seeded demo accounts matching `backend/app/db/seed.py` (`Password123!`), real verified Phase 5 test & benchmark outputs (`51 passed in 2.71s`, `18/18` = `100.00%`), full directory tree, and valid links to all 9 documentation files. |
| 2 | **Local & Production Deployment Runbook** | `docs/DEPLOYMENT.md` (`@devops`) | **PASS** | Accurate operational runbook matching `docker-compose.yml`, `.env.example`, `infra/postgres/init.sql`, `infra/nginx.conf` (`client_max_body_size 55M`, `proxy_read_timeout 180s`), `infra/scripts/{entrypoint,healthcheck}.sh`, and `backend/alembic/versions/001_initial_schema.py`. Covers `.env` security hardening (`JWT_SECRET_KEY`, DB credentials, `SEED_ON_STARTUP=false`, TLS termination), Alembic migrations (`alembic upgrade head`, `RUN_MIGRATIONS=true`), healthchecks (`/api/v1/health`, `/healthz`, `pg_isready`), backup/restore (`pg_dump`/`pg_restore` + `eka_upload_data` volume), and troubleshooting. *(Minor v1.0 note: `v1/documents.py` executes ingestion synchronously in v1.0; distributed async background ingestion is tracked as a P0 v2 roadmap item.)* |
| 3 | **Honest Known Limitations & Prioritized v2 Roadmap** | `docs/KNOWN_LIMITATIONS.md` (`@architect`) | **PASS** | Thorough engineering assessment verified against `backend/app/**`, `docker-compose.yml`, and `tests/rag_eval_report.json`. Transparently documents document parsing limits (no OCR for scanned PDFs, complex merged XLSX/charts, encrypted files), embedding space boundaries (`MockEmbeddingProvider` vs. production neural embeddings, re-indexing on provider switch), RAG/guardrail calibration (`0.65`/`0.50`, lexical vs. cross-lingual grounding, regex vs. ML prompt-injection classifier, single-turn retrieval), auth/storage boundaries (local HS256 JWT without SSO/OIDC/revocation list, local disk volume vs. S3/MinIO), scalability limits (sync ingestion, HNSW `iterative_scan`), and a prioritized P0/P1/P2 v2 roadmap. |

### 2. Cross-Document Link & Boundary Verification
- **PASS**: All relative links across `README.md`, `docs/DEPLOYMENT.md`, and `docs/KNOWN_LIMITATIONS.md` resolve to verified files (`ASSUMPTIONS.md`, `docs/ADR.md`, `docs/ARCHITECTURE.md`, `docs/ERD.md`, `docs/openapi.yaml`, `docs/CONTRACTS.md`, `docs/DEPLOYMENT.md`, `docs/KNOWN_LIMITATIONS.md`, `docs/REVIEWS.md`).
- **PASS**: `@devops` modified only `README.md` and `docs/DEPLOYMENT.md`; `@architect` modified only `docs/KNOWN_LIMITATIONS.md`.

### 3. Final Release Sign-Off Decision
**APPROVED — ALL 6 PHASES COMPLETE.** All architecture, backend core, DevOps infrastructure, LlamaIndex ingestion, RAG + Guardrail engine, React + TypeScript frontend, QA/Red-Team security test suite (`51/51` Pytest, `18/18` = `100.00%` RAG eval), and packaging documentation meet the project Definition of Done.

---

## Post-Release Verification — Frontend TypeScript `ts(2339)` Fix & Real Production Build

- **Reviewed Author**: `@frontend`
- **Review Date**: 2026-11-24
- **Status**: **PASSED / APPROVED**

### 1. Code Diff Verification (`frontend/src/api/client.ts`)
- **Root Cause**: `Partial<ErrorResponse> & { detail?: unknown }` evaluated `detail` to `string | undefined`, causing TypeScript to narrow `parsed.detail` to `never` inside `else if (Array.isArray(parsed?.detail))` (`ts(2339)` at line 128).
- **Verified Fix**: Introduced dedicated `ValidationErrorItem` and `RawErrorPayload` interfaces (`detail?: string | ValidationErrorItem[] | unknown; error_code?: string | null;`) and safely narrowed `item: unknown` inside `parsed.detail.map(...)` before accessing `msg` or falling back to `JSON.stringify(item)`.

### 2. Independent Build Verification Output (Reviewer Re-Run)
Executed directly in `d:/enterprise-knowledge-assistant/frontend` with Node `v24.11.1`:
- **`tsc --noEmit`**: Exit code `0` (`0` errors across all `.ts` and `.tsx` files).
- **`tsc -p tsconfig.node.json --noEmit`**: Exit code `0` (`0` errors on `vite.config.ts`).
- **`vite build`**: Exit code `0` (`✓ 40 modules transformed`, `✓ built in 1.23s`):
  - `dist/index.html` (`0.64 kB │ gzip: 0.45 kB`)
  - `dist/assets/index-D5y2f7KS.css` (`24.30 kB │ gzip: 5.09 kB`)
  - `dist/assets/index-5QufDjNn.js` (`203.40 kB │ gzip: 62.15 kB`)

### 3. Sign-Off Decision
**APPROVED.** Frontend TypeScript compilation and Vite production bundle build pass cleanly with zero errors.

---

## Post-Release Verification — Docker Build Fix (`.dockerignore` & Refactored Dockerfiles)

- **Reviewed Author**: `@devops`
- **Review Date**: 2026-11-24
- **Status**: **PASSED / APPROVED**

### 1. Root Cause & Fix Verification
- **Root Cause**: Missing `.dockerignore` combined with blanket `COPY . /tmp/build_ctx` in `infra/frontend.Dockerfile` and `infra/backend.Dockerfile` copied Windows host `frontend/node_modules/` (Windows native binaries) and `frontend/dist/` into the `node:20-alpine` Linux builder container, causing `ERROR [frontend builder 4/4]`.
- **Verified Fixes**:
  1. **`.dockerignore`**: Created with full exclusions for `**/node_modules`, `**/dist`, `**/build`, `**/.vite`, `**/.venv`, `**/venv`, `**/__pycache__`, `**/*.py[cod]`, `**/.pytest_cache`, `**/.ruff_cache`, `.git`, `.env`, `.env.*` (`!.env.example`), `data/uploads`, `backend/data/uploads`, `tests`, `docs`, and IDE/OS artifacts.
  2. **`infra/frontend.Dockerfile`**: Removed all blanket `COPY .` commands. Stage 1 (`node:20-alpine`) explicitly copies only `frontend/package.json frontend/package-lock.json* /app/`, installs clean Linux dependencies (`npm ci` / `npm install`), copies explicit config and source (`frontend/index.html`, `frontend/tsconfig.json`, `frontend/tsconfig.node.json`, `frontend/vite.config.ts`, `frontend/src`), and runs `npm run build`. Stage 2 (`nginx:1.27-alpine`) copies `--from=builder /app/dist /usr/share/nginx/html` and `infra/nginx.conf /etc/nginx/conf.d/default.conf`.
  3. **`infra/backend.Dockerfile`**: Removed blanket `COPY . /tmp/build_ctx`. Layer-caches `COPY backend/requirements.txt /app/requirements.txt` + `pip install`, followed by explicit copies of `backend/pyproject.toml`, `backend/alembic.ini`, `backend/alembic`, `backend/app`, `infra/scripts/entrypoint.sh`, and `infra/scripts/healthcheck.sh` with CRLF normalization (`sed -i 's/\r$//'`).
  4. **`docker-compose.yml` & `.github/workflows/ci.yml`**: Verified alignment with `context: .` and all explicit `COPY` paths (100% of referenced source files/directories exist on disk; zero host-artifact contamination possible).

### 2. Sign-Off Decision
**APPROVED.** Docker build configuration is hardened against host OS binary contamination and ready for `docker compose build --no-cache frontend backend` on the user's Docker host.
