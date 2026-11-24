# Enterprise Knowledge Assistant (Hệ thống Trợ lý Tri thức Doanh nghiệp)

> **Giải pháp RAG (Retrieval-Augmented Generation) nội bộ doanh nghiệp** hỗ trợ tiếng Việt & tiếng Anh, được thiết kế với bảo mật **Pre-Retrieval RBAC + Department ABAC** trực tiếp trong PostgreSQL/`pgvector`, **Post-Generation Citation Verification** chống bịa nguồn, và cơ chế **Abstention Guardrail** đảm bảo không suy diễn (zero hallucination).

---

## 1. Tổng quan & Công nghệ sử dụng (Tech Stack)

| Tầng hệ thống | Công nghệ | Vai trò chính |
| :--- | :--- | :--- |
| **Backend API** | **Python 3.11+**, **FastAPI**, **SQLAlchemy 2.0**, **Pydantic v2**, **Alembic** | REST API chuẩn OpenAPI 3.1, JWT Authentication, RBAC/ABAC enforcement, quản lý tài liệu & hội thoại |
| **RAG & Ingestion** | **LlamaIndex** (`SentenceSplitter`, `Document`, `TextNode`, `NodeWithScore`) + `pypdf`, `pdfplumber`, `python-docx`, `openpyxl` | Parse PDF (theo trang), DOCX (theo heading/bảng), XLSX (theo sheet/header + cụm dòng); chunking 512 tokens / 64 overlap |
| **Database & Vector** | **PostgreSQL 16** + **`pgvector`** (HNSW index `vector(768)`, `vector_cosine_ops`) | Lưu trữ đồng nhất dữ liệu quan hệ, quyền truy cập (`documents`, `document_permissions`) và vector 768-dim trong cùng một câu truy vấn SQL |
| **LLM & Embedding** | **Provider Abstraction Layer**: **OpenAI** (`gpt-4o-mini`, `text-embedding-3-small` 768-dim) \| **Google Gemini** (`gemini-2.0-flash`, `text-embedding-004` 768-dim) \| **Deterministic Mock** (`mock-grounded-vi`, `mock-768`) | Chuyển đổi linh hoạt qua biến môi trường `LLM_PROVIDER` & `EMBEDDING_PROVIDER`; chạy offline 100% cho CI/testing |
| **Frontend UI** | **React 18**, **TypeScript 5**, **Vite 5**, **Nginx** | Giao diện tiếng Việt gồm Chat RAG + Citation Cards, Upload & Quản lý ACL tài liệu, Quản trị phân quyền RBAC, và chuyển đổi tài khoản demo nhanh |
| **Container & CI** | **Docker Compose**, **GitHub Actions** (`.github/workflows/ci.yml`) | Đóng gói 3 container (`db`, `backend`, `frontend`) kèm healthcheck và pipeline CI tự động |

---

## 2. Ba Kịch bản Bắt buộc Ưu tiên Cao nhất (Three Mandatory Scenarios)

Mọi yêu cầu hỏi đáp qua `POST /api/v1/chat/query` đều đi qua 3 lớp bảo vệ nghiêm ngặt:

### Kịch bản 1: AI không tìm thấy thông tin (`Abstention Gate`)
- **Cơ chế**: Nếu truy vấn không tìm được chunk hợp lệ nào đạt ngưỡng tương đồng cosine `SIMILARITY_THRESHOLD >= 0.65` (hoặc tất cả trích dẫn đều bị loại ở bước hậu kiểm), hệ thống **ngắt mạch (short-circuit)** không cho LLM suy diễn.
- **Kết quả trả về**:
  - `found: false`
  - `answer`: `"Không tìm thấy thông tin phù hợp trong các tài liệu bạn có quyền truy cập."`
  - `citations: []` (tuyệt đối không tạo nguồn giả)
  - `suggested_queries`: 2–3 gợi ý diễn đạt lại câu hỏi bằng tiếng Việt (không làm lộ tên tài liệu hạn chế).

