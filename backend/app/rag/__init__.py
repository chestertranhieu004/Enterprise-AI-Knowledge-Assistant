# File: backend/app/rag/__init__.py
from app.rag.providers.base import (
    BaseEmbeddingProvider,
    BaseLLMProvider,
    RawCitationCandidate,
    RawLLMGeneration,
    RetrievedChunk,
)
from app.rag.providers.factory import get_embedding_provider, get_llm_provider
from app.rag.retriever import PreRetrievalRBACRetriever
from app.rag.service import RAGService
from app.rag.verifier import CitationVerifier, VerificationResult

__all__ = [
    "BaseEmbeddingProvider",
    "BaseLLMProvider",
    "CitationVerifier",
    "PreRetrievalRBACRetriever",
    "RAGService",
    "RawCitationCandidate",
    "RawLLMGeneration",
    "RetrievedChunk",
    "VerificationResult",
    "get_embedding_provider",
    "get_llm_provider",
]
