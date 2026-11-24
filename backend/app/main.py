# File: backend/app/main.py
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.db.models import Base
from app.db.seed import seed_database
from app.db.session import SessionLocal, engine

logger = logging.getLogger(__name__)


def init_db_and_seed() -> None:
    """Initialize database extensions, tables, indexes, and optional default seed data."""
    try:
        Path(settings.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
        with engine.begin() as conn:
            if conn.dialect.name == "postgresql":
                conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";'))
                conn.execute(text('CREATE EXTENSION IF NOT EXISTS "vector";'))

        Base.metadata.create_all(bind=engine)

        with engine.begin() as conn:
            if conn.dialect.name == "postgresql":
                conn.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS idx_documents_allowed_roles_gin "
                        "ON documents USING GIN (allowed_roles);"
                    )
                )
                conn.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS idx_document_chunks_embedding_hnsw "
                        "ON document_chunks USING hnsw (embedding vector_cosine_ops) "
                        "WITH (m = 16, ef_construction = 64);"
                    )
                )

        if settings.SEED_ON_STARTUP:
            with SessionLocal() as db:
                counts = seed_database(db)
                logger.info("Database seed completed: %s", counts)
    except Exception as exc:
        logger.warning(
            "Startup database initialization skipped or deferred (%s)",
            exc,
        )


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    init_db_and_seed()
    yield


def create_app() -> FastAPI:
    application = FastAPI(
        title=settings.PROJECT_NAME,
        version="1.0.0",
        description=(
            "Grounded Enterprise Knowledge Assistant API with Pre-Retrieval RBAC "
            "and Post-Generation Citation Verification"
        ),
        openapi_url=f"{settings.API_V1_STR}/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(application)
    application.include_router(api_router, prefix=settings.API_V1_STR)

    @application.get("/", tags=["Root"])
    def root() -> dict[str, str]:
        return {
            "service": settings.PROJECT_NAME,
            "version": "1.0.0",
            "docs": "/docs",
            "health": f"{settings.API_V1_STR}/health",
        }

    return application


app = create_app()