### Kịch bản 2: Chống trả lời sai nguồn (`Post-Generation Citation Verification`)
- **Cơ chế**: Prompt bắt buộc LLM gắn mã `[chunk_id]` cho từng nhận định và trích nguyên văn `quote` từ ngữ cảnh được cấp. Sau khi LLM sinh câu trả lời, module `CitationVerifier` (`backend/app/rag/verifier.py`) thực thi hậu kiểm bắt buộc:
  1. **Kiểm tra Whitelist `chunk_id`**: Mọi `chunk_id` không nằm trong tập chunk đã truy xuất hợp lệ của lượt hỏi đó đều bị từ chối và ghi vào `rejected_chunk_ids`.
  2. **Kiểm tra độ khớp nội dung (`QUOTE_OVERLAP_THRESHOLD >= 0.50`)**: Đối chiếu trùng khớp từ vựng/ngữ nghĩa giữa `quote`/nhận định và `chunk.content` gốc trong DB; loại bỏ tuyên bố sai lệch số liệu hoặc bịa đặt.
  3. **Ghi đè metadata chuẩn từ DB**: `document_id`, `file_name`, `page_or_sheet` (`Page 4`, `Section 1: ...`, `Sheet: Bang_Luong_Cap_Cao (Rows 1-15)`) và `score` được lấy trực tiếp từ bản ghi PostgreSQL, không tin tưởng raw output của LLM.
  4. **Fallback an toàn**: Nếu sau khi lọc không còn trích dẫn hợp lệ nào, hệ thống tự động chuyển về Kịch bản 1 (`found: false`).

### Kịch bản 3: Người dùng không có quyền (`Pre-Retrieval SQL RBAC & Zero-Leakage`)
- **Cơ chế**: Bộ lọc quyền truy cập theo **Role** (`admin`, `manager`, `employee`, `guest`), **Department** (`HR`, `Finance`, `Legal`, `Engineering`, `General`), **Access Level** (`public`, `department`, `restricted`), `allowed_roles`, và bảng `document_permissions` được thực thi **ngay trong mệnh đề `WHERE` của câu lệnh SQL** (`backend/app/rag/retriever.py`) **TRƯỚC** bước xếp hạng khoảng cách vector (`pgvector <=>`).
- **Đảm bảo Zero-Leakage**:
  - Tài liệu không có quyền **không bao giờ** rời khỏi database, không vào RAM ứng dụng Python và không vào LLM prompt.
  - Trên `POST /api/v1/chat/query`: Truy vấn vào tài liệu mật/khác phòng ban trả về kết quả giống hệt Kịch bản 1 (`found: false`), không tiết lộ sự tồn tại, tên file hay số lượng tài liệu.
  - Trên `GET/PATCH/DELETE /api/v1/documents/{doc_id}`: Trả về `404 Not Found` đồng nhất với UUID không tồn tại (không trả `403` để chống dò tìm tài liệu).

---

## 3. Hướng dẫn Khởi chạy Nhanh (Quick Start)

### Cách 1: Chạy toàn bộ hệ thống với Docker Compose (Khuyên dùng)

```bash
# 1. Sao chép file biến môi trường mẫu (mặc định chạy offline với LLM_PROVIDER=mock)
cp .env.example .env

# 2. Build và khởi chạy 3 services: db (pgvector), backend (FastAPI), frontend (Nginx + React)
docker compose up --build
```

Sau khi khởi chạy thành công:
- **Frontend UI (React SPA)**: http://localhost:3000
- **Backend API & Swagger Docs**: http://localhost:8000/docs
- **Healthcheck Endpoint**: http://localhost:8000/api/v1/health

Dừng và giữ nguyên dữ liệu volume:
```bash
docker compose down
```

### Cách 2: Chạy phát triển cục bộ (Local Development)

