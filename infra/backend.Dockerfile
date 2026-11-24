# ==============================================================================
# Enterprise Knowledge Assistant — Backend Dockerfile (FastAPI + LlamaIndex)
# Python 3.11+ with PostgreSQL/pgvector driver & document ingestion dependencies
# Build context: repository root (".") as configured in docker-compose.yml
# ==============================================================================
FROM python:3.11-slim

LABEL maintainer="Enterprise Knowledge Assistant DevOps" \
      service="backend"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app \
    UPLOAD_DIR=/data/uploads \
    LLM_PROVIDER=mock \
    EMBEDDING_PROVIDER=mock

WORKDIR /app

# Install OS-level dependencies required by psycopg, pypdf/docx/xlsx parsers, and curl healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        libpq-dev \
        postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy only requirements.txt first for Docker layer caching
COPY backend/requirements.txt /app/requirements.txt

RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.txt

# Copy explicit backend application files, Alembic migrations, and helper scripts
COPY backend/pyproject.toml backend/alembic.ini /app/
COPY backend/alembic /app/alembic
COPY backend/app /app/app
COPY infra/scripts/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY infra/scripts/healthcheck.sh /usr/local/bin/healthcheck.sh

# Normalize line endings (CRLF -> LF for Windows checkouts), set executable bits, prepare upload dirs
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh /usr/local/bin/healthcheck.sh && \
    chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/healthcheck.sh && \
    mkdir -p /data/uploads /app/data/uploads

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD ["/usr/local/bin/healthcheck.sh"]

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
