#!/usr/bin/env sh
# ==============================================================================
# Enterprise Knowledge Assistant — Backend Container Entrypoint Script
# Initializes upload directories, verifies PostgreSQL + pgvector readiness,
# optionally runs Alembic migrations, and launches the FastAPI application.
# ==============================================================================
set -e

UPLOAD_PATH="${UPLOAD_DIR:-/data/uploads}"
mkdir -p "$UPLOAD_PATH"

echo "[entrypoint] Enterprise Knowledge Assistant starting..."
echo "[entrypoint] Upload directory: $UPLOAD_PATH"
echo "[entrypoint] LLM_PROVIDER=${LLM_PROVIDER:-mock} | EMBEDDING_PROVIDER=${EMBEDDING_PROVIDER:-mock}"

# Wait for PostgreSQL connection if DATABASE_URL points to a PostgreSQL host
if [ -n "$DATABASE_URL" ]; then
    echo "[entrypoint] Verifying database connectivity..."
    python3 -c "
import os, sys, time
from sqlalchemy import create_engine, text

db_url = os.environ.get('DATABASE_URL', '')
if db_url.startswith('postgresql'):
    for attempt in range(1, 31):
        try:
            engine = create_engine(db_url, pool_pre_ping=True)
            with engine.connect() as conn:
                conn.execute(text('SELECT 1'))
            print(f'[entrypoint] Database reachable on attempt {attempt}.')
            sys.exit(0)
        except Exception as exc:
            print(f'[entrypoint] Waiting for database (attempt {attempt}/30): {exc}')
            time.sleep(1)
    print('[entrypoint] ERROR: Database did not become reachable in time.', file=sys.stderr)
    sys.exit(1)
"
fi

# Optionally run Alembic migrations when RUN_MIGRATIONS=true
if [ "${RUN_MIGRATIONS:-false}" = "true" ] && [ -f "alembic.ini" ]; then
    echo "[entrypoint] Running Alembic migrations..."
    alembic upgrade head || echo "[entrypoint] Warning: Alembic migration skipped or already applied."
fi

# Default command if none passed
if [ "$#" -eq 0 ]; then
    set -- uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
fi

echo "[entrypoint] Executing: $*"
exec "$@"