#### Yêu cầu môi trường
- Python **3.11+**, Node.js **20+**, và PostgreSQL 16 + `pgvector` (có thể chỉ bật service `db` bằng Docker: `docker compose up -d db`).

#### 1. Khởi chạy Backend (FastAPI)
```bash
# Khởi động PostgreSQL + pgvector
docker compose up -d db

# Cài đặt thư viện Python
cd backend
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt

# (Tùy chọn) Chạy migration Alembic
alembic upgrade head

# Chạy FastAPI server (tự động seed 5 phòng ban, 7 tài khoản demo và 3 tài liệu mẫu khi SEED_ON_STARTUP=true)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

#### 2. Khởi chạy Frontend (React + Vite)
```bash
cd frontend
npm install
npm run dev
```
Truy cập giao diện dev tại http://localhost:5173 (Vite tự động proxy `/api` sang `http://localhost:8000`).

---

## 4. Chuyển đổi nhà cung cấp LLM & Embedding (`mock` | `openai` | `gemini`)

Hệ thống chuẩn hóa không gian vector về **`768` chiều (`vector(768)`)** trên cả 3 provider. Chỉnh sửa file `.env` và khởi động lại backend:

| Chế độ | Biến môi trường cần thiết trong `.env` | Ghi chú |
| :--- | :--- | :--- |
| **Offline / CI Mock** (Mặc định) | `LLM_PROVIDER=mock`<br>`EMBEDDING_PROVIDER=mock` | Không cần API key; sử dụng feature-hashing hỗ trợ tiếng Việt 768-dim và bộ sinh câu trả lời grounded tất định |
| **OpenAI API** | `LLM_PROVIDER=openai`<br>`EMBEDDING_PROVIDER=openai`<br>`OPENAI_API_KEY=sk-...`<br>`OPENAI_LLM_MODEL=gpt-4o-mini`<br>`OPENAI_EMBEDDING_MODEL=text-embedding-3-small` | `text-embedding-3-small` được cấu hình `dimensions=768` để tương thích cột `vector(768)` |
| **Google Gemini API** | `LLM_PROVIDER=gemini`<br>`EMBEDDING_PROVIDER=gemini`<br>`GEMINI_API_KEY=...`<br>`GEMINI_LLM_MODEL=gemini-2.0-flash`<br>`GEMINI_EMBEDDING_MODEL=models/text-embedding-004` | `models/text-embedding-004` xuất trực tiếp vector 768 chiều |

> **Lưu ý quan trọng**: Khi thay đổi `EMBEDDING_PROVIDER` trên database đã có tài liệu, cần re-index (upload lại hoặc re-embed) các `document_chunks` để vector truy vấn và vector lưu trữ nằm trên cùng một không gian biểu diễn.

---

## 5. Danh sách Tài khoản Demo được Seed tự động (`backend/app/db/seed.py`)

Khi `SEED_ON_STARTUP=true`, hệ thống tự động tạo idempotent 5 phòng ban (`HR`, `Finance`, `Legal`, `Engineering`, `General`), **7 tài khoản demo** (mật khẩu chung: **`Password123!`**), và 3 tài liệu mẫu (`public`, `department`, `restricted`):

