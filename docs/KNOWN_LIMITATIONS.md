# Known Limitations, Operational Boundaries & v2 Roadmap (`docs/KNOWN_LIMITATIONS.md`)

> **Project**: Enterprise Knowledge Assistant (Hệ thống Trợ lý Tri thức Doanh nghiệp)  
> **Version**: v1.0.0 (Phase 6 Packaging Release)  
> **Owner**: `@architect`  
> **Scope**: Honest engineering assessment of v1.0 boundaries verified against the implemented codebase (`backend/`, `frontend/`, `infra/`, `tests/`), plus prioritized v2 remediation roadmap.

---

## 1. Executive Summary & Verified v1.0 Baseline

Version 1.0 of the **Enterprise Knowledge Assistant** delivers a production-structured, container-ready RAG architecture built on **FastAPI**, **LlamaIndex**, **PostgreSQL 16 + `pgvector`**, and **React + TypeScript**, with deterministic offline verification enabled via the provider abstraction layer (`LLM_PROVIDER`, `EMBEDDING_PROVIDER` in `{mock, openai, gemini}`).

All three mandatory security and reliability invariants were independently verified during Phase 5 (`docs/REVIEWS.md`):
- **Automated Test Suite (`pytest`)**: `51 passed` (0 failed, 0 skipped) across API endpoints, PDF/DOCX/XLSX parsers, red-team security controls, and all 3 mandatory scenarios.
- **RAG Evaluation Benchmark (`python tests/eval_rag.py`)**: `18/18 passed` (`100.00%` Scenario 1 abstention accuracy, `100.00%` Scenario 2 citation precision & verification accuracy, `100.00%` Scenario 3 pre-retrieval RBAC zero-leakage rate, `100.00%` red-team prompt-injection block rate, and `100.00%` overall answer groundedness on the offline `mock` provider baseline).

While these architectural foundations are verified, v1.0 intentionally scopes out several enterprise operational capabilities documented transparently below so operators can deploy safely and plan v2 upgrades accurately.

---

## 2. Document Parsing & Ingestion Limitations

The ingestion layer (`backend/app/ingestion/parsers/`) supports `.pdf`, `.docx`, `.xlsx`, `.txt`, and `.md` files up to `50 MB` (`MAX_UPLOAD_SIZE_MB=50`) with page/section/sheet metadata preservation. The following edge cases are **not** supported in v1.0:

| Limitation | Technical Root Cause (Current Implementation) | Operational Impact | v2 Remediation |
| :--- | :--- | :--- | :--- |
| **Scanned / Image-Only PDFs (No OCR)** | `backend/app/ingestion/parsers/pdf_parser.py` extracts embedded text layers via `pypdf` / `pdfplumber` (plus a pure-Python text-stream fallback). It does not rasterize pages or run optical character recognition. | Scanned contracts, signed forms, or image-only PDFs yield zero extracted text and fail ingestion (`status="failed"`). | Add an optional OCR stage (PaddleOCR / Tesseract with Vietnamese `vie` + English `eng` language packs, or Vision-LLM page transcription) triggered automatically when extracted text density falls below a threshold. |
| **Complex Excel Layouts & Charts** | `backend/app/ingestion/parsers/xlsx_parser.py` opens workbooks with `openpyxl(..., data_only=True, read_only=True)`, treats the first non-empty row per sheet as column headers, and chunks rows in blocks of 25. | Multi-row hierarchical headers, heavily merged cells, embedded charts/images, pivot-table metadata, and formulas saved without cached values may lose structural fidelity or evaluate to blank cells. | Upgrade XLSX extraction to resolve merged cell ranges (`sheet.merged_cells.ranges`), detect multi-level header blocks, and serialize wide financial tables using layout-aware table parsers. |
| **Password-Protected / Encrypted Office & PDF Files** | Neither `pypdf`, `python-docx`, nor `openpyxl` attempts brute-force or interactive decryption of encrypted archives. | Password-protected or enterprise DRM-wrapped documents cannot be parsed and are marked `status="failed"` with an error message. | Integrate enterprise KMS/IRM decryption hooks for approved service accounts or explicitly reject encrypted uploads at validation time with a localized `400 Bad Request` hint. |
| **Legacy Binary Formats & Embedded Media** | Supported types are constrained to modern OOXML/PDF/text (`pdf`, `docx`, `xlsx`, `txt`, `md`). Embedded diagrams/screenshots inside DOCX/PDF and legacy `.doc`/`.xls`/`.pptx` files are skipped. | Knowledge contained purely inside pasted screenshots, flowcharts, or PowerPoint decks is not indexed. | Add LibreOffice headless conversion for legacy `.doc`/`.xls`/`.ppt`, native `.pptx` slide parsing (`python-pptx`), and multimodal captioning for embedded figures. |

