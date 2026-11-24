# File: backend/app/ingestion/service.py
from __future__ import annotations

import hashlib
import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from app.ingestion.chunker import (
    ChunkData,
    LlamaIndexChunker,
    ParsedSection,
    parse_text_or_markdown,
)
from app.ingestion.parsers.docx_parser import parse_docx
from app.ingestion.parsers.pdf_parser import parse_pdf
from app.ingestion.parsers.xlsx_parser import parse_xlsx

try:
    from app.schemas.common import DocumentStatus
except Exception:  # pragma: no cover
    from enum import StrEnum

    class DocumentStatus(StrEnum):  # type: ignore[no-redef]
        PROCESSING = "processing"
        READY = "ready"
        FAILED = "failed"

try:
    from sqlalchemy import delete, select
    from sqlalchemy.orm import Session

    _HAS_SQLALCHEMY = True
except Exception:  # pragma: no cover
    delete = None  # type: ignore[assignment]
    select = None  # type: ignore[assignment]
    Session = Any  # type: ignore[assignment,misc]
    _HAS_SQLALCHEMY = False

try:
    from app.core.config import settings
except Exception:  # pragma: no cover

    class _FallbackSettings:
        CHUNK_SIZE: int = 512
        CHUNK_OVERLAP: int = 64
        EMBEDDING_PROVIDER: str = "mock"
        EMBEDDING_DIM: int = 768

    settings = _FallbackSettings()  # type: ignore[assignment]

try:
    from app.db.models import Document, DocumentChunk

    _HAS_ORM_MODELS = True
except Exception:  # pragma: no cover
    _HAS_ORM_MODELS = False

    @dataclass
    class DocumentChunk:  # type: ignore[no-redef]
        document_id: uuid.UUID
        chunk_index: int
        page_or_sheet: str
        content: str
        token_count: int = 0
        metadata_json: dict[str, Any] = field(default_factory=dict)
        embedding: list[float] = field(default_factory=list)
        id: uuid.UUID = field(default_factory=uuid.uuid4)
        created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @dataclass
    class Document:  # type: ignore[no-redef]
        id: uuid.UUID
        title: str
        file_name: str
        file_path: str
        file_type: str
        file_size_bytes: int = 0
        content_hash: str = ""
        department: str = "General"
        access_level: str = "department"
        allowed_roles: list[str] = field(default_factory=lambda: ["admin", "manager", "employee"])
        uploaded_by: uuid.UUID = field(default_factory=uuid.uuid4)
        status: str = DocumentStatus.PROCESSING.value
        error_message: str | None = None
        chunk_count: int = 0
        embedding_provider: str = "mock"
        embedding_model: str = "mock-768"
        is_deleted: bool = False
        created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
        updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
        chunks: list[DocumentChunk] = field(default_factory=list)


if TYPE_CHECKING:
    from app.rag.providers.base import BaseEmbeddingProvider
else:
    try:
        from app.rag.providers.base import BaseEmbeddingProvider
    except Exception:  # pragma: no cover
        BaseEmbeddingProvider = Any  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)


class _FallbackEmbeddingProvider:
    """
    Safe deterministic 768-dim L2-normalized embedding fallback used only if
    `app.rag.providers.factory` is not yet importable.
    """

    provider_name: str = "mock"
    model_name: str = "mock-768"
    dimension: int = 768

    def embed_query(self, text: str) -> list[float]:
        vec = [0.0] * self.dimension
        tokens = (text or "").lower().split()
        if not tokens:
            vec[0] = 1.0
            return vec
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            for i in range(0, len(digest), 4):
                idx = int.from_bytes(digest[i : i + 2], "big") % self.dimension
                sign = 1.0 if (digest[i + 2] % 2 == 0) else -1.0
                vec[idx] += sign * (1.0 + (digest[i + 3] / 255.0))
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [round(v / norm, 6) for v in vec]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(t) for t in texts]