| Email | Username | Vai trò (`role`) | Phòng ban (`department`) | Mật khẩu | Phạm vi truy cập tiêu biểu |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `admin@company.vn` | `admin` | `admin` | `General` | `Password123!` | Toàn quyền quản trị người dùng, tài liệu, ACL và tra cứu mọi phòng ban |
| `hr.manager@company.vn` | `hr.manager` | `manager` | `HR` | `Password123!` | Tra cứu tài liệu `public`, phòng `HR`, và tài liệu `restricted` mật (`Chinh_sach_luong_thuong_cap_cao_HR.xlsx`) |
| `hr.employee@company.vn` | `hr.employee` | `employee` | `HR` | `Password123!` | Tra cứu tài liệu `public` và phòng `HR` (bị chặn tài liệu `restricted` chỉ dành cho `admin`/`manager`) |
| `finance.manager@company.vn` | `finance.manager` | `manager` | `Finance` | `Password123!` | Upload/quản lý và tra cứu tài liệu phòng `Finance` + `public`; bị cô lập khỏi `HR`/`Engineering` |
| `legal.manager@company.vn` | `legal.manager` | `manager` | `Legal` | `Password123!` | Upload/quản lý và tra cứu tài liệu phòng `Legal` + `public`; bị cô lập khỏi phòng khác |
| `eng.employee@company.vn` | `eng.employee` | `employee` | `Engineering` | `Password123!` | Tra cứu `Quy_che_lam_viec_chung.pdf` (`public`) và `Huong_dan_ky_thuat_he_thong.docx` (`Engineering`); bị chặn tài liệu mật HR |
| `guest@company.vn` | `guest` | `guest` | `General` | `Password123!` | Chỉ tra cứu tài liệu `public` cho phép `guest`; bị chặn toàn bộ tài liệu `department` và `restricted` |

---

## 6. Kiểm thử & Đánh giá RAG (Testing & Evaluation)

### Lệnh chạy kiểm thử
```bash
# Chạy toàn bộ bộ test tự động (API, Parsers, Red-Team Security, Scenarios 1, 2, 3)
PYTHONPATH=backend pytest tests/ -v

# Chạy benchmark đánh giá định lượng RAG & Security (xuất kết quả ra tests/rag_eval_report.json)
PYTHONPATH=backend python tests/eval_rag.py
```

### Kết quả kiểm chứng thực tế (Phase 5 Sign-off)

#### 1. Bộ kiểm thử Pytest (`51 passed`, `0 failed`, `0 skipped` trong `2.71s`)
- `tests/test_api_endpoints.py`: **5 passed** (Health, Auth/JWT, Users RBAC, Multipart Upload PDF/DOCX/XLSX, Chat & Sessions)
- `tests/test_ingestion_parsers.py`: **5 passed** (PDF page parser, DOCX heading/table parser, XLSX sheet/header-preserving parser, `LlamaIndexChunker`, `IngestionService`)
- `tests/test_redteam_security.py`: **17 passed** (Prompt injection/jailbreak VI & EN, SQL injection, forged/tampered/expired JWT, privilege escalation, invalid/empty file uploads)
- `tests/test_scenario1_not_found.py`: **7 passed** (Out-of-domain abstention, exact `ABSTENTION_MESSAGE_VI`, zero citations, 2–3 `suggested_queries`, threshold cutoff)
- `tests/test_scenario2_citation_verification.py`: **8 passed** (DB-authoritative citations, rejecting fabricated UUIDs, rejecting unauthorized chunk IDs, rejecting fabricated numbers, stripping unverified claims)
- `tests/test_scenario3_rbac_unauthorized.py`: **9 passed** (SQLAlchemy `before_cursor_execute` SQL `WHERE` pre-retrieval proof, cross-department & role isolation, identical `404 Not Found` zero-leakage)

#### 2. Benchmark Đánh giá RAG & Bảo mật (`python tests/eval_rag.py` — `18/18 passed`)
| Chỉ số đánh giá (Metric) | Kết quả thực tế |
| :--- | :--- |
| **Scenario 1 Abstention Accuracy (%)** | **100.00%** (4/4) |
| **Scenario 2 Citation Precision & Verification Accuracy (%)** | **100.00%** (6/6 citations, 6/6 queries) |
| **Scenario 3 RBAC Pre-Retrieval Zero-Leakage Rate (%)** | **100.00%** (5/5) |
| **Red-Team Prompt Injection Block Rate (%)** | **100.00%** (3/3) |
| **Overall Answer Groundedness (%)** | **100.00%** (18/18) |

---

## 7. Cấu trúc Thư mục & Tài liệu Kỹ thuật

