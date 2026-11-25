# File: backend/app/core/config.py
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "Enterprise Knowledge Assistant"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: str = "development"

    # Database
    DATABASE_URL: str = "postgresql+psycopg://eka_user:eka_password@localhost:5432/eka_db"

    # JWT Security
    JWT_SECRET_KEY: str = "super-secret-enterprise-jwt-key-change-in-prod"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24 hours

    # Providers: "mock" | "openai" | "gemini"
    LLM_PROVIDER: str = "mock"
    EMBEDDING_PROVIDER: str = "mock"

    # OpenAI Configuration
    OPENAI_API_KEY: str | None = None
    OPENAI_LLM_MODEL: str = "gpt-4o-mini"
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"

    # Gemini Configuration
    GEMINI_API_KEY: str | None = None
    GEMINI_LLM_MODEL: str = "gemini-flash-latest"
    GEMINI_EMBEDDING_MODEL: str = "gemini-embedding-001"
    # "auto" (AQ. prefix => Vertex Express) | "aistudio" | "vertex"
    GEMINI_API_BACKEND: str = "auto"
    # Comma-separated LLM models tried in order when the primary returns 404/429/5xx
    GEMINI_FALLBACK_LLM_MODELS: str = "gemini-flash-lite-latest"
    # Retries per request on HTTP 429/5xx (exponential backoff: 1s, 2s, 4s...)
    GEMINI_MAX_RETRIES: int = 2
    GEMINI_RETRY_BASE_DELAY: float = 1.0

    # Vector & RAG Parameters
    EMBEDDING_DIM: int = 768
    SIMILARITY_THRESHOLD: float = 0.65
    # Scenario 1 tiering: when no chunk directly answers the query, authorized chunks whose
    # similarity is >= RELATED_SIMILARITY_THRESHOLD are suggested as "possibly related" documents
    # (found=false, no generated facts). Unset => provider default (mock 0.40, openai 0.40, gemini 0.60).
    RELATED_SIMILARITY_THRESHOLD: float | None = None
    RELATED_MAX_DOCUMENTS: int = 3
    DEFAULT_TOP_K: int = 5
    QUOTE_OVERLAP_THRESHOLD: float = 0.50

    # Ingestion & Storage Parameters
    UPLOAD_DIR: str = "./data/uploads"
    MAX_UPLOAD_SIZE_MB: int = 50
    CHUNK_SIZE: int = 512
    CHUNK_OVERLAP: int = 64
    INGESTION_MODE: str = "sync"  # "sync" | "background"

    # Auto-seed database on startup
    SEED_ON_STARTUP: bool = True

    @field_validator("RELATED_SIMILARITY_THRESHOLD", mode="before")
    @classmethod
    def _blank_related_threshold_means_auto(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and value.strip().lower() in ("", "auto", "none")):
            return None
        return value

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
