# File: backend/app/rag/providers/factory.py
from app.core.config import settings
from app.rag.providers.base import BaseEmbeddingProvider, BaseLLMProvider
from app.rag.providers.gemini_provider import GeminiEmbeddingProvider, GeminiLLMProvider
from app.rag.providers.mock_provider import MockEmbeddingProvider, MockLLMProvider
from app.rag.providers.openai_provider import OpenAIEmbeddingProvider, OpenAILLMProvider


def get_embedding_provider(provider_override: str | None = None) -> BaseEmbeddingProvider:
    """
    Returns MockEmbeddingProvider, OpenAIEmbeddingProvider, or GeminiEmbeddingProvider
    based on settings.EMBEDDING_PROVIDER (or provider_override).
    """
    provider_name = (provider_override or settings.EMBEDDING_PROVIDER or "mock").strip().lower()
    if provider_name == "openai":
        return OpenAIEmbeddingProvider()
    if provider_name == "gemini":
        return GeminiEmbeddingProvider()
    if provider_name == "mock":
        return MockEmbeddingProvider(dimension=settings.EMBEDDING_DIM)
    raise ValueError(
        f"Unsupported EMBEDDING_PROVIDER '{provider_name}'. Expected one of: 'mock', 'openai', 'gemini'."
    )


def get_llm_provider(provider_override: str | None = None) -> BaseLLMProvider:
    """
    Returns MockLLMProvider, OpenAILLMProvider, or GeminiLLMProvider
    based on settings.LLM_PROVIDER (or provider_override).
    """
    provider_name = (provider_override or settings.LLM_PROVIDER or "mock").strip().lower()
    if provider_name == "openai":
        return OpenAILLMProvider()
    if provider_name == "gemini":
        return GeminiLLMProvider()
    if provider_name == "mock":
        return MockLLMProvider()
    raise ValueError(
        f"Unsupported LLM_PROVIDER '{provider_name}'. Expected one of: 'mock', 'openai', 'gemini'."
    )
