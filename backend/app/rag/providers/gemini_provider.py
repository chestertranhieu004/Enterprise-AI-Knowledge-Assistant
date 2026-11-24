# File: backend/app/rag/providers/gemini_provider.py
import json
import logging
import urllib.request

from app.core.config import settings
from app.rag.prompts import (
    build_conversational_agent_messages,
    build_grounded_rag_messages,
    build_query_rewrite_messages,
    build_query_suggestion_messages,
    is_prompt_injection,
    sanitize_user_query,
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
from app.schemas.user import UserContext

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
    Google Gemini LLM Provider (default: gemini-2.0-flash) supporting natural conversational
    agent interaction, multi-turn query rewriting, and JSON-structured grounded RAG answers.
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
        self._fallback_provider = MockLLMProvider()

    def _generate_content_rest(
        self,
        system_prompt: str,
        user_prompt: str,
        json_mode: bool = True,
        temperature: float = 0.0,
    ) -> str:
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured for GeminiLLMProvider.")

        url = f"{self.base_url}/models/{self.model_name}:generateContent?key={self.api_key}"
        gen_config: dict[str, object] = {"temperature": temperature}
        if json_mode:
            gen_config["responseMimeType"] = "application/json"

        payload = json.dumps(
            {
                "systemInstruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                "generationConfig": gen_config,
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
            return "{}" if json_mode else ""
        parts = candidates[0].get("content", {}).get("parts") or []
        return parts[0].get("text", "{}" if json_mode else "") if parts else ""

    def generate_conversational_reply(
        self,
        query: str,
        user: UserContext | None = None,
        history: list[dict[str, str]] | None = None,
        intent: str = "greeting",
    ) -> RawLLMGeneration:
        if is_prompt_injection(query):
            return RawLLMGeneration(
                answer=ABSTENTION_MESSAGE_VI,
                abstained=True,
                citations=[],
                suggested_queries=self._fallback_provider.suggest_rephrased_queries(query),
            )

        if not self.api_key:
            return self._fallback_provider.generate_conversational_reply(
                query=query, user=user, history=history, intent=intent
            )

        system_prompt, user_prompt = build_conversational_agent_messages(
            query=query, user=user, history=history, intent=intent
        )
        try:
            raw_content = self._generate_content_rest(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                json_mode=True,
                temperature=0.6,
            )
            cleaned = raw_content.strip().strip("`")
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].strip()
            parsed = json.loads(cleaned)
            answer = str(parsed.get("answer") or "").strip()
            raw_suggestions = parsed.get("suggested_queries") or []
            suggested = [str(s).strip() for s in raw_suggestions if str(s).strip()][:3]
            if answer:
                if len(suggested) < 2:
                    suggested = self._fallback_provider.suggest_rephrased_queries(query)
                return RawLLMGeneration(
                    answer=answer,
                    abstained=False,
                    citations=[],
                    suggested_queries=suggested,
                )
        except Exception as exc:
            logger.debug("Gemini conversational reply fallback triggered: %s", exc)

        return self._fallback_provider.generate_conversational_reply(
            query=query, user=user, history=history, intent=intent
        )

    def rewrite_query_with_history(
        self,
        query: str,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        cleaned = sanitize_user_query(query)
        if not cleaned or not history or is_prompt_injection(cleaned):
            return cleaned
        if not self.api_key:
            return self._fallback_provider.rewrite_query_with_history(cleaned, history)

        system_prompt, user_prompt = build_query_rewrite_messages(cleaned, history)
        try:
            rewritten = self._generate_content_rest(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                json_mode=False,
                temperature=0.0,
            )
            rewritten_clean = sanitize_user_query(rewritten.strip().strip('"').strip("'"))
            if rewritten_clean and len(rewritten_clean) <= 500:
                return rewritten_clean
        except Exception as exc:
            logger.debug("Gemini rewrite_query_with_history fallback triggered: %s", exc)

        return self._fallback_provider.rewrite_query_with_history(cleaned, history)

    def generate_grounded_answer(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        history: list[dict[str, str]] | None = None,
    ) -> RawLLMGeneration:
        if is_prompt_injection(query) or not chunks:
            return RawLLMGeneration(
                answer=ABSTENTION_MESSAGE_VI,
                abstained=True,
                citations=[],
                suggested_queries=self._fallback_provider.suggest_rephrased_queries(query),
            )

        system_prompt, user_prompt = build_grounded_rag_messages(
            query=query, chunks=chunks, history=history
        )
        raw_content = self._generate_content_rest(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            json_mode=True,
            temperature=0.0,
        )
        try:
            return OpenAILLMProvider._parse_generation_json(raw_content, query)
        except Exception as exc:
            logger.warning("Failed to parse Gemini JSON output (%s); falling back to abstention.", exc)
            return RawLLMGeneration(
                answer=ABSTENTION_MESSAGE_VI,
                abstained=True,
                citations=[],
                suggested_queries=self._fallback_provider.suggest_rephrased_queries(query),
            )

    def suggest_rephrased_queries(
        self,
        query: str,
    ) -> list[str]:
        if is_prompt_injection(query) or not self.api_key:
            return self._fallback_provider.suggest_rephrased_queries(query)

        system_prompt, user_prompt = build_query_suggestion_messages(query)
        try:
            raw_content = self._generate_content_rest(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                json_mode=True,
                temperature=0.2,
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

        return self._fallback_provider.suggest_rephrased_queries(query)
