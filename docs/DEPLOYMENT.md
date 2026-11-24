# Deployment & Operations Runbook (`docs/DEPLOYMENT.md`)

> **Project**: Enterprise Knowledge Assistant  
> **Scope**: Local & Production Deployment, Security Hardening, Database/Alembic Management, Nginx Reverse Proxy, Healthchecks, Backup/Restore, and Troubleshooting  
> **Owner**: `@devops`

---

## 1. Deployment Architecture Overview

The system deploys as three containerized services orchestrated by `docker-compose.yml` over an isolated Docker bridge network (`eka_network`):

| Service | Container Name | Image / Build | Internal Port | Host Port (Default) | Persistence / Mounts | Healthcheck |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`db`** | `eka-postgres-pgvector` | `pgvector/pgvector:pg16` | `5432` | `${POSTGRES_PORT:-5432}` | `postgres_data:/var/lib/postgresql/data`<br>`./infra/postgres/init.sql:/docker-entrypoint-initdb.d/01-init.sql:ro` | `pg_isready -U eka_user -d eka_db` |
| **`backend`** | `eka-backend` | `./infra/backend.Dockerfile` (Python 3.11 + FastAPI + LlamaIndex) | `8000` | `${BACKEND_PORT:-8000}` | `upload_data:/data/uploads` | `curl -fsS http://localhost:8000/api/v1/health` |
| **`frontend`** | `eka-frontend` | `./infra/frontend.Dockerfile` (Node 20 builder -> Nginx 1.27 Alpine) | `80` | `${FRONTEND_PORT:-3000}` | Static SPA bundle + `./infra/nginx.conf` | `curl -fsS http://localhost:80/healthz` |

Startup order is enforced via `depends_on` with `condition: service_healthy`:
`db (healthy)` $\rightarrow$ `backend (healthy)` $\rightarrow$ `frontend (healthy)`.

---

## 2. Prerequisites

- **Docker Engine** 24+ and **Docker Compose v2** (`docker compose version`)
- Minimum host sizing:
  - **Local / Dev (`mock` mode)**: 2 vCPU, 2 GB RAM, 5 GB disk
  - **Production (OpenAI / Gemini API + pgvector HNSW)**: 4+ vCPU, 8+ GB RAM, 50+ GB SSD (scales with `document_chunks` count and uploaded document volume)
- Outbound HTTPS (port `443`) access from `backend` container when using `LLM_PROVIDER=openai|gemini` or `EMBEDDING_PROVIDER=openai|gemini`.

---

## 3. Step-by-Step Deployment

### 3.1 Local / Staging Quick Deployment (Offline Mock Mode)

```bash
# 1. Clone or enter project root
cd enterprise-knowledge-assistant

# 2. Create environment configuration from template
cp .env.example .env

# 3. Validate Docker Compose manifest before launching
docker compose config --quiet

# 4. Build images and start all 3 services in detached mode
docker compose up -d --build

# 5. Monitor container health status until all 3 report "(healthy)"
docker compose ps
```

Verify endpoints:
```bash
# Backend health & pgvector verification
curl -s http://localhost:8000/api/v1/health

# Frontend Nginx healthcheck
curl -s http://localhost:3000/healthz
```

Expected `GET /api/v1/health` JSON payload:
```json
{
  "status": "ok",
  "database": "connected",
  "pgvector": "enabled",
  "llm_provider": "mock",
  "embedding_provider": "mock",
  "timestamp": "2026-11-24T00:00:00Z"
}
```

---

### 3.2 Production Deployment & `.env` Security Hardening

Before exposing the application to production traffic, update `.env` with hardened values (never commit `.env` to Git — it is excluded by `.gitignore`):

#### Mandatory Security Checklist for `.env`
1. **Environment Mode**:
   ```env
   ENVIRONMENT=production
   ```
2. **Strong Cryptographic `JWT_SECRET_KEY`** (minimum 256-bit / 64 hex characters):
   ```bash
   python3 -c "import secrets; print(secrets.token_hex(32))"
   ```
   Set the output in `.env`:
   ```env
   JWT_SECRET_KEY=<generated-64-char-hex-secret>
   JWT_ALGORITHM=HS256
   ACCESS_TOKEN_EXPIRE_MINUTES=480
   ```