---

## 3. Embedding Space & Provider Switching Limitations

The provider abstraction layer (`backend/app/rag/providers/`) unifies `mock`, `openai`, and `gemini` behind `BaseEmbeddingProvider` and `BaseLLMProvider` using a fixed `vector(768)` column (`EMBEDDING_DIM=768`).

1. **`MockEmbeddingProvider` vs. Production Neural Embeddings**:
   - `backend/app/rag/providers/mock_provider.py` implements deterministic Vietnamese/English lexical and n-gram feature hashing with domain keyword projection into $\mathbb{R}^{768}$.
   - **Boundary**: This is designed strictly for deterministic offline development, Docker smoke tests, and zero-cost CI pipelines. It does **not** capture deep semantic paraphrasing, indirect Vietnamese synonyms, or cross-lingual Vietnamese $\leftrightarrow$ English conceptual equivalence the way neural embeddings (`OpenAI text-embedding-3-small` with `dimensions=768` or `Gemini models/text-embedding-004`) do. Production deployments MUST configure `LLM_PROVIDER` and `EMBEDDING_PROVIDER` to `openai` or `gemini` (or a self-hosted multilingual neural model such as `bge-m3` / `vietnamese-sbert`).
2. **Vector Space Compatibility on Provider/Model Migration**:
   - Vectors produced by `mock`, `openai`, and `gemini` occupy completely different latent spaces despite sharing `768` dimensions.
   - Although `documents.embedding_provider` and `documents.embedding_model` are recorded in PostgreSQL (`docs/ERD.md`), v1.0 does not yet include an automated zero-downtime re-indexing pipeline or mismatch blocker.
   - **Operational Rule**: Changing `EMBEDDING_PROVIDER` or `EMBEDDING_MODEL` in `.env` requires re-ingesting existing documents so stored chunk embeddings and query embeddings come from the exact same model.
3. **PostgreSQL `pgvector` vs. SQLite Unit-Test Fallback**:
   - In production (and Docker Compose), `PreRetrievalRBACRetriever` (`backend/app/rag/retriever.py`) executes a single PostgreSQL SQL query applying both the RBAC `WHERE` clause and `pgvector` cosine distance (`1 - (dc.embedding <=> :query_embedding)`) with HNSW indexing.
   - For local headless `pytest` execution without a running Postgres container, `retriever.py` supports a SQLite fallback that still enforces the RBAC `WHERE` predicate inside SQL before computing cosine similarity in Python. Production must **never** run on SQLite.

---

## 4. RAG Pipeline, Citation Verifier & Guardrail Boundaries

1. **Threshold Calibration (`SIMILARITY_THRESHOLD=0.65`, `QUOTE_OVERLAP_THRESHOLD=0.50`)**:
   - The default similarity cutoff (`0.65`) and quote lexical containment threshold (`0.50`) in `backend/app/core/config.py` are tuned for balanced precision and safe Scenario 1 abstention on the baseline corpus.
   - Because cosine similarity distributions differ across embedding models (for example, `text-embedding-3-small` cosine scores on dense enterprise text often cluster between `0.45` and `0.78`, whereas `text-embedding-004` clusters higher), production teams should run an evaluation sweep on labeled domain queries and adjust `SIMILARITY_THRESHOLD` via `.env` after switching to a cloud embedding provider.
2. **Lexical vs. Semantic Citation Verification**:
   - `CitationVerifier` (`backend/app/rag/verifier.py`) enforces three deterministic checks: (a) `chunk_id` whitelist lookup against retrieved authorized chunks, (b) numeric literal subset validation (`_extract_numbers`) to block fabricated numbers/dates/amounts, and (c) normalized substring / content-token containment (`>= 0.50`).
   - **Boundary**: If an LLM translates or heavily paraphrases a claim (e.g., user asks in English about a Vietnamese-only chunk) without quoting source phrasing, lexical token containment may score below `0.50`, causing the verifier to conservatively strip the citation or fall back to Scenario 1 (`found: false`). Pairing the deterministic verifier with a lightweight NLI / cross-encoder entailment model in v2 will improve recall on cross-lingual answers while retaining strict anti-hallucination safety.