```text
enterprise-knowledge-assistant/
├── ASSUMPTIONS.md                  # Giả định kỹ thuật & mặc định Phase 0
├── README.md                       # Tài liệu tổng quan & hướng dẫn chạy nhanh
├── docker-compose.yml              # Điều phối 3 container: db, backend, frontend
├── .env.example                    # Mẫu toàn bộ biến môi trường hệ thống
├── .github/workflows/ci.yml        # GitHub Actions CI (Pytest+pgvector, TS build, Compose)
├── docs/
│   ├── ADR.md                      # Architectural Decision Records (ADR-001..004)
│   ├── ARCHITECTURE.md             # Kiến trúc hệ thống & sơ đồ luồng dữ liệu Mermaid
│   ├── ERD.md                      # Lược đồ cơ sở dữ liệu 8 bảng & SQL DDL pgvector
│   ├── openapi.yaml                # Đặc tả REST API OpenAPI 3.1.0
│   ├── CONTRACTS.md                # Giao diện module Python & Pydantic v2 contracts
│   ├── DEPLOYMENT.md               # Hướng dẫn triển khai Production & vận hành Runbook
│   ├── KNOWN_LIMITATIONS.md        # Giới hạn hiện tại & lộ trình nâng cấp v2
│   └── REVIEWS.md                  # Biên bản nghiệm thu kiểm duyệt từng giai đoạn
├── infra/
│   ├── backend.Dockerfile          # Dockerfile Python 3.11 + FastAPI + LlamaIndex
│   ├── frontend.Dockerfile         # Multi-stage Node 20 -> Nginx Alpine SPA
│   ├── nginx.conf                  # Cấu hình Nginx SPA + reverse proxy /api (55MB limit)
│   ├── postgres/init.sql           # Khởi tạo extension uuid-ossp, vector & schema DDL
│   └── scripts/                    # entrypoint.sh & healthcheck.sh
├── backend/
│   ├── alembic/                    # Migrations PostgreSQL + pgvector
│   ├── requirements.txt            # Python dependencies
│   └── app/
│       ├── core/                   # Config, JWT security, exception handlers
│       ├── db/                     # SQLAlchemy session, 8 ORM models, seed.py
│       ├── schemas/                # Pydantic v2 request/response schemas
│       ├── api/                    # FastAPI dependencies & /api/v1 routers
│       ├── ingestion/              # Parsers (PDF/DOCX/XLSX), LlamaIndex chunker, service
│       └── rag/                    # Providers (Mock/OpenAI/Gemini), RBAC retriever, verifier, service
├── frontend/                       # React 18 + TypeScript + Vite SPA
└── tests/                          # Pytest suite (51 tests) & eval_rag.py benchmark
```

### Danh mục tài liệu tham khảo
- [Giả định & Mặc định kỹ thuật (`ASSUMPTIONS.md`)](./ASSUMPTIONS.md)
- [Quyết định Kiến trúc (`docs/ADR.md`)](./docs/ADR.md)
- [Kiến trúc Hệ thống & Luồng RAG (`docs/ARCHITECTURE.md`)](./docs/ARCHITECTURE.md)
- [Thiết kế Cơ sở dữ liệu & pgvector (`docs/ERD.md`)](./docs/ERD.md)
- [Đặc tả OpenAPI 3.1 (`docs/openapi.yaml`)](./docs/openapi.yaml)
- [Hợp đồng Giao diện Module (`docs/CONTRACTS.md`)](./docs/CONTRACTS.md)
- [Hướng dẫn Triển khai & Vận hành (`docs/DEPLOYMENT.md`)](./docs/DEPLOYMENT.md)
- [Giới hạn Hiện tại & Lộ trình v2 (`docs/KNOWN_LIMITATIONS.md`)](./docs/KNOWN_LIMITATIONS.md)
- [Biên bản Kiểm duyệt các Giai đoạn (`docs/REVIEWS.md`)](./docs/REVIEWS.md)