3. **Strong PostgreSQL Credentials** (replace default `eka_password`):
   ```env
   POSTGRES_USER=eka_prod_user
   POSTGRES_PASSWORD=<strong-random-password>
   POSTGRES_DB=eka_prod_db
   DATABASE_URL=postgresql+psycopg://eka_prod_user:<strong-random-password>@db:5432/eka_prod_db
   ```
4. **Disable Automatic Demo Seeding in Production** (after initial admin provisioning):
   ```env
   SEED_ON_STARTUP=false
   ```
   > **Warning**: Keeping `SEED_ON_STARTUP=true` creates 7 demo users with the known password `Password123!`. In production, either set `SEED_ON_STARTUP=false` or immediately rotate/deactivate all seeded demo accounts via `PATCH /api/v1/users/{user_id}/permissions` and password updates.
5. **Configure Real LLM & Embedding Provider**:
   - **OpenAI**:
     ```env
     LLM_PROVIDER=openai
     EMBEDDING_PROVIDER=openai
     OPENAI_API_KEY=sk-proj-...
     OPENAI_LLM_MODEL=gpt-4o-mini
     OPENAI_EMBEDDING_MODEL=text-embedding-3-small
     EMBEDDING_DIM=768
     ```
   - **Google Gemini**:
     ```env
     LLM_PROVIDER=gemini
     EMBEDDING_PROVIDER=gemini
     GEMINI_API_KEY=AIza...
     GEMINI_LLM_MODEL=gemini-2.0-flash
     GEMINI_EMBEDDING_MODEL=models/text-embedding-004
     EMBEDDING_DIM=768
     ```
6. **Guardrail Threshold Calibration**:
   ```env
   SIMILARITY_THRESHOLD=0.65
   DEFAULT_TOP_K=5
   QUOTE_OVERLAP_THRESHOLD=0.50
   MAX_UPLOAD_SIZE_MB=50
   INGESTION_MODE=sync
   ```
