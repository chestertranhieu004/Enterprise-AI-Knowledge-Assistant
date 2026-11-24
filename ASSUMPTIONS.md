# Phase 0: Engineering Assumptions & System Defaults (`ASSUMPTIONS.md`)

> **Project**: Enterprise Knowledge Assistant (Hệ thống Trợ lý Tri thức Doanh nghiệp)  
> **Phase**: Phase 0 — Requirements Alignment & Engineering Defaults  
> **Status**: Baseline Approved for Phase 1 Architecture & Implementation

---

## 1. Executive Summary

This document defines the baseline engineering assumptions, security invariants, operational parameters, and stakeholder question defaults for the **Enterprise Knowledge Assistant**. These defaults govern the implementation of the FastAPI backend, PostgreSQL + `pgvector` database, LlamaIndex ingestion & retrieval pipeline, and React + TypeScript frontend.

All design decisions prioritize the **Three Mandatory System Scenarios**:
1. **Scenario 1 — Abstention on Missing Information (`AI không tìm thấy thông tin`)**: Deterministic `found: false` response with exact standard Vietnamese message `"Không tìm thấy thông tin phù hợp trong các tài liệu bạn có quyền truy cập."`, zero hallucination, empty citations list, and 2–3 actionable `suggested_queries`.
2. **Scenario 2 — Post-Generation Citation Verification (`Chống trả lời sai nguồn`)**: Every factual claim must cite an authorized `[chunk_id]`, verified post-generation against the retrieved chunk pool for ID validity, metadata accuracy (`file_name`, `page_or_sheet`), and lexical/semantic quote grounding.
3. **Scenario 3 — Pre-Retrieval RBAC & Department ABAC Filtering (`Người dùng không có quyền`)**: Access control predicates are enforced inside the PostgreSQL/pgvector SQL `WHERE` clause **prior** to vector similarity scoring. Unauthorized chunks never enter application memory or LLM context, and no metadata (file existence, title, count) is leaked.

---

## 2. Core Engineering Defaults

### 2.1 Access Control Model (RBAC + Department ABAC)

We implement a hybrid **Role-Based Access Control (RBAC)** and **Department Attribute-Based Access Control (ABAC)** model enforced at the SQL layer:

