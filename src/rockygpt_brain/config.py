from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: Literal["development", "test", "production"] = "development"
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    openai_api_key: SecretStr | None = None
    openai_chat_model: str = "gpt-4.1-mini"
    openai_planner_model: str = "gpt-4.1-mini"
    openai_web_model: str = "gpt-4.1-mini"
    database_url: SecretStr | None = None
    chat_log_hash_key: SecretStr | None = None
    data_url: str = "http://127.0.0.1:8100"
    data_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    data_backend: Literal["http", "postgres"] = "postgres"
    campus_timezone: str = "America/New_York"
    rag_enabled: bool = False
    admin_api_token: SecretStr | None = None
    admin_enabled: bool = True

    @model_validator(mode="after")
    def require_durable_production_logs(self) -> Settings:
        if self.app_env != "production":
            return self
        if self.database_url is None:
            raise ValueError("DATABASE_URL is required in production")
        hash_key = self.secret_value(self.chat_log_hash_key)
        if hash_key is None or len(hash_key) < 32:
            raise ValueError("CHAT_LOG_HASH_KEY must contain at least 32 characters")
        return self

    @staticmethod
    def secret_value(value: SecretStr | None) -> str | None:
        return value.get_secret_value() if value else None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
