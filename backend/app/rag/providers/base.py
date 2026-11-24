# File: backend/app/rag/providers/base.py
import math
import uuid
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


def l2_normalize(vec: list[float], target_dim: int = 768) -> list[float]:
    """Ensure vector has exact target_dim and unit L2 norm."""
    if len(vec) < target_dim:
        vec = list(vec) + [0.0] * (target_dim - len(vec))
    elif len(vec) > target_dim:
        vec = list(vec[:target_dim])

    norm = math.sqrt(sum(v * v for v in vec))
    if norm <= 1e-12:
        out = [0.0] * target_dim
        out[0] = 1.0
        return out
    return [round(v / norm, 7) for v in vec]


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Compute cosine similarity in [-1.0, 1.0] between two equal-dimension vectors."""
    if not vec_a or not vec_b:
        return 0.0
    n = min(len(vec_a), len(vec_b))
    dot = sum(vec_a[i] * vec_b[i] for i in range(n))
    norm_a = math.sqrt(sum(vec_a[i] * vec_a[i] for i in range(n)))
    norm_b = math.sqrt(sum(vec_b[i] * vec_b[i] for i in range(n)))
    if norm_a <= 1e-12 or norm_b <= 1e-12:
        return 0.0
    return float(max(-1.0, min(1.0, dot / (norm_a * norm_b))))


class RetrievedChunk(BaseModel):
    """Authorized chunk returned by PreRetrievalRBACRetriever (Scenario 3 compliant)."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    file_name: str
    title: str
    department: str
    access_level: str
    page_or_sheet: str
    chunk_index: int
    content: str
    score: float
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class RawCitationCandidate(BaseModel):
    """Candidate citation proposed by the LLM before post-generation verification."""

    chunk_id: uuid.UUID | str
    quote: str = ""


class RawLLMGeneration(BaseModel):
    """Structured output produced by BaseLLMProvider.generate_grounded_answer."""

    answer: str
    abstained: bool = False
    citations: list[RawCitationCandidate] = Field(default_factory=list)
    suggested_queries: list[str] = Field(default_factory=list)


class BaseEmbeddingProvider(ABC):
    provider_name: str = "base"
    model_name: str = "base-768"
    dimension: int = 768

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """Returns a 768-dimensional L2-normalized float vector for a query string."""
        ...

    @abstractmethod
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Returns a list of 768-dimensional L2-normalized float vectors for batch document chunks."""
        ...


class BaseLLMProvider(ABC):
    provider_name: str = "base"
    model_name: str = "base-llm"

    @abstractmethod
    def generate_grounded_answer(
        self,
        query: str,
        chunks: list[RetrievedChunk],
    ) -> RawLLMGeneration:
        """
        Generates a Vietnamese (or query-language-matched) grounded answer using ONLY the provided
        authorized chunks. Every claim must cite [chunk_id] and provide a verbatim quote.
        """
        ...

    @abstractmethod
    def suggest_rephrased_queries(
        self,
        query: str,
    ) -> list[str]:
        """
        Returns 2-3 suggested query rephrasings in Vietnamese for Scenario 1 (when found=False).
        Must NEVER reference or leak any unauthorized document titles or content.
        """
        ...
