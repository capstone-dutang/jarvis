"""Application settings loaded from environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://jarvis:jarvis@localhost:5432/jarvis"
    database_echo: bool = False

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    # Embedding model
    embedding_model_name: str = "dragonkue/multilingual-e5-small-ko"
    embedding_dim: int = 384

    # Entity resolution thresholds
    entity_auto_merge_threshold: float = 0.92
    entity_merge_log_threshold: float = 0.85
    entity_review_threshold: float = 0.78

    # Search
    search_default_limit: int = 10
    search_rrf_k: int = 60

    # OAuth
    oauth_issuer: str = "http://localhost:8000"
    oauth_token_ttl_seconds: int = 3600
    oauth_refresh_token_ttl_seconds: int = 86400 * 30
    oauth_secret_key: str = "change-me-in-production"

    model_config = {"env_prefix": "JARVIS_", "env_file": ".env"}


settings = Settings()
