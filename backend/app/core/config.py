# File: backend/app/core/config.py
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
    GEMINI_LLM_MODEL: str = "gemini-2.0-flash"
    GEMINI_EMBEDDING_MODEL: str = "models/text-embedding-004"

    # Vector & RAG Parameters
    EMBEDDING_DIM: int = 768
    SIMILARITY_THRESHOLD: float = 0.65
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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
