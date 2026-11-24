# Architectural Decision Records (`docs/ADR.md`)

> **Project**: Enterprise Knowledge Assistant  
> **Phase**: Phase 1 — System Architecture & Technical Decisions  
> **Owner**: `@architect`

---

## Index of Decisions

1. **[ADR-001](#adr-001-rag-orchestration-framework-llamaindex-vs-langchain)**: RAG Orchestration Framework — **LlamaIndex** vs. LangChain
2. **[ADR-002](#adr-002-pre-retrieval-rbac--department-acl-enforcement-in-postgresql--pgvector)**: Pre-Retrieval RBAC & Department ACL Enforcement in PostgreSQL + `pgvector`
3. **[ADR-003](#adr-003-provider-agnostic-llm--embedding-abstraction-layer)**: Provider-Agnostic LLM & Embedding Abstraction Layer (`OpenAI`, `Gemini`, `Mock`)
4. **[ADR-004](#adr-004-post-generation-citation-verification--abstention-guardrail-architecture)**: Post-Generation Citation Verification & Abstention Guardrail Architecture

---

## ADR-001: RAG Orchestration Framework: LlamaIndex vs. LangChain

- **Status**: Accepted
- **Date**: 2026-11-24
- **Decision**: Choose **LlamaIndex (`llama-index-core` + custom PostgreSQL/pgvector node & retriever integration)** over LangChain.

### Context
The Enterprise Knowledge Assistant must ingest complex multi-format enterprise documents (`.pdf`, `.docx`, `.xlsx`), preserve fine-grained structural coordinates (`page_or_sheet`, `chunk_index`, section headers, row ranges), execute strict pre-retrieval RBAC filtering in PostgreSQL + `pgvector`, and track chunk-level provenance for deterministic post-generation citation verification.

We evaluated two primary Python frameworks: **LlamaIndex** and **LangChain**.

### Technical Comparison

| Evaluation Criterion | LlamaIndex (Chosen) | LangChain (Rejected) |
| :--- | :--- | :--- |
| **Document & Node Data Model** | First-class `Document` and `TextNode` primitives with native `node_id`, `ref_doc_id`, `metadata`, `excluded_embed_metadata_keys`, and `excluded_llm_metadata_keys`, plus `NodeWithScore` relationships (`PREVIOUS`/`NEXT`/`SOURCE`). | Generic `Document(page_content, metadata)` dataclass lacking built-in node relationship graphs or granular control over which metadata keys are embedded vs. exposed to the LLM. |
| **Multi-Format Enterprise Parsing (PDF/DOCX/XLSX)** | Native document-centric node parsers (`SentenceSplitter`, hierarchical/tabular node construction) that preserve `page_label`, sheet names, and structural boundaries cleanly inside node metadata. | Requires stitching multiple external document loaders and text splitters that frequently lose page or sheet row boundaries during secondary splitting. |
| **Pre-Retrieval Metadata Filtering with `pgvector`** | Clean alignment between LlamaIndex `TextNode` metadata / `VectorStoreQuery` filters and parameterized SQL `WHERE` clauses over relational ACL tables (`documents`, `document_permissions`). | VectorStore retrievers in LangChain abstract away relational joins, making multi-table RBAC + Department ABAC pre-filtering brittle or forcing post-retrieval filtering (which violates Scenario 3). |
| **Chunk-Level Citation & Provenance Tracking** | `NodeWithScore` directly retains `node.node_id` (`chunk_id`), exact character spans, `score`, and structured metadata throughout retrieval and synthesis, simplifying deterministic citation verification. | Chains (`RetrievalQA`, LCEL pipelines) often flatten documents into prompt strings early, requiring custom side-channel state passing to validate `[chunk_id]` citations post-generation. |
| **Abstraction Stability & Minimal Footprint** | `llama-index-core` provides lightweight, stable data structures (`Document`, `TextNode`, `NodeWithScore`, `SentenceSplitter`) that integrate cleanly with SQLAlchemy 2.0 + `pgvector`. | High surface area and frequent breaking changes across `langchain`, `langchain-core`, and `langchain-community` packages. |

### Decision Details
We adopt **LlamaIndex (`llama-index-core`)** as our document ingestion, node chunking, and retrieval data model framework, combined with an explicit SQLAlchemy + `pgvector` pre-retrieval SQL query layer:
1. **Ingestion Pipeline (`backend/app/ingestion`)**: Uses format-specific extractors (`pypdf`/`pdfplumber` for PDF, `python-docx` for DOCX, `openpyxl` for XLSX) wrapped into LlamaIndex `Document` objects and split via LlamaIndex's `SentenceSplitter(chunk_size=512, chunk_overlap=64)` (and row-grouped sheet nodes for XLSX) into `TextNode` instances with deterministic metadata (`page_or_sheet`, `chunk_index`, `section_title`).
2. **Pre-Retrieval Filtering (`backend/app/rag/retriever.py`)**: Maps retrieved database rows directly into LlamaIndex `NodeWithScore` objects after executing a single parameterized PostgreSQL query that enforces RBAC + Department ABAC **before** computing cosine similarity (`1 - (embedding <=> :query_embedding)`).

### Consequences
- **Positive**: Explicit control over node metadata, zero hidden post-filtering vulnerabilities, native support for chunk-level citations (`[chunk_id]`), and clean separation between node splitting and relational ACL enforcement.
- **Negative**: Requires writing an explicit SQLAlchemy/`pgvector` query builder to bridge `document_permissions` relational joins with LlamaIndex `NodeWithScore` objects (documented in ADR-002 and `docs/CONTRACTS.md`).

---

## ADR-002: Pre-Retrieval RBAC & Department ACL Enforcement in PostgreSQL + `pgvector`

- **Status**: Accepted
- **Date**: 2026-11-24
- **Decision**: Enforce all RBAC and Department ABAC permissions inside a single PostgreSQL SQL query (`WHERE` clause) **prior** to vector similarity ranking and `LIMIT :top_k`.

### Context
**Scenario 3 (`Người dùng không có quyền`)** requires that unauthorized documents and chunks are never retrieved into application memory, never sent to the LLM, and never leaked via error messages, filenames, or document counts.

A naive RAG implementation retrieves the top-$K$ nearest vectors globally from the vector index first and filters out unauthorized chunks in Python afterwards (**Post-Retrieval Filtering**). Post-retrieval filtering has two fatal flaws:
1. **Recall Starvation**: If the top 5 global vector matches belong to restricted HR documents, filtering them out in Python leaves 0 results even when authorized `public` documents exist at rank 6–10.
2. **Security Boundary Weakness**: Unauthorized chunk text is loaded into the Python process memory on every query, creating risk of logging or side-channel exposure.

### Decision Details
We store `documents`, `document_permissions`, and `document_chunks` in the same PostgreSQL 16 database with the `vector` (`pgvector`) extension enabled. Every retrieval query executes a parameterized SQL query that joins `document_chunks` with `documents` and evaluates the authorization predicate in the `WHERE` clause before sorting by cosine distance (`<=>`):

```sql
SELECT
    dc.id AS chunk_id,
    dc.document_id,
    d.file_name,
    d.title,
    d.department,
    d.access_level,
    dc.page_or_sheet,
    dc.chunk_index,
    dc.content,
    dc.metadata_json,
    (1 - (dc.embedding <=> :query_embedding)) AS similarity_score
FROM document_chunks dc
JOIN documents d ON d.id = dc.document_id
WHERE d.status = 'ready'
  AND d.is_deleted = FALSE
  AND (
      -- Rule 1: Admin has unrestricted access
      :user_role = 'admin'
      OR (
          -- Role must be permitted by document's allowed_roles array
          (:user_role = ANY(d.allowed_roles))
          AND (
              -- Rule 2a: Public documents
              d.access_level = 'public'
              -- Rule 2b: Department-scoped documents matching user's department
              OR (d.access_level = 'department' AND d.department = :user_department)
              -- Rule 2c: Explicit ACL grant in document_permissions
              OR EXISTS (
                  SELECT 1
                  FROM document_permissions dp
                  WHERE dp.document_id = d.id
                    AND dp.permission_type = 'read'
                    AND (
                        dp.user_id = :user_id
                        OR (dp.department = :user_department AND (dp.role IS NULL OR dp.role = :user_role))
                        OR (dp.department IS NULL AND dp.role = :user_role)
                    )
              )
          )
      )
  )
  AND (1 - (dc.embedding <=> :query_embedding)) >= :similarity_threshold
ORDER BY dc.embedding <=> :query_embedding ASC
LIMIT :top_k;
```

### Consequences
- **Positive**:
  - **100% Pre-Retrieval Enforcement**: Unauthorized rows never leave PostgreSQL.
  - **No Recall Starvation**: `LIMIT :top_k` applies only to the subset of chunks the user is authorized to read.
  - **Indistinguishable Abstention**: If a user asks about a restricted document, the query returns 0 rows (or only low-similarity public rows below threshold), seamlessly triggering Scenario 1 (`found: false`) with zero metadata leakage.
- **Negative**: Combining relational joins with HNSW vector search requires composite B-Tree indexes on `documents(status, is_deleted, access_level, department)` and `document_permissions(document_id, user_id, department, role)` alongside the HNSW index on `document_chunks(embedding vector_cosine_ops)`.

---

## ADR-003: Provider-Agnostic LLM & Embedding Abstraction Layer

- **Status**: Accepted
- **Date**: 2026-11-24
- **Decision**: Implement a unified Strategy/Adapter pattern (`BaseLLMProvider`, `BaseEmbeddingProvider`) supporting **`openai`**, **`gemini`**, and **`mock`** via environment variables (`LLM_PROVIDER`, `EMBEDDING_PROVIDER`) with a standardized `768`-dimensional embedding vector space.

### Context
The system must support cloud production deployments using **OpenAI API** or **Google Gemini API**, while also running deterministically in local development, Docker Compose evaluations, and offline CI test suites without external network dependencies or paid API keys.

### Decision Details
1. **Standardized 768-Dimension Vector Column**:
   - PostgreSQL table `document_chunks` defines `embedding vector(768)`.
   - **OpenAI Embedding Adapter (`OpenAIEmbeddingProvider`)**: Calls `text-embedding-3-small` with `dimensions=768` (natively supported by OpenAI's Matryoshka representation learning) and L2-normalizes the output.
   - **Gemini Embedding Adapter (`GeminiEmbeddingProvider`)**: Calls `models/text-embedding-004` (or `gemini-embedding-001` with `output_dimensionality=768`), returning a 768-dim L2-normalized vector.
   - **Deterministic Mock Embedding Adapter (`MockEmbeddingProvider`)**: Uses a deterministic bilingual (Vietnamese + English) feature hashing and semantic concept projection algorithm into $\mathbb{R}^{768}$ (L2-normalized). Texts sharing domain terms, phrases, or token n-grams produce high cosine similarity ($\ge 0.70$), while unrelated texts produce low cosine similarity ($< 0.35$). This enables end-to-end pgvector similarity search and threshold testing offline.
2. **Unified LLM Generation Interface (`BaseLLMProvider`)**:
   - Exposes `async def generate_rag_answer(query: str, chunks: list[RetrievedChunk], language: str = "vi") -> RawLLMGeneration`.
   - **OpenAI / Gemini Adapters**: Enforce structured JSON output containing `answer`, `cited_chunk_ids`, `citations` (with `chunk_id` and verbatim `quote`), and `suggested_queries`.
   - **Deterministic Mock LLM Adapter (`MockLLMProvider`)**: Extracts the highest-scoring authorized chunks, synthesizes a grounded Vietnamese response citing `[<chunk_id>]` with verbatim quotes extracted directly from `chunk.content`, and produces contextual `suggested_queries`.

### Consequences
- **Positive**: Seamless zero-downtime switching via `.env` variables; 100% reproducible automated test suite for all 3 mandatory scenarios without fragile external API mocks.
- **Negative**: Switching between different embedding providers (e.g., from `mock` to `openai`) requires re-embedding existing documents so query vectors and chunk vectors share the same latent space. We track `embedding_provider` and `embedding_model` on `documents` to detect mismatches.

---

## ADR-004: Post-Generation Citation Verification & Abstention Guardrail Architecture

- **Status**: Accepted
- **Date**: 2026-11-24
- **Decision**: Implement a two-stage guardrail pipeline around LLM generation: a **Pre-Generation Abstention Gate** (Scenario 1) and a **Post-Generation Deterministic Citation Verifier** (Scenario 2).

### Context
LLMs are prone to two critical failure modes in enterprise knowledge bases:
1. **Hallucinating answers** when retrieved chunks have low relevance or when no chunks are found (**Scenario 1**).
2. **Fabricating citations** (citing a non-existent `chunk_id`, attributing a claim to the wrong document/page, or misquoting the chunk content) (**Scenario 2**).

### Decision Details

```
[Authorized Chunks from pgvector]
        │
        ▼
┌─────────────────────────────────────────┐
│ Stage 1: Pre-Generation Abstention Gate │
│ Are there >= 1 chunks with              │
│ similarity_score >= 0.65?               │
└───────┬─────────────────────────┬───────┘
        │ NO                      │ YES
        ▼                         ▼
┌───────────────────────┐ ┌──────────────────────────────┐
│ Scenario 1 Triggered  │ │ Stage 2: Constrained LLM Gen │
│ - found = False       │ │ - Prompt supplies ONLY       │
│ - Exact VN abstention │ │   authorized chunks + IDs    │
│ - citations = []      │ │ - Requires [chunk_id] inline │
│ - 2-3 suggested       │ └──────────────┬───────────────┘
│   rephrasings         │                │
└───────────────────────┘                ▼
                          ┌──────────────────────────────────────────┐
                          │ Stage 3: Post-Generation Citation        │
                          │ Verifier (`verify_answer_and_citations`) │
                          │ 1. Parse all [chunk_id] & citation items │
                          │ 2. Check chunk_id in retrieved_chunk_map │
                          │ 3. Verify quote lexical/token overlap    │
                          │    with authoritative chunk.content      │
                          │ 4. Overwrite file_name & page_or_sheet   │
                          │    from DB record (never trust LLM)      │
                          │ 5. Strip unverified claims/citations     │
                          └──────────────┬───────────────────────────┘
                                         │
                         ┌───────────────┴───────────────┐
                         │ 0 verified citations left?    │
                         ├───────────────┬───────────────┤
                         │ YES           │ NO            │
                         ▼               ▼               │
             [Fallback to Scenario 1]  [Return Verified Answer + Citations]
```

#### Verification Algorithm (`backend/app/rag/verifier.py`)
For each candidate citation returned or referenced in the LLM's answer:
1. **Whitelist Lookup**: Verify `citation.chunk_id in retrieved_chunks_by_id`. If absent, mark `REJECTED_UNKNOWN_CHUNK` and strip from answer.
2. **Quote Grounding Verification**:
   - Normalize both `citation.quote` and `authoritative_chunk.content` (lowercase, Unicode NFC normalization, whitespace collapsing, punctuation stripping).
   - Check either:
     - Exact normalized substring inclusion (`norm_quote in norm_content`), OR
     - Token Jaccard / containment ratio $\frac{|T(\text{quote}) \cap T(\text{content})|}{|T(\text{quote})|} \ge 0.60$.
   - If the LLM provided an empty or inaccurate quote, but the answer sentence referencing `[chunk_id]` has token containment $\ge 0.50$ with `authoritative_chunk.content`, extract the best matching sentence span from `authoritative_chunk.content` as the canonical verbatim `quote`.
   - Otherwise, reject the citation (`REJECTED_UNGROUNDED_QUOTE`).
3. **Authoritative Metadata Binding**:
   - Bind `document_id`, `file_name`, `page_or_sheet`, and `score` directly from `authoritative_chunk`, making it impossible for the LLM to spoof a filename or page number.
4. **Post-Verification Abstention Fallback**:
   - If the LLM generated an affirmative answer (`found=True`) but **zero** citations pass verification, the verifier overrides the response to **Scenario 1** (`found=False`, standard abstention message, `citations=[]`, `suggested_queries`).

### Consequences
- **Positive**: Mathematically eliminates fabricated `chunk_id`s, spoofed filenames, wrong page/sheet numbers, and ungrounded answers.
- **Negative**: Adds ~2–5ms of CPU verification overhead per response, which is negligible compared to database retrieval and LLM inference latency.
