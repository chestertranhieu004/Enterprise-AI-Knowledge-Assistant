# File: backend/app/rag/providers/base.py
import math
import uuid
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.user import UserContext


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


class AIProviderError(RuntimeError):
    """
    Sanitized, typed exception raised when an external AI provider (Gemini / OpenAI)
    fails due to HTTP errors (400/401/403/429/5xx), network errors, or timeouts.
    NEVER includes raw API keys or secret credentials.
    """

    def __init__(
        self,
        provider: str,
        reason: str,
        status_code: int | None = None,
    ) -> None:
        self.provider = (provider or "AI").strip()
        self.status_code = status_code
        self.reason = (reason or "Dịch vụ AI tạm thời không khả dụng.").strip()
        super().__init__(self.to_user_message())

    @property
    def provider_label(self) -> str:
        low = self.provider.lower()
        if low == "gemini":
            return "Gemini"
        if low == "openai":
            return "OpenAI"
        return self.provider

    def to_user_message(self) -> str:
        return f"Không kết nối được dịch vụ AI ({self.provider_label}): {self.reason}"


def build_ai_provider_error(
    provider: str,
    status_code: int | None = None,
    body_snippet: str = "",
    is_vertex_express: bool = False,
) -> AIProviderError:
    """Map HTTP status codes / network failures into a sanitized Vietnamese AIProviderError."""
    low_body = (body_snippet or "").lower()
    if status_code == 403:
        if provider.lower() == "gemini" and (
            is_vertex_express
            or "aiplatform" in low_body
            or "vertex" in low_body
            or "has not been used" in low_body
            or "disabled" in low_body
        ):
            reason = (
                "API Vertex AI chưa được bật cho project Google Cloud hoặc bị từ chối quyền truy cập. "
                "Vui lòng bật API hoặc dùng key Google AI Studio."
            )
        else:
            reason = (
                "Bị từ chối quyền truy cập (HTTP 403). "
                "Vui lòng kiểm tra quyền và cấu hình API key."
            )
    elif status_code in (400, 401):
        reason = (
            f"API key không hợp lệ hoặc yêu cầu bị từ chối xác thực (HTTP {status_code}). "
            "Vui lòng kiểm tra cấu hình trong .env."
        )
    elif status_code == 429:
        reason = (
            "Đã vượt hạn mức sử dụng (quota) hoặc giới hạn tốc độ (HTTP 429). "
            "Vui lòng thử lại sau."
        )
    elif status_code is not None and status_code >= 500:
        reason = f"Dịch vụ AI tạm thời gặp lỗi máy chủ (HTTP {status_code}). Vui lòng thử lại sau."
    elif status_code is not None:
        reason = f"Dịch vụ AI phản hồi lỗi (HTTP {status_code}). Vui lòng thử lại sau."
    else:
        reason = (
            "Không thể kết nối mạng hoặc hết thời gian chờ phản hồi. "
            "Vui lòng kiểm tra kết nối mạng và thử lại."
        )
    return AIProviderError(provider=provider, reason=reason, status_code=status_code)


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
        history: list[dict[str, str]] | None = None,
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

    def generate_conversational_reply(
        self,
        query: str,
        user: UserContext | None = None,
        history: list[dict[str, str]] | None = None,
        intent: str = "greeting",
    ) -> RawLLMGeneration:
        """
        Generate a warm, natural Vietnamese conversational response for social/smalltalk intents
        (greeting, identity, capabilities, thanks, farewell, smalltalk) without inventing policies
        or leaking restricted documents.
        """
        name = user.full_name if user is not None and user.full_name else "bạn"
        return RawLLMGeneration(
            answer=(
                f"Xin chào {name}! Mình là Trợ lý Tri thức Doanh nghiệp. "
                "Bạn có thể trò chuyện tự nhiên hoặc đặt câu hỏi về các tài liệu nội bộ mà bạn có quyền truy cập nhé!"
            ),
            abstained=False,
            citations=[],
            suggested_queries=self.suggest_rephrased_queries(query),
        )

    def rewrite_query_with_history(
        self,
        query: str,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        """
        Resolve multi-turn conversational coreference when `history` exists and `query`
        refers back to an earlier topic. Defaults to returning `query` unchanged.
        """
        return query
