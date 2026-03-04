from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    DATABASE_URL: str = "postgresql+asyncpg://jarvis:jarvis@localhost:5432/jarvis"
    REDIS_URL: str = "redis://localhost:6379/0"
    # LLM: Cerebras (OpenAI 호환 API)
    CEREBRAS_API_KEY: str = "csk-xxx"
    CEREBRAS_BASE_URL: str = "https://api.cerebras.ai/v1"
    CEREBRAS_MODEL: str = "gpt-oss-120b"
    # Embeddings: Jina AI
    JINA_API_KEY: str = "jina_xxx"
    JINA_MODEL: str = "jina-embeddings-v3"
    SECRET_KEY: str = "change-me-in-production"
    ENVIRONMENT: str = "development"


settings = Settings()
