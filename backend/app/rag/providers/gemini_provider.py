# File: backend/app/rag/providers/gemini_provider.py
import json
import logging
import random
import socket
import time
import urllib.error
import urllib.request
from typing import Any

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
    AIProviderError,
    BaseEmbeddingProvider,
    BaseLLMProvider,
    RawLLMGeneration,
    RetrievedChunk,
    build_ai_provider_error,
    l2_normalize,
)
from app.rag.providers.mock_provider import MockLLMProvider
from app.rag.providers.openai_provider import OpenAILLMProvider
from app.schemas.common import ABSTENTION_MESSAGE_VI
from app.schemas.user import UserContext

logger = logging.getLogger(__name__)

DEFAULT_AI_STUDIO_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
VERTEX_EXPRESS_GLOBAL_BASE_URL = "https://aiplatform.googleapis.com/v1/publishers/google"
VERTEX_EXPRESS_REGIONAL_BASE_URL = (
    "https://us-central1-aiplatform.googleapis.com/v1/publishers/google"
)
VERTEX_EMBEDDING_BATCH_SIZE = 50


def _strip_models_prefix(model_name: str) -> str:
    """Return bare model identifier without any leading `models/` prefix."""
    cleaned = (model_name or "").strip()
    if cleaned.startswith("models/"):
        return cleaned[len("models/") :]
    return cleaned


def _is_vertex_express_key(api_key: str | None) -> bool:
    """
    Decide whether to call Vertex AI Express (`aiplatform.googleapis.com`) or Google AI Studio
    (`generativelanguage.googleapis.com`), driven by settings.GEMINI_API_BACKEND:
    - "aistudio": always Google AI Studio (new AI Studio keys may also start with `AQ.`).
    - "vertex":   always Vertex AI Express Mode.
    - "auto":     legacy heuristic, `AQ.` prefix => Vertex Express.
    """
    backend = (getattr(settings, "GEMINI_API_BACKEND", "auto") or "auto").strip().lower()
    if backend == "aistudio":
        return False
    if backend == "vertex":
        return bool(api_key)
    return bool(api_key and api_key.startswith("AQ."))


def _read_http_error_snippet(exc: urllib.error.HTTPError, max_chars: int = 500) -> str:
    """Safely read an HTTP error response body snippet without exposing credentials."""
    try:
        cached = getattr(exc, "_eka_body_snippet", None)
        if isinstance(cached, str):
            return cached
        raw_bytes = exc.read()
        snippet = raw_bytes.decode("utf-8", errors="replace").strip()[:max_chars]
        setattr(exc, "_eka_body_snippet", snippet)
        return snippet
    except Exception:
        return ""


# HTTP statuses worth retrying: rate limit (429) and transient server overload (5xx).
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
# Statuses that justify switching to the next fallback LLM model.
MODEL_FALLBACK_STATUS_CODES = RETRYABLE_STATUS_CODES | {404}


def _is_retryable(exc: AIProviderError) -> bool:
    return exc.status_code in RETRYABLE_STATUS_CODES


def _post_json(
    url: str,
    payload_obj: dict[str, Any],
    api_key: str,
    timeout: int = 45,
    is_vertex_express: bool = False,
) -> dict[str, Any]:
    """
    POST with automatic retry + exponential backoff (with jitter) on HTTP 429/5xx,
    which the Gemini free tier returns frequently when the model is overloaded.
    Number of retries is controlled by settings.GEMINI_MAX_RETRIES.
    """
    max_retries = max(0, int(getattr(settings, "GEMINI_MAX_RETRIES", 2) or 0))
    base_delay = float(getattr(settings, "GEMINI_RETRY_BASE_DELAY", 1.0) or 1.0)
    attempt = 0
    while True:
        try:
            return _post_json_once(
                url=url,
                payload_obj=payload_obj,
                api_key=api_key,
                timeout=timeout,
                is_vertex_express=is_vertex_express,
            )
        except AIProviderError as exc:
            if attempt >= max_retries or not _is_retryable(exc):
                raise
            delay = base_delay * (2**attempt) + random.uniform(0, 0.5)
            attempt += 1
            logger.warning(
                "Gemini HTTP %s at %s; retry %d/%d in %.1fs.",
                exc.status_code,
                url,
                attempt,
                max_retries,
                delay,
            )
            time.sleep(delay)


