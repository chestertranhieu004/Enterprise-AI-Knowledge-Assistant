# File: backend/app/api/v1/health.py
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.schemas.common import HealthResponse

router = APIRouter(tags=["Health"])


@router.get("/health", response_model=HealthResponse, summary="Health & readiness check")
def get_health(db: Session = Depends(get_db)) -> HealthResponse:
    db_status = "disconnected"
    pgvector_status = "unavailable"

    try:
        db.execute(text("SELECT 1"))
        db_status = "connected"
        dialect_name = db.get_bind().dialect.name
        if dialect_name == "postgresql":
            ext_row = db.execute(
                text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
            ).first()
            pgvector_status = "enabled" if ext_row is not None else "unavailable"
        else:
            # Local SQLite fallback used in unit testing
            pgvector_status = "enabled"
    except Exception:
        db_status = "disconnected"
        pgvector_status = "unavailable"

    overall_status = "ok" if db_status == "connected" else "degraded"

    return HealthResponse(
        status=overall_status,
        database=db_status,
        pgvector=pgvector_status,
        llm_provider=settings.LLM_PROVIDER,
        embedding_provider=settings.EMBEDDING_PROVIDER,
        timestamp=datetime.now(timezone.utc),
    )