3. **Regex-Based Prompt Injection & Indirect Injection Defense**:
   - `backend/app/rag/prompts.py` detects adversarial instructions via 11 bilingual Vietnamese/English `PROMPT_INJECTION_PATTERNS` regex rules alongside strict system-prompt isolation and pre-retrieval SQL RBAC enforcement (so even a successful jailbreak cannot access unauthorized chunks that never entered LLM context).
   - **Boundary**: Regex heuristics can be evaded by novel phrasings, leetspeak/base64 obfuscation, or **indirect prompt injection** hidden inside an uploaded document's body text. High-security deployments should add a dedicated guardrail classifier (e.g., Llama Guard / ShieldGemma) and content-level prompt-injection scanning during ingestion.
4. **Single-Turn Retrieval in Multi-Turn Chat Sessions**:
   - While `RAGService.query()` (`backend/app/rag/service.py`) stores full conversation turns in `chat_sessions` and `chat_messages`, each query is currently embedded and retrieved independently without conversational coreference rewriting.
   - **Impact**: Context-dependent follow-ups such as *"Còn đối với nhân viên thử việc thì sao?"* (without repeating *"nghỉ phép năm"*) may fail to match the previous topic above threshold. v2 should add a history-aware query condensation step constrained strictly to the current user's session history.

---

## 5. Authentication, RBAC & Storage Architecture Boundaries

1. **Internal JWT Authentication Only (No Enterprise SSO / IdP Sync)**:
   - `backend/app/core/security.py` implements local username/email authentication with bcrypt hashing and HS256-signed JWT tokens (`ACCESS_TOKEN_EXPIRE_MINUTES=1440`).
   - v1.0 does not yet integrate with corporate Identity Providers (OIDC, SAML 2.0, Microsoft Entra ID / Azure AD, Okta, Keycloak) or automatically synchronize department transfers / offboarding from HRIS/LDAP directories.
2. **Stateless Token Revocation & Session Lifecycle**:
   - Every authenticated API request resolves the latest `User` row from PostgreSQL in `get_current_user` (`backend/app/api/deps.py`), meaning role downgrades, department changes, or `is_active = False` deactivations take effect immediately even for existing JWTs.
   - However, because JWTs are stateless with a 24-hour expiration and no server-side token blocklist, explicit user logout only discards the token on the client. v2 should introduce short-lived access tokens (15 min), HTTP-only rotating refresh tokens, and a Redis `jti` revocation list.
3. **Local Filesystem Document Storage (`./data/uploads` / Docker Volume `upload_data`)**:
   - Uploaded raw files are persisted to a local filesystem directory (`UPLOAD_DIR=/data/uploads` mapped to the `eka_upload_data` Docker volume in `docker-compose.yml`).
   - **Boundary**: Local volumes work cleanly for single-node deployments but prevent stateless multi-replica horizontal scaling of the `backend` service. Production clustered deployments should replace local disk storage with an S3-compatible object store (AWS S3, GCS, or MinIO) with encryption at rest (SSE-KMS) and lifecycle retention policies.
4. **Static Department Catalog & Coarse Role Hierarchy**:
   - Departments are currently constrained by a `CHECK` constraint (`HR`, `Finance`, `Legal`, `Engineering`, `General`) and roles to 4 tiers (`admin`, `manager`, `employee`, `guest`). Organizations with nested business units (e.g., `Engineering > Platform > SRE`), multi-department matrix memberships, or time-bound temporary ACL grants will require dynamic department tables and many-to-many `user_departments` mappings in v2.

---

## 6. Performance, Scalability & Operational Readiness Boundaries

1. **Synchronous Ingestion Default (`INGESTION_MODE=sync`)**:
   - By default, `POST /api/v1/documents/upload` executes parsing, LlamaIndex chunking, and embedding generation synchronously so documents are immediately queryable (and tests remain deterministic). Although `INGESTION_MODE=background` offloads work to FastAPI `BackgroundTasks`, in-process background tasks do not survive container restarts and share CPU/memory with live API workers.
   - **v2 Upgrade**: Offload ingestion to a durable distributed job queue (Celery or ARQ backed by Redis/RabbitMQ) with chunked batch embedding, automatic retries, dead-letter queues, and real-time WebSocket/SSE progress updates.
2. **Vector Search Recall Under Highly Selective RBAC Predicates**:
   - The `document_chunks.embedding` column uses an HNSW index (`m=16, ef_construction=64`, `vector_cosine_ops` in `docs/ERD.md` and `infra/postgres/init.sql`).
   - When a large corpus (>100,000 chunks) is queried by a user whose RBAC predicate matches only a tiny fraction of rows (e.g., <1% of chunks), standard approximate HNSW traversal with default `hnsw.ef_search = 40` can experience pre-filtering recall drop.
   - **v2 Upgrade**: Upgrade to `pgvector >= 0.8.0` and enable iterative index scans (`SET LOCAL hnsw.iterative_scan = strict_order`), tune `hnsw.ef_search`, and combine dense vector retrieval with PostgreSQL BM25 / `tsvector` full-text hybrid search (Reciprocal Rank Fusion + cross-encoder reranking) for exact Vietnamese policy codes and legal clause numbers.