- **Roles (`UserRole`)**:
  - `admin`: System administrator; full access to manage users, permissions, documents, and query across all departments and access levels.
  - `manager`: Department manager; can upload/manage documents within their department and access `public`, matching `department`, and explicitly permitted `restricted` documents.
  - `employee`: Standard staff member; can query `public` documents, their own `department` documents (where `employee` is in `allowed_roles`), and `restricted` documents explicitly granted to their role/user/department.
  - `guest`: External/temporary user; restricted strictly to `public` documents where `guest` is included in `allowed_roles` (or ` explicit user permission).

- **Departments (`DepartmentCode`)**:
  - `HR` (Nhân sự)
  - `Finance` (Tài chính - Kế toán)
  - `Legal` (Pháp chế)
  - `Engineering` (Kỹ thuật - Công nghệ)
  - `General` (Hành chính / Toàn công ty)

- **Document Visibility Levels (`AccessLevel`)**:
  - `public`: Accessible to any authenticated user whose role is in the document's `allowed_roles`.
  - `department`: Accessible only to users belonging to the document's `department` (or granted via `document_permissions`) AND whose role is in `allowed_roles`.
  - `restricted`: Accessible only to `admin` users or users matching an explicit `document_permissions` rule (`user_id`, or `department` + `role`) AND whose role is in `allowed_roles`.

- **Zero-Leakage Indistinguishability Rule**:
  - If a user queries about a topic that exists **only** in unauthorized documents, the pre-retrieval SQL filter excludes those chunks completely. The RAG pipeline receives 0 matching chunks and triggers **Scenario 1 (`found: false`)** with the exact same response as when a document does not exist at all. No HTTP 403 or "You don't have permission to view file X.pdf" is ever returned on `/api/v1/chat/query`.

### 2.2 Document Ingestion & File Constraints

| Parameter | Default Value | Notes |
| :--- | :--- | :--- |
| **Allowed File Formats** | `.pdf`, `.docx`, `.xlsx` (plus `.txt`, `.md` for tests) | Validated by file extension and MIME magic bytes / header inspection |
| **Max File Upload Size** | `50 MB` (`52,428,800` bytes) | Enforced at FastAPI upload middleware and endpoint validation (`MAX_UPLOAD_SIZE_MB=50`) |
| **Chunk Size** | `512` tokens | Configured on LlamaIndex `SentenceSplitter` / structured node parsers (`CHUNK_SIZE=512`) |
| **Chunk Overlap** | `64` tokens | Preserves cross-boundary semantic continuity (`CHUNK_OVERLAP=64`) |
| **PDF Metadata Tracking** | `Page <N>` (1-indexed) | Stored in `document_chunks.page_or_sheet` (e.g., `"Trang 3"` or `"Page 3"`) |
| **DOCX Metadata Tracking** | `Section <N>` / Heading path | Stored in `document_chunks.page_or_sheet` (e.g., `"Section 2: Quy định nghỉ phép"`) |
| **XLSX Metadata Tracking** | `Sheet: <Name> (Rows <A>-<B>)` | Preserves column headers on every chunked row block so tabular semantics remain self-contained |
| **Ingestion Execution Mode** | Dual: `sync` (default) & `background` | Controlled via `INGESTION_MODE=sync\|background` and optional query param `async_processing: bool = False` |

### 2.3 Vector Database, Embedding & Retrieval Parameters

| Parameter | Default Value | Notes |
| :--- | :--- | :--- |
| **Vector Store Engine** | PostgreSQL 16 + `pgvector` | Uses HNSW index (`vector_cosine_ops`, `m=16`, `ef_construction=64`) |
| **Unified Embedding Dimension** | `768` (`vector(768)`) | Unified across Gemini (`text-embedding-004` / `768`), OpenAI (`text-embedding-3-small` with `dimensions=768`), and `mock` (`768`-dim deterministic L2-normalized vector) |
| **Similarity Metric** | Cosine Similarity (`1 - (embedding <=> query_vec)`) | Range `[0.0, 1.0]` for normalized embeddings |
| **Minimum Similarity Threshold** | `0.65` (`SIMILARITY_THRESHOLD=0.65`) | Chunks with score `< 0.65` are filtered out before LLM context construction |
| **Default `top_k`** | `5` (Range: `1..20`) | Maximum authorized chunks above threshold passed to the generator |
| **Primary Language** | Vietnamese (`vi`) | Supports Vietnamese and English queries, prompts, and abstention messages |

### 2.4 Citation Verification & Guardrail Parameters

- **Inline Citation Requirement**: The LLM system prompt requires every factual statement to include an inline bracketed chunk reference `[<chunk_id>]` and extracts exact supporting quotes from the source text.
- **Post-Generation Verifier Checks**:
  1. **Existence & Authorization Check**: `chunk_id` must exist in the exact set of authorized chunks retrieved for the current query turn (`retrieved_chunk_map`).
  2. **Metadata Integrity Check**: `file_name`, `document_id`, and `page_or_sheet` are populated deterministically from the authoritative database record of `chunk_id`, never trusted from raw LLM output.
  3. **Quote Grounding Check**: The cited `quote` (or claim text) must have a normalized token overlap / substring match score $\ge 0.50$ against `chunk.content`. If a citation fails verification, it is stripped or marked unverified; if zero verified citations remain for a factual response, the system falls back to **Scenario 1 (`found: false`)**.

---

## 3. Five Key Open Stakeholder Questions & Chosen Defaults

Because implementation proceeds immediately without waiting for external business sign-off, we document the 5 highest-impact stakeholder questions below along with the chosen engineering defaults:

### Question 1: Cross-Department Visibility & Manager Privileges
- **Stakeholder Question**: *Can a `manager` in one department (e.g., `Finance`) view `department`-level documents belonging to another department (e.g., `HR`), or is cross-department visibility restricted strictly to `admin` and explicit ACL grants?*
- **Chosen Default**: **Strict Department Isolation by default.** A `manager` only has automatic access to `department`-scoped documents within their own assigned department (`user.department == document.department`). Cross-department access requires either `access_level = 'public'` or an explicit row in `document_permissions` granting that department/role/user access. Only `admin` has global visibility across all departments.

### Question 2: Behavior When Unauthorized Documents Match a User's Query
- **Stakeholder Question**: *When an employee asks about a sensitive topic (e.g., "Bảng lương giám đốc") that exists in a restricted HR document, should the assistant say "Bạn không có quyền truy cập tài liệu này" or say "Không tìm thấy thông tin"?*
- **Chosen Default**: **Zero-Knowledge Abstention ("Không tìm thấy thông tin").** Revealing that a restricted document exists on a sensitive topic is an information disclosure vulnerability (existence leakage). Pre-retrieval SQL filtering excludes unauthorized rows before ranking, so the system returns the standard Scenario 1 response (`found: false`, `"Không tìm thấy thông tin phù hợp trong các tài liệu bạn có quyền truy cập."`).

### Question 3: Embedding Model Standardization Across Cloud & Offline Environments
- **Stakeholder Question**: *Which LLM and Embedding provider should be used in production versus local/CI environments without paid API keys?*
- **Chosen Default**: **Provider-Agnostic Adapter with 768-dim Standardization (`LLM_PROVIDER` & `EMBEDDING_PROVIDER` in `{openai, gemini, mock}`).** All embedding providers output `768`-dimensional L2-normalized vectors (`OpenAI text-embedding-3-small` with `dimensions=768`, `Gemini text-embedding-004` at `768`, and `DeterministicMockEmbeddingProvider` at `768` using char/token n-gram hashing + domain keyword anchors). This ensures schema compatibility without altering the PostgreSQL `vector(768)` column across environments.

### Question 4: Handling Partially Supported Answers During Citation Verification
- **Stakeholder Question**: *If an LLM answer contains two statements—one backed by a valid retrieved chunk and one referencing a hallucinated or unverified chunk—should the entire response be rejected or should only the unverified claim/citation be removed?*
- **Chosen Default**: **Granular Claim/Citation Pruning with Safe Fallback.** The Post-Generation Citation Verifier strips any citation whose `chunk_id` is not in the retrieved authorized pool or whose quote fails grounding verification against the chunk text. If at least one verified citation remains and supports the answer, the cleaned answer and verified citations are returned (`verification_status = "verified"`). If **zero** valid citations remain after verification, the entire answer is overridden to Scenario 1 (`found: false`) to guarantee zero ungrounded claims.

### Question 5: Synchronous vs. Asynchronous Document Ingestion on Upload
- **Stakeholder Question**: *Should `POST /api/v1/documents/upload` block until parsing, chunking, and embedding finish, or return `202 Accepted` and process in a background worker?*
- **Chosen Default**: **Configurable Hybrid Mode (Default `sync` for immediate readiness).** By default, uploads up to 50MB execute synchronously within the request lifecycle and return `status: "ready"` with `chunk_count` so users and automated tests can query the document immediately after upload. An optional form field / setting (`INGESTION_MODE=background`) runs ingestion via FastAPI `BackgroundTasks`, returning `status: "processing"` immediately and transitioning to `"ready"` or `"failed"`.
