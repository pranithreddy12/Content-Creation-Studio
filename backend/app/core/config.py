from functools import lru_cache
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Core
    app_env: str = "dev"
    app_name: str = "ai-content-studio"
    log_level: str = "info"
    secret_key: str = Field(min_length=16, default="dev-secret-change-me-please-32b")
    cors_origins: List[str] = ["*"]

    # DB
    database_url: str = "postgresql+asyncpg://studio:studio@postgres:5432/studio"
    pool_size: int = 20
    max_overflow: int = 10

    # Redis
    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str = "redis://redis:6379/1"
    celery_result_backend: str = "redis://redis:6379/2"

    # Qdrant
    qdrant_url: str = "http://qdrant:6333"
    qdrant_api_key: Optional[str] = None

    # S3
    s3_endpoint: Optional[str] = None
    s3_region: str = "us-east-1"
    s3_bucket: str = "studio-media"
    s3_access_key: Optional[str] = None
    s3_secret_key: Optional[str] = None
    s3_public_base: Optional[str] = None

    # Auth (Clerk)
    clerk_secret_key: Optional[str] = None
    clerk_publishable_key: Optional[str] = None
    clerk_jwt_issuer: Optional[str] = None
    clerk_jwks_url: Optional[str] = None

    # Payments
    stripe_secret_key: Optional[str] = None
    stripe_webhook_secret: Optional[str] = None
    stripe_price_pro: Optional[str] = None
    stripe_price_agency: Optional[str] = None

    # LLM
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    gemini_api_key: Optional[str] = None
    voyage_api_key: Optional[str] = None
    elevenlabs_api_key: Optional[str] = None
    replicate_api_token: Optional[str] = None

    # Social
    linkedin_client_id: Optional[str] = None
    linkedin_client_secret: Optional[str] = None
    x_client_id: Optional[str] = None
    x_client_secret: Optional[str] = None
    facebook_app_id: Optional[str] = None
    facebook_app_secret: Optional[str] = None
    instagram_app_id: Optional[str] = None
    tiktok_client_key: Optional[str] = None
    tiktok_client_secret: Optional[str] = None
    youtube_client_id: Optional[str] = None
    youtube_client_secret: Optional[str] = None

    # Research
    serpapi_key: Optional[str] = None
    reddit_client_id: Optional[str] = None
    reddit_client_secret: Optional[str] = None
    reddit_user_agent: str = "ai-content-studio/1.0"

    # Observability
    sentry_dsn: Optional[str] = None
    posthog_api_key: Optional[str] = None
    posthog_host: str = "https://us.posthog.com"
    prometheus_port: int = 9100


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
