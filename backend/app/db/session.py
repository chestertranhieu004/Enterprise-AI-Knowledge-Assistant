# File: backend/app/db/session.py
import logging
from collections.abc import Generator
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from app.core.config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


def build_engine(database_url: str | None = None) -> Engine:
    """Create SQLAlchemy engine with automatic driver fallback if psycopg is absent locally."""
    url = database_url or settings.DATABASE_URL
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    try:
        return create_engine(url, pool_pre_ping=True, connect_args=connect_args)
    except ModuleNotFoundError as exc:
        if "psycopg" in str(exc) and url.startswith("postgresql+psycopg://"):
            alt_url = url.replace("postgresql+psycopg://", "postgresql+psycopg2://", 1)
            try:
                return create_engine(alt_url, pool_pre_ping=True)
            except ModuleNotFoundError:
                pass
        logger.warning(
            "Database driver for '%s' not installed (%s); falling back to local SQLite engine.",
            url,
            exc,
        )
        return create_engine(
            "sqlite:///./eka_fallback.db",
            pool_pre_ping=True,
            connect_args={"check_same_thread": False},
        )


engine = build_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