3. **Rate Limiting, Cost Controls & Production Observability**:
   - v1.0 exposes `/api/v1/health` and persists structured `audit_logs` in PostgreSQL, but does not yet enforce per-user/IP rate limits, per-department LLM token budgets, or export OpenTelemetry traces / Prometheus metrics.
   - **v2 Upgrade**: Add Redis-backed rate limiting (`slowapi` / Nginx `limit_req`), OpenTelemetry instrumentation, Prometheus/Grafana dashboards tracking P95 latency, LLM token usage, abstention rate, and citation rejection rate, plus automated `audit_logs` table partitioning and SIEM forwarding.

---

## 7. Prioritized v2 Engineering Roadmap

| Priority | Initiative | Target Components | Key Outcome |
| :--- | :--- | :--- | :--- |
| **P0 (Pre-Production Security & Scale)** | Enterprise SSO (OIDC/SAML) + Short-Lived Access/Refresh Tokens + Redis `jti` Revocation | `backend/app/core/security.py`, `backend/app/api/v1/auth.py` | Zero-trust identity lifecycle and immediate logout/offboarding enforcement |
| **P0 (Pre-Production Security & Scale)** | S3 / MinIO Object Storage Migration & Distributed Async Ingestion Queue (ARQ/Celery + Redis) | `backend/app/api/v1/documents.py`, `backend/app/ingestion/service.py`, `docker-compose.yml` | Multi-replica horizontal scaling and resilient 50MB+ batch ingestion |
| **P0 (Pre-Production Security & Scale)** | Automated Embedding Re-Indexing Job & Provider Mismatch Guard | `backend/app/ingestion/service.py`, `backend/app/rag/retriever.py` | Safe switching between `mock`, `openai`, `gemini`, or self-hosted embedding models |
| **P1 (RAG Quality & Document Coverage)** | Hybrid Retrieval (PostgreSQL BM25/`tsvector` + `pgvector` HNSW `iterative_scan` + Reranker) | `backend/app/rag/retriever.py`, `infra/postgres/init.sql` | Higher recall for exact Vietnamese legal/policy IDs and selective RBAC slices |
| **P1 (RAG Quality & Document Coverage)** | Multi-Turn Conversational Query Rewriting (Session-Scoped Coreference Resolution) | `backend/app/rag/service.py`, `backend/app/rag/prompts.py` | Accurate follow-up Q&A across multi-turn conversations |
| **P1 (RAG Quality & Document Coverage)** | Vietnamese/English OCR Pipeline (PaddleOCR/Tesseract) + Merged-Cell XLSX & PPTX Support | `backend/app/ingestion/parsers/` | Support scanned PDFs, complex financial spreadsheets, and slide decks |
| **P2 (Governance & Observability)** | ML Guardrail Classifier (Direct + Indirect Prompt Injection) & NLI Citation Entailment | `backend/app/rag/prompts.py`, `backend/app/rag/verifier.py` | Defense-in-depth against obfuscated/indirect injections and cross-lingual paraphrasing |
| **P2 (Governance & Observability)** | Dynamic Department Hierarchy, Multi-Department Users & OpenTelemetry/Prometheus + SIEM Export | `backend/app/db/models.py`, `backend/app/main.py`, `infra/` | Full enterprise governance, SLO monitoring, and compliance auditing |

---

## 8. Related Architecture & Verification Documents

- [ASSUMPTIONS.md](../ASSUMPTIONS.md) — Phase 0 engineering defaults and stakeholder decisions
- [docs/ADR.md](./ADR.md) — Architectural Decision Records (LlamaIndex, Pre-Retrieval RBAC, Provider Abstraction, Citation Verification)
- [docs/ARCHITECTURE.md](./ARCHITECTURE.md) — System architecture diagrams and module ownership
- [docs/ERD.md](./ERD.md) — PostgreSQL 16 + `pgvector` schema and SQL DDL
- [docs/openapi.yaml](./openapi.yaml) — OpenAPI 3.1.0 REST specification
- [docs/CONTRACTS.md](./CONTRACTS.md) — Cross-module Python and Pydantic v2 interface contracts
- [docs/REVIEWS.md](./REVIEWS.md) — Independent phase-gate review sign-offs and verified test metrics