7. **Network Exposure & TLS Termination**:
   - In production, bind PostgreSQL (`5432`) and Backend (`8000`) to `127.0.0.1` (or remove host port mappings) and place an external TLS-terminating reverse proxy / load balancer (e.g., Traefik, Caddy, AWS ALB, or enterprise Nginx with Let's Encrypt/corporate PKI) in front of `frontend:80`.

---

## 4. PostgreSQL 16 + `pgvector` Initialization & Alembic Migrations

### 4.1 Automatic Initialization on First Boot
When the `postgres_data` volume is empty, the `pgvector/pgvector:pg16` container automatically executes `./infra/postgres/init.sql` (mounted read-only at `/docker-entrypoint-initdb.d/01-init.sql`), which:
1. Enables extensions `uuid-ossp` and `vector`.
2. Creates all 8 tables (`departments`, `users`, `documents`, `document_permissions`, `document_chunks`, `chat_sessions`, `chat_messages`, `audit_logs`) matching `docs/ERD.md`.
3. Builds B-Tree RBAC indexes (`idx_documents_rbac_filter`, `idx_doc_perms_lookup`), GIN index (`idx_documents_allowed_roles_gin`), and pgvector HNSW index (`idx_document_chunks_embedding_hnsw` using `vector_cosine_ops` with `m = 16, ef_construction = 64`).
4. Idempotently seeds the 5 baseline department codes (`HR`, `Finance`, `Legal`, `Engineering`, `General`).

Verify extensions and indexes inside PostgreSQL:
```bash
docker compose exec db psql -U "${POSTGRES_USER:-eka_user}" -d "${POSTGRES_DB:-eka_db}" -c "\dx"
docker compose exec db psql -U "${POSTGRES_USER:-eka_user}" -d "${POSTGRES_DB:-eka_db}" -c "\dt"
```

### 4.2 Running Alembic Migrations (`alembic upgrade head`)
Alembic configuration lives at `backend/alembic.ini` and `backend/alembic/versions/001_initial_schema.py`.

- **Manual execution inside running backend container**:
  ```bash
  docker compose exec backend alembic current
  docker compose exec backend alembic upgrade head
  ```
- **Automatic execution at container startup**:
  The backend entrypoint (`infra/scripts/entrypoint.sh`) supports `RUN_MIGRATIONS=true`. Set `RUN_MIGRATIONS=true` in the backend service environment to run `alembic upgrade head` automatically after confirming PostgreSQL reachability.
- **Local host execution (against local Postgres)**:
  ```bash
  cd backend
  DATABASE_URL=postgresql+psycopg://eka_user:eka_password@localhost:5432/eka_db alembic upgrade head
  ```

---

## 5. Nginx Reverse Proxy & 50MB Document Upload Configuration

The `frontend` container uses `infra/nginx.conf` to serve the built React SPA and reverse-proxy API requests to `http://backend:8000`:

| Nginx Location | Target / Behavior | Purpose |
| :--- | :--- | :--- |
| `= /healthz` | Returns `200 {"status":"ok","service":"frontend"}` | Docker container healthcheck (access log disabled) |
| `/api/` | `proxy_pass http://backend:8000/api/` | All `/api/v1/*` REST & multipart requests; `proxy_read_timeout 180s` for large synchronous PDF/DOCX/XLSX ingestion |
| `/docs`, `/openapi.json` | Proxied to `backend:8000` | Swagger UI & OpenAPI schema access |
| `/assets/` | Static files with `7d` immutable cache | Fingerprinted Vite JS/CSS bundles |
| `/` | `try_files $uri $uri/ /index.html` | React Router SPA fallback |

### Upload Body Size Alignment
- Application limit (`MAX_UPLOAD_SIZE_MB`): **50 MB** (`52,428,800` bytes enforced by FastAPI & PostgreSQL `CHECK (file_size_bytes <= 52428800)`).
- Nginx limit (`infra/nginx.conf`): **`client_max_body_size 55M;`** (5 MB headroom for multipart form boundaries and metadata headers).
- If placing an external corporate reverse proxy or Ingress controller in front of `frontend`, ensure it also permits at least `55M` request body size and `>= 180s` upstream read timeout.

---

## 6. Healthchecks & Operational Monitoring

### 6.1 Built-in Container Probes
1. **Database (`db`)**:
   - Command: `pg_isready -U ${POSTGRES_USER:-eka_user} -d ${POSTGRES_DB:-eka_db}`
   - Interval: `5s` | Timeout: `5s` | Retries: `10`
2. **Backend (`backend`)**:
   - Script: `infra/scripts/healthcheck.sh` (and Compose `curl -fsS http://localhost:8000/api/v1/health`)
   - Probes live DB `SELECT 1`, verifies `pg_extension WHERE extname = 'vector'`, and reports active `llm_provider` / `embedding_provider`.
   - Interval: `15s` | Timeout: `5s` | Start period: `20s` | Retries: `5`
3. **Frontend (`frontend`)**:
   - Command: `curl -fsS http://localhost:80/healthz`
   - Interval: `15s` | Timeout: `5s` | Start period: `10s` | Retries: `3`

### 6.2 Inspecting Logs & Service Status
```bash
# View container health and port bindings
docker compose ps

# Follow logs across all services or a single service
docker compose logs -f --tail=100
docker compose logs -f backend

# Inspect healthcheck history for backend
docker inspect --format='{{json .State.Health}}' eka-backend
```

---

## 7. Backup & Restore Procedures

Both PostgreSQL metadata/vectors (`eka_postgres_data`) and raw uploaded files (`eka_upload_data`) must be backed up consistently.

### 7.1 Backup
```bash
# Create timestamped backup directory
BACKUP_DIR="./backups/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"

# 1. Logical PostgreSQL + pgvector dump (custom compressed format)
docker compose exec -T db pg_dump \
  -U "${POSTGRES_USER:-eka_user}" \
  -d "${POSTGRES_DB:-eka_db}" \
  --format=custom \
  --no-owner \
  > "$BACKUP_DIR/eka_db.dump"

# 2. Archive uploaded files volume (/data/uploads)
docker run --rm \
  -v eka_upload_data:/source:ro \
  -v "$(pwd)/$BACKUP_DIR:/backup" \
  alpine tar czf /backup/uploads.tar.gz -C /source .
```

### 7.2 Restore
```bash
# Set path to backup directory
BACKUP_DIR="./backups/<YYYYMMDD_HHMMSS>"

# 1. Stop backend & frontend to prevent writes during restore
docker compose stop backend frontend

# 2. Restore PostgreSQL database (ensure pgvector extension exists)
docker compose exec -T db psql -U "${POSTGRES_USER:-eka_user}" -d "${POSTGRES_DB:-eka_db}" \
  -c 'CREATE EXTENSION IF NOT EXISTS "uuid-ossp"; CREATE EXTENSION IF NOT EXISTS "vector";'

docker compose exec -T db pg_restore \
  -U "${POSTGRES_USER:-eka_user}" \
  -d "${POSTGRES_DB:-eka_db}" \
  --clean --if-exists --no-owner \
  < "$BACKUP_DIR/eka_db.dump"

# 3. Restore uploaded files volume
docker run --rm \
  -v eka_upload_data:/target \
  -v "$(pwd)/$BACKUP_DIR:/backup:ro" \
  alpine sh -c "rm -rf /target/* && tar xzf /backup/uploads.tar.gz -C /target"

# 4. Restart application services and verify health
docker compose start backend frontend
curl -fsS http://localhost:8000/api/v1/health
```

---

## 8. Troubleshooting Guide

| Symptom | Likely Cause | Resolution |
| :--- | :--- | :--- |
| **`backend` fails to start with `connection refused` to `db:5432`** | `db` container still initializing or `.env` has `DATABASE_URL` pointing to `localhost` instead of `db` | Verify `docker compose ps` shows `db` healthy. Inside Docker Compose, `docker-compose.yml` overrides `DATABASE_URL` to use host `@db:5432`; ensure `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB` match across `db` and `backend`. |
| **`GET /api/v1/health` shows `"pgvector": "unavailable"`** | Database volume was created using a standard `postgres:16` image instead of `pgvector/pgvector:pg16`, or `init.sql` did not run | Verify `db` image is `pgvector/pgvector:pg16`. Run `CREATE EXTENSION IF NOT EXISTS "vector";` via `psql` or reset volume with `docker compose down -v && docker compose up -d`. |
| **`413 Request Entity Too Large` on document upload** | Uploaded file exceeds 50 MB limit or an upstream proxy has default `1m` Nginx limit | Verify file is `<= 50 MB` (`MAX_UPLOAD_SIZE_MB=50`) and that any external proxy in front of `frontend` sets `client_max_body_size 55M;`. |
| **`504 Gateway Timeout` on large PDF/XLSX upload** | Synchronous ingestion (`INGESTION_MODE=sync`) took longer than upstream proxy timeout | Switch to `INGESTION_MODE=background` (or pass `async_processing=true` on upload) or increase `proxy_read_timeout` in `infra/nginx.conf`. |
| **Chat queries return `found: false` after switching `EMBEDDING_PROVIDER`** | Existing `document_chunks.embedding` vectors were generated by a different embedding model (e.g., `mock-768` vs `openai` or `gemini`), causing mismatched vector spaces | Re-upload or re-index existing documents so all `document_chunks` embeddings match the active `EMBEDDING_PROVIDER`. |
| **Windows CRLF line-ending errors (`exec /usr/local/bin/entrypoint.sh: no such file or directory`)** | Shell scripts checked out with Windows `CRLF` line endings | `infra/backend.Dockerfile` automatically strips `\r` via `sed -i 's/\r$//'`. If mounting scripts locally, convert to LF (`dos2unix infra/scripts/*.sh`). |
| **`frontend` cannot reach `/api/v1/*` in local dev** | Backend not running on port `8000` or mismatched `VITE_API_BASE_URL` | Ensure `uvicorn app.main:app --port 8000` is active; Vite proxies `/api` to `http://localhost:8000` per `frontend/vite.config.ts`. |
