"""Small environment configuration for the BASE brain."""

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
    #: All temporal resolution happens in this zone. Contract section 5.
    campus_timezone: str = "America/New_York"
    staging_service_token: SecretStr | None = None
    admin_api_token: SecretStr | None = None
    admin_enabled: bool = True

    @staticmethod
    def secret_value(value: SecretStr | None) -> str | None:
        return value.get_secret_value() if value else None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
