# File: backend/app/rag/providers/openai_provider.py
import json
import logging
import socket
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
    RawCitationCandidate,
    RawLLMGeneration,
    RetrievedChunk,
    build_ai_provider_error,
    l2_normalize,
)
from app.rag.providers.mock_provider import MockLLMProvider
from app.schemas.common import ABSTENTION_MESSAGE_VI
from app.schemas.user import UserContext

logger = logging.getLogger(__name__)


class OpenAIEmbeddingProvider(BaseEmbeddingProvider):
    """
    OpenAI Embedding Provider (default: text-embedding-3-small) configured for 768 dimensions
    via Matryoshka Representation Learning (`dimensions=768`) to match PostgreSQL vector(768).
    """

    provider_name: str = "openai"

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str | None = None,
        dimension: int | None = None,
        base_url: str = "https://api.openai.com/v1",
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.OPENAI_API_KEY
        self.model_name = model_name or settings.OPENAI_EMBEDDING_MODEL
        self.dimension = dimension or settings.EMBEDDING_DIM
        self.base_url = base_url.rstrip("/")

    def _call_embeddings_api(self, inputs: list[str]) -> list[list[float]]:
        if not self.api_key:
            raise AIProviderError(
                provider="openai",
                status_code=None,
                reason="Chưa cấu hình OPENAI_API_KEY trong .env.",
            )

        cleaned_inputs = [text.strip() or " " for text in inputs]
        try:
            from openai import OpenAI  # type: ignore[import-not-found]
        except ImportError:
            OpenAI = None

        if OpenAI is not None:
            try:
                client = OpenAI(api_key=self.api_key, base_url=self.base_url)
                response = client.embeddings.create(
                    model=self.model_name,
                    input=cleaned_inputs,
                    dimensions=self.dimension,
                )
                ordered = sorted(response.data, key=lambda item: item.index)
                return [
                    l2_normalize(list(item.embedding), target_dim=self.dimension)
                    for item in ordered
                ]
            except Exception as exc:
                status_code = getattr(exc, "status_code", None)
                logger.error(
                    "OpenAI embeddings API error (status=%s): %s",
                    status_code,
                    type(exc).__name__,
                )
                raise build_ai_provider_error(
                    provider="openai",
                    status_code=int(status_code) if isinstance(status_code, int) else None,
                    body_snippet=str(exc)[:300],
                ) from exc

        payload = json.dumps(
            {
                "model": self.model_name,
                "input": cleaned_inputs,
                "dimensions": self.dimension,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/embeddings",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body_snippet = exc.read().decode("utf-8", errors="replace").strip()[:500]
            logger.error(
                "OpenAI embeddings HTTP %s error at %s/embeddings: %s",
                exc.code,
                self.base_url,
                body_snippet or exc.reason,
            )
            raise build_ai_provider_error(
                provider="openai",
                status_code=exc.code,
                body_snippet=body_snippet,
            ) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            logger.error("OpenAI embeddings network/timeout error: %s", exc)
            raise build_ai_provider_error(
                provider="openai",
                status_code=None,
                body_snippet=str(exc),
            ) from exc

        data_items = sorted(body.get("data", []), key=lambda x: x.get("index", 0))
        return [
            l2_normalize(list(item["embedding"]), target_dim=self.dimension)
            for item in data_items
        ]

    def embed_query(self, text: str) -> list[float]:
        """Returns a 768-dimensional L2-normalized float vector for a query string."""
        vectors = self._call_embeddings_api([text])
        return vectors[0]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Returns a list of 768-dimensional L2-normalized float vectors for batch document chunks."""
        if not texts:
            return []
        return self._call_embeddings_api(texts)


class OpenAILLMProvider(BaseLLMProvider):
    """
    OpenAI Chat Completions Provider supporting natural conversational agent interaction,
    multi-turn query coreference rewriting, and JSON-structured grounded answers with [chunk_id].
    """

    provider_name: str = "openai"

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str | None = None,
        base_url: str = "https://api.openai.com/v1",
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.OPENAI_API_KEY
        self.model_name = model_name or settings.OPENAI_LLM_MODEL
        self.base_url = base_url.rstrip("/")
        self._fallback_provider = MockLLMProvider()

    def _chat_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        json_object_mode: bool = True,
        temperature: float = 0.0,
    ) -> str:
        if not self.api_key:
            raise AIProviderError(
                provider="openai",
                status_code=None,
                reason="Chưa cấu hình OPENAI_API_KEY trong .env.",
            )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        try:
            from openai import OpenAI  # type: ignore[import-not-found]
        except ImportError:
            OpenAI = None

        if OpenAI is not None:
            try:
                client = OpenAI(api_key=self.api_key, base_url=self.base_url)
                kwargs: dict[str, Any] = {
                    "model": self.model_name,
                    "messages": messages,
                    "temperature": temperature,
                }
                if json_object_mode:
                    kwargs["response_format"] = {"type": "json_object"}
                response = client.chat.completions.create(**kwargs)
                return response.choices[0].message.content or "{}"
            except Exception as exc:
                status_code = getattr(exc, "status_code", None)
                logger.error(
                    "OpenAI chat completion error (status=%s): %s",
                    status_code,
                    type(exc).__name__,
                )
                raise build_ai_provider_error(
                    provider="openai",
                    status_code=int(status_code) if isinstance(status_code, int) else None,
                    body_snippet=str(exc)[:300],
                ) from exc

        req_body: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature,
        }
        if json_object_mode:
            req_body["response_format"] = {"type": "json_object"}
        payload = json.dumps(req_body).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body_snippet = exc.read().decode("utf-8", errors="replace").strip()[:500]
            logger.error(
                "OpenAI chat HTTP %s error at %s/chat/completions: %s",
                exc.code,
                self.base_url,
                body_snippet or exc.reason,
            )
            raise build_ai_provider_error(
                provider="openai",
                status_code=exc.code,
                body_snippet=body_snippet,
            ) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            logger.error("OpenAI chat network/timeout error: %s", exc)
            raise build_ai_provider_error(
                provider="openai",
                status_code=None,
                body_snippet=str(exc),
            ) from exc

        return body["choices"][0]["message"]["content"] or "{}"

    @staticmethod
    def _parse_generation_json(raw_text: str, query: str) -> RawLLMGeneration:
        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].strip()

        parsed = json.loads(cleaned)
        abstained = bool(parsed.get("abstained", False))
        answer = str(parsed.get("answer") or "").strip()
        raw_citations = parsed.get("citations") or []
        raw_suggestions = parsed.get("suggested_queries") or []

        citations: list[RawCitationCandidate] = []
        for item in raw_citations:
            if isinstance(item, dict) and item.get("chunk_id"):
                citations.append(
                    RawCitationCandidate(
                        chunk_id=str(item["chunk_id"]),
                        quote=str(item.get("quote") or ""),
                    )
                )

        suggested = [str(s).strip() for s in raw_suggestions if str(s).strip()][:3]
        if len(suggested) < 2:
            suggested = MockLLMProvider().suggest_rephrased_queries(query)

        if abstained or not answer:
            return RawLLMGeneration(
                answer=ABSTENTION_MESSAGE_VI,
                abstained=True,
                citations=[],
                suggested_queries=suggested,
            )

        return RawLLMGeneration(
            answer=answer,
            abstained=False,
            citations=citations,
            suggested_queries=suggested,
        )

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
            raw_content = self._chat_completion(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                json_object_mode=True,
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
            logger.debug("OpenAI conversational reply fallback triggered: %s", exc)

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
            rewritten = self._chat_completion(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                json_object_mode=False,
                temperature=0.0,
            )
            rewritten_clean = sanitize_user_query(rewritten.strip().strip('"').strip("'"))
            if rewritten_clean and len(rewritten_clean) <= 500:
                return rewritten_clean
        except Exception as exc:
            logger.debug("OpenAI rewrite_query_with_history fallback triggered: %s", exc)

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
        raw_content = self._chat_completion(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            json_object_mode=True,
            temperature=0.0,
        )
        try:
            return self._parse_generation_json(raw_content, query)
        except Exception as exc:
            logger.warning("Failed to parse OpenAI JSON output (%s); falling back to abstention.", exc)
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
            raw_content = self._chat_completion(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                json_object_mode=False,
                temperature=0.2,
            )
            parsed = json.loads(raw_content.strip().strip("`").lstrip("json").strip())
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
            logger.debug("OpenAI suggest_rephrased_queries fallback triggered: %s", exc)

        return self._fallback_provider.suggest_rephrased_queries(query)