def _post_json_once(
    url: str,
    payload_obj: dict[str, Any],
    api_key: str,
    timeout: int = 45,
    is_vertex_express: bool = False,
) -> dict[str, Any]:
    """Execute a single JSON POST request authenticating strictly via `x-goog-api-key` header."""
    payload = json.dumps(payload_obj).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return dict(json.loads(resp.read().decode("utf-8")))
    except urllib.error.HTTPError as exc:
        body_snippet = _read_http_error_snippet(exc)
        logger.error(
            "Gemini API HTTP %s error at %s: %s",
            exc.code,
            url,
            body_snippet or exc.reason,
        )
        raise build_ai_provider_error(
            provider="gemini",
            status_code=exc.code,
            body_snippet=body_snippet,
            is_vertex_express=is_vertex_express,
        ) from exc
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        logger.error("Gemini API network/timeout error at %s: %s", url, exc)
        raise build_ai_provider_error(
            provider="gemini",
            status_code=None,
            body_snippet=str(exc),
            is_vertex_express=is_vertex_express,
        ) from exc


class GeminiEmbeddingProvider(BaseEmbeddingProvider):
    """
    Google Gemini Embedding Provider supporting both:
    - Vertex AI Express Mode (`AQ.` keys) via `aiplatform.googleapis.com/...:predict` with batching
      and automatic `us-central1` regional fallback on HTTP 400/404.
    - Standard Google AI Studio (`AIza...` keys) via `generativelanguage.googleapis.com/v1beta/...:embedContent`.
    Always returns 768-dimensional L2-normalized vectors compatible with pgvector `vector(768)`.
    """

    provider_name: str = "gemini"

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str | None = None,
        dimension: int | None = None,
        base_url: str | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.GEMINI_API_KEY
        raw_model = model_name or settings.GEMINI_EMBEDDING_MODEL
        self.clean_model_id = _strip_models_prefix(raw_model)
        self._custom_base_url = base_url is not None
        self.base_url = (base_url or DEFAULT_AI_STUDIO_BASE_URL).rstrip("/")
        self.is_vertex_express = (
            _is_vertex_express_key(self.api_key) and not self._custom_base_url
        )
        self.model_name = (
            self.clean_model_id
            if self.is_vertex_express
            else f"models/{self.clean_model_id}"
        )
        self.dimension = dimension or settings.EMBEDDING_DIM

    def _embed_batch_vertex(self, texts: list[str]) -> list[list[float]]:
        if not self.api_key:
            raise AIProviderError(
                provider="gemini",
                status_code=None,
                reason="Chưa cấu hình GEMINI_API_KEY trong .env.",
            )
        if not texts:
            return []

        primary_url = (
            f"{VERTEX_EXPRESS_GLOBAL_BASE_URL}/models/{self.clean_model_id}:predict"
        )
        fallback_url = (
            f"{VERTEX_EXPRESS_REGIONAL_BASE_URL}/models/{self.clean_model_id}:predict"
        )

        all_vectors: list[list[float]] = []
        active_url = primary_url

        for start in range(0, len(texts), VERTEX_EMBEDDING_BATCH_SIZE):
            batch = texts[start : start + VERTEX_EMBEDDING_BATCH_SIZE]
            payload_obj: dict[str, Any] = {
                "instances": [{"content": (t.strip() or " ")} for t in batch],
                "parameters": {
                    "outputDimensionality": self.dimension,
                    "autoTruncate": True,
                },
            }
            try:
                body = _post_json(
                    url=active_url,
                    payload_obj=payload_obj,
                    api_key=self.api_key,
                    timeout=30,
                    is_vertex_express=True,
                )
            except AIProviderError as exc:
                if active_url == primary_url and exc.status_code in (400, 404):
                    logger.warning(
                        "Vertex Express global embedding endpoint returned HTTP %s; retrying regional endpoint %s.",
                        exc.status_code,
                        fallback_url,
                    )
                    active_url = fallback_url
                    body = _post_json(
                        url=active_url,
                        payload_obj=payload_obj,
                        api_key=self.api_key,
                        timeout=30,
                        is_vertex_express=True,
                    )
                else:
                    raise

            predictions = body.get("predictions") or []
            for pred in predictions:
                emb_obj = pred.get("embeddings") if isinstance(pred, dict) else {}
                values = emb_obj.get("values", []) if isinstance(emb_obj, dict) else []
                all_vectors.append(
                    l2_normalize([float(v) for v in values], target_dim=self.dimension)
                )

        return all_vectors

    def _embed_single_aistudio(self, text: str) -> list[float]:
        if not self.api_key:
            raise AIProviderError(
                provider="gemini",
                status_code=None,
                reason="Chưa cấu hình GEMINI_API_KEY trong .env.",
            )

        url = f"{self.base_url}/models/{self.clean_model_id}:embedContent"
        payload_obj: dict[str, Any] = {
            "model": f"models/{self.clean_model_id}",
            "content": {"parts": [{"text": text.strip() or " "}]},
            "outputDimensionality": self.dimension,
        }
        body = _post_json(
            url=url,
            payload_obj=payload_obj,
            api_key=self.api_key,
            timeout=30,
            is_vertex_express=False,
        )
        values = body.get("embedding", {}).get("values", [])
        return l2_normalize([float(v) for v in values], target_dim=self.dimension)

    def embed_query(self, text: str) -> list[float]:
        """Returns a 768-dimensional L2-normalized float vector for a query string."""
        if self.is_vertex_express:
            vectors = self._embed_batch_vertex([text])
            return vectors[0] if vectors else l2_normalize([], target_dim=self.dimension)
        return self._embed_single_aistudio(text)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Returns a list of 768-dimensional L2-normalized float vectors for batch document chunks."""
        if not texts:
            return []
        if self.is_vertex_express:
            return self._embed_batch_vertex(texts)
        return [self._embed_single_aistudio(t) for t in texts]


class GeminiLLMProvider(BaseLLMProvider):
    """
    Google Gemini LLM Provider supporting both:
    - Vertex AI Express Mode (`AQ.` keys) via `aiplatform.googleapis.com/v1/publishers/google/models/{model}:generateContent`
    - Standard Google AI Studio (`AIza...` keys) via `generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`
    Authenticates via `x-goog-api-key` header and supports natural conversational agent interaction,
    multi-turn query rewriting, and JSON-structured grounded RAG answers.
    """

    provider_name: str = "gemini"

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.GEMINI_API_KEY
        raw_model = model_name or settings.GEMINI_LLM_MODEL
        self.model_name = _strip_models_prefix(raw_model)
        self._custom_base_url = base_url is not None
        self.base_url = (base_url or DEFAULT_AI_STUDIO_BASE_URL).rstrip("/")
        self.is_vertex_express = (
            _is_vertex_express_key(self.api_key) and not self._custom_base_url
        )
        self._fallback_provider = MockLLMProvider()

    def _model_chain(self) -> list[str]:
        """Primary model followed by settings.GEMINI_FALLBACK_LLM_MODELS (comma-separated), de-duplicated."""
        raw_fallbacks = str(getattr(settings, "GEMINI_FALLBACK_LLM_MODELS", "") or "")
        chain: list[str] = []
        for name in [self.model_name, *raw_fallbacks.split(",")]:
            clean = _strip_models_prefix(name)
            if clean and clean not in chain:
                chain.append(clean)
        return chain

    def _build_generate_content_url(self, model_name: str | None = None) -> str:
        clean_model_id = _strip_models_prefix(model_name or self.model_name)
        if self.is_vertex_express:
            return f"{VERTEX_EXPRESS_GLOBAL_BASE_URL}/models/{clean_model_id}:generateContent"
        return f"{self.base_url}/models/{clean_model_id}:generateContent"

    def _generate_content_rest(
        self,
        system_prompt: str,
        user_prompt: str,
        json_mode: bool = True,
        temperature: float = 0.0,
    ) -> str:
        if not self.api_key:
            raise AIProviderError(
                provider="gemini",
                status_code=None,
                reason="Chưa cấu hình GEMINI_API_KEY trong .env.",
            )

        gen_config: dict[str, Any] = {"temperature": temperature}
        if json_mode:
            gen_config["responseMimeType"] = "application/json"

        payload_obj: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": gen_config,
        }
        chain = self._model_chain()
        body: dict[str, Any] = {}
        for index, model_id in enumerate(chain):
            try:
                body = _post_json(
                    url=self._build_generate_content_url(model_id),
                    payload_obj=payload_obj,
                    api_key=self.api_key,
                    timeout=45,
                    is_vertex_express=self.is_vertex_express,
                )
                break
            except AIProviderError as exc:
                is_last = index == len(chain) - 1
                if is_last or exc.status_code not in MODEL_FALLBACK_STATUS_CODES:
                    raise
                logger.warning(
                    "Gemini model %s failed with HTTP %s; falling back to %s.",
                    model_id,
                    exc.status_code,
                    chain[index + 1],
                )
        candidates = body.get("candidates") or []
        if not candidates:
            return "{}" if json_mode else ""
        parts = candidates[0].get("content", {}).get("parts") or []
        return str(parts[0].get("text", "{}" if json_mode else "")) if parts else ""

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
            logger.warning(
                "Failed to parse Gemini JSON output (%s); falling back to abstention.",
                exc,
            )
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
