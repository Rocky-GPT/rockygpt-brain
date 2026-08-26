from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: Literal["development", "test", "staging", "production"] = "development"
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    openai_api_key: SecretStr | None = None
    openai_chat_model: str = "gpt-4.1-mini"
    openai_planner_model: str = "gpt-4.1-mini"
    openai_web_model: str = "gpt-4.1-mini"
    data_url: str = "http://127.0.0.1:8100"
    data_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    campus_timezone: str = "America/New_York"
    rag_enabled: bool = False
    staging_service_token: SecretStr | None = None
    admin_api_token: SecretStr | None = None
    admin_enabled: bool = True

    @staticmethod
    def secret_value(value: SecretStr | None) -> str | None:
        return value.get_secret_value() if value else None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
