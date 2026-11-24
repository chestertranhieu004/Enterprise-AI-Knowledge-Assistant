# File: backend/app/rag/providers/gemini_provider.py
import json
import logging
import urllib.request

from app.core.config import settings
from app.rag.prompts import (
    build_grounded_rag_messages,
    build_query_suggestion_messages,
    is_prompt_injection,
)
from app.rag.providers.base import (
    BaseEmbeddingProvider,
    BaseLLMProvider,
    RawLLMGeneration,
    RetrievedChunk,
    l2_normalize,
)
from app.rag.providers.mock_provider import MockLLMProvider
from app.rag.providers.openai_provider import OpenAILLMProvider
from app.schemas.common import ABSTENTION_MESSAGE_VI

logger = logging.getLogger(__name__)


class GeminiEmbeddingProvider(BaseEmbeddingProvider):
    """
    Google Gemini Embedding Provider (default: models/text-embedding-004)
    returning 768-dimensional L2-normalized vectors compatible with pgvector vector(768).
    """

    provider_name: str = "gemini"

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str | None = None,
        dimension: int | None = None,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.GEMINI_API_KEY
        raw_model = model_name or settings.GEMINI_EMBEDDING_MODEL
        self.model_name = (
            raw_model if raw_model.startswith("models/") else f"models/{raw_model}"
        )
        self.dimension = dimension or settings.EMBEDDING_DIM
        self.base_url = base_url.rstrip("/")

    def _embed_single_rest(self, text: str) -> list[float]:
        if not self.api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not configured for GeminiEmbeddingProvider."
            )

        url = f"{self.base_url}/{self.model_name}:embedContent?key={self.api_key}"
        payload = json.dumps(
            {
                "model": self.model_name,
                "content": {"parts": [{"text": text.strip() or " "}]},
                "outputDimensionality": self.dimension,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        values = body.get("embedding", {}).get("values", [])
        return l2_normalize([float(v) for v in values], target_dim=self.dimension)

    def embed_query(self, text: str) -> list[float]:
        """Returns a 768-dimensional L2-normalized float vector for a query string."""
        return self._embed_single_rest(text)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Returns a list of 768-dimensional L2-normalized float vectors for batch document chunks."""
        return [self._embed_single_rest(t) for t in texts]


class GeminiLLMProvider(BaseLLMProvider):
    """
    Google Gemini LLM Provider (default: gemini-2.0-flash) enforcing JSON-structured
    Vietnamese grounded answers with strict [chunk_id] citations and verbatim quotes.
    """

    provider_name: str = "gemini"

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str | None = None,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.GEMINI_API_KEY
        raw_model = model_name or settings.GEMINI_LLM_MODEL
        self.model_name = (
            raw_model[len("models/") :]
            if raw_model.startswith("models/")
            else raw_model
        )
        self.base_url = base_url.rstrip("/")
        self._fallback_suggester = MockLLMProvider()

    def _generate_content_rest(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured for GeminiLLMProvider.")

        url = f"{self.base_url}/models/{self.model_name}:generateContent?key={self.api_key}"
        payload = json.dumps(
            {
                "systemInstruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                "generationConfig": {
                    "temperature": 0.0,
                    "responseMimeType": "application/json",
                },
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        candidates = body.get("candidates") or []
        if not candidates:
            return "{}"
        parts = candidates[0].get("content", {}).get("parts") or []
        return parts[0].get("text", "{}") if parts else "{}"

    def generate_grounded_answer(
        self,
        query: str,
        chunks: list[RetrievedChunk],
    ) -> RawLLMGeneration:
        if is_prompt_injection(query) or not chunks:
            return RawLLMGeneration(
                answer=ABSTENTION_MESSAGE_VI,
                abstained=True,
                citations=[],
                suggested_queries=self._fallback_suggester.suggest_rephrased_queries(query),
            )

        system_prompt, user_prompt = build_grounded_rag_messages(query, chunks)
        raw_content = self._generate_content_rest(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        try:
            return OpenAILLMProvider._parse_generation_json(raw_content, query)
        except Exception as exc:
            logger.warning("Failed to parse Gemini JSON output (%s); falling back to abstention.", exc)
            return RawLLMGeneration(
                answer=ABSTENTION_MESSAGE_VI,
                abstained=True,
                citations=[],
                suggested_queries=self._fallback_suggester.suggest_rephrased_queries(query),
            )

    def suggest_rephrased_queries(
        self,
        query: str,
    ) -> list[str]:
        if is_prompt_injection(query) or not self.api_key:
            return self._fallback_suggester.suggest_rephrased_queries(query)

        system_prompt, user_prompt = build_query_suggestion_messages(query)
        try:
            raw_content = self._generate_content_rest(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
            parsed = json.loads(raw_content.strip())
            if isinstance(parsed, dict):
                candidates = parsed.get("suggested_queries") or parsed.get("queries") or []
            elif isinstance(parsed, list):
                candidates = parsed
            else:
                candidates = []
            cleaned = [str(q).strip() for q in candidates if str(q).strip()][:3]
            if len(cleaned) >= 2:
                return cleaned
        except Exception as exc:
            logger.debug("Gemini suggest_rephrased_queries fallback triggered: %s", exc)

        return self._fallback_suggester.suggest_rephrased_queries(query)