class IngestionResult(BaseModel):
    document_id: uuid.UUID
    status: DocumentStatus
    chunk_count: int
    error_message: str | None = None


class IngestionService:
    def __init__(
        self,
        embedding_provider: BaseEmbeddingProvider | None = None,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
    ) -> None:
        """
        If embedding_provider is None, resolves default via get_embedding_provider()
        from app.rag.providers.factory (with a safe fallback if called before app.rag finishes importing).
        """
        self.chunk_size = chunk_size if chunk_size is not None else settings.CHUNK_SIZE
        self.chunk_overlap = (
            chunk_overlap if chunk_overlap is not None else settings.CHUNK_OVERLAP
        )
        self.chunker = LlamaIndexChunker(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
        )
        self.embedding_provider: Any = embedding_provider
        self._using_fallback_provider: bool = False

        if self.embedding_provider is None:
            self._resolve_embedding_provider()

    def _resolve_embedding_provider(self) -> Any:
        if self.embedding_provider is not None and not self._using_fallback_provider:
            return self.embedding_provider
        try:
            from app.rag.providers.factory import get_embedding_provider

            self.embedding_provider = get_embedding_provider()
            self._using_fallback_provider = False
        except Exception as exc:
            if self.embedding_provider is None:
                logger.warning(
                    "app.rag.providers.factory.get_embedding_provider unavailable (%s); using fallback 768-dim provider.",
                    exc,
                )
                self.embedding_provider = _FallbackEmbeddingProvider()
                self._using_fallback_provider = True
        return self.embedding_provider

    def _parse_file_by_extension(
        self,
        file_path: Path,
        file_type_hint: str = "",
    ) -> list[ParsedSection]:
        ext = file_path.suffix.lower().lstrip(".") or file_type_hint.lower().lstrip(".")
        if ext == "pdf":
            return parse_pdf(file_path)
        if ext == "docx":
            return parse_docx(file_path)
        if ext == "xlsx":
            return parse_xlsx(file_path)
        if ext in ("txt", "md", "markdown"):
            return parse_text_or_markdown(file_path)
        raise ValueError(
            f"Unsupported file format '.{ext}'. Supported formats: .pdf, .docx, .xlsx, .txt, .md"
        )

    def _load_document(self, db: Session, document_id: uuid.UUID) -> Any:
        if _HAS_SQLALCHEMY and _HAS_ORM_MODELS and select is not None and hasattr(db, "scalars"):
            try:
                return db.scalars(select(Document).where(Document.id == document_id)).first()
            except Exception:
                pass
        if hasattr(db, "get"):
            try:
                return db.get(Document, document_id)
            except TypeError:
                return db.get(document_id)
        return None

    def _delete_existing_chunks(self, db: Session, doc: Any) -> None:
        if _HAS_SQLALCHEMY and _HAS_ORM_MODELS and delete is not None and hasattr(db, "execute"):
            db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == doc.id))
            if hasattr(db, "flush"):
                db.flush()
        elif hasattr(db, "delete_chunks"):
            db.delete_chunks(doc.id)
        elif hasattr(doc, "chunks") and isinstance(doc.chunks, list):
            doc.chunks.clear()

    def ingest_document(
        self,
        db: Session,
        document_id: uuid.UUID,
        file_path: str | Path | None = None,
    ) -> IngestionResult:
        """
        1. Loads Document row by document_id from db.
        2. Dispatches file_path (or doc.file_path) to parse_pdf / parse_docx / parse_xlsx / text parser.
        3. Splits sections into ChunkData via LlamaIndexChunker.
        4. Generates 768-dim embeddings via self.embedding_provider.embed_texts([c.content for c in chunks]).
        5. Deletes any old chunks for document_id, bulk inserts DocumentChunk rows.
        6. Updates Document.status = 'ready', Document.chunk_count = len(chunks), and commits.
        7. On exception, sets Document.status = 'failed', Document.error_message = str(exc), commits, and returns IngestionResult.
        """
        doc = self._load_document(db, document_id)
        if doc is None:
            msg = f"Document {document_id} not found in database"
            logger.error(msg)
            return IngestionResult(
                document_id=document_id,
                status=DocumentStatus.FAILED,
                chunk_count=0,
                error_message=msg,
            )

        try:
            resolved_path = Path(file_path) if file_path is not None else Path(doc.file_path)
            if not resolved_path.exists():
                raise FileNotFoundError(f"Document file does not exist at path: {resolved_path}")

            sections = self._parse_file_by_extension(
                file_path=resolved_path,
                file_type_hint=getattr(doc, "file_type", "") or "",
            )
            if not sections:
                raise ValueError(f"No extractable text content found in document '{doc.file_name}'")

            chunks: list[ChunkData] = self.chunker.split_sections(
                sections=sections,
                file_name=doc.file_name,
            )
            if not chunks:
                raise ValueError(f"Document '{doc.file_name}' yielded 0 text chunks after splitting")

            provider = self._resolve_embedding_provider()
            texts_to_embed = [c.content for c in chunks]
            vectors = provider.embed_texts(texts_to_embed)

            if len(vectors) != len(chunks):
                raise RuntimeError(
                    f"Embedding provider returned {len(vectors)} vectors for {len(chunks)} chunks"
                )

            # Delete existing chunks for this document before inserting new ones
            self._delete_existing_chunks(db, doc)

            chunk_rows: list[Any] = []
            for chunk, vector in zip(chunks, vectors):
                enriched_meta = {
                    **chunk.metadata_json,
                    "document_id": str(doc.id),
                    "file_name": doc.file_name,
                    "page_or_sheet": chunk.page_or_sheet,
                    "chunk_index": chunk.chunk_index,
                    "department": getattr(doc, "department", "General"),
                    "access_level": getattr(doc, "access_level", "department"),
                }
                chunk_rows.append(
                    DocumentChunk(
                        id=uuid.uuid4(),
                        document_id=doc.id,
                        chunk_index=chunk.chunk_index,
                        page_or_sheet=chunk.page_or_sheet,
                        content=chunk.content,
                        token_count=chunk.token_count,
                        metadata_json=enriched_meta,
                        embedding=vector,
                    )
                )

            if hasattr(db, "add_all"):
                db.add_all(chunk_rows)
            if not _HAS_ORM_MODELS and hasattr(doc, "chunks") and isinstance(doc.chunks, list):
                doc.chunks = list(chunk_rows)

            provider_name = str(
                getattr(provider, "provider_name", None) or settings.EMBEDDING_PROVIDER
            )
            model_name = str(
                getattr(provider, "model_name", None)
                or f"{provider_name}-{settings.EMBEDDING_DIM}"
            )

            doc.status = DocumentStatus.READY.value
            doc.error_message = None
            doc.chunk_count = len(chunk_rows)
            doc.embedding_provider = provider_name
            doc.embedding_model = model_name
            doc.updated_at = datetime.now(timezone.utc)

            if hasattr(db, "commit"):
                db.commit()
            if hasattr(db, "refresh"):
                db.refresh(doc)

            return IngestionResult(
                document_id=doc.id,
                status=DocumentStatus.READY,
                chunk_count=len(chunk_rows),
                error_message=None,
            )

        except Exception as exc:
            logger.exception("Failed to ingest document %s (%s): %s", document_id, doc.file_name, exc)
            if hasattr(db, "rollback"):
                db.rollback()
            doc_retry = self._load_document(db, document_id) or doc
            if doc_retry is not None:
                doc_retry.status = DocumentStatus.FAILED.value
                doc_retry.error_message = str(exc)
                doc_retry.chunk_count = 0
                doc_retry.updated_at = datetime.now(timezone.utc)
                if hasattr(db, "commit"):
                    db.commit()
            return IngestionResult(
                document_id=document_id,
                status=DocumentStatus.FAILED,
                chunk_count=0,
                error_message=str(exc),
            )
