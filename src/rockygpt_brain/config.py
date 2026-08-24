"""Validated runtime configuration; secrets never enter model prompts or logs."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
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
    database_url: SecretStr | None = None
    data_url: str = "http://127.0.0.1:8100"
    chat_log_hash_key: SecretStr | None = None
    admin_api_token: SecretStr | None = None
    abuse_hash_key: SecretStr | None = None
    staging_service_token: SecretStr | None = None

    campus_timezone: str = "America/New_York"
    max_body_bytes: int = Field(default=65_536, ge=4_096, le=1_048_576)
    chat_rate_limit: int = Field(default=30, ge=1, le=10_000)
    feedback_rate_limit: int = Field(default=60, ge=1, le=10_000)
    rate_window_seconds: int = Field(default=60, ge=1, le=3_600)
    memory_recent_turns: int = Field(default=10, ge=1, le=20)
    memory_claims: int = Field(default=100, ge=1, le=500)
    text_retention_days: int = Field(default=30, ge=1, le=30)
    metadata_retention_days: int = Field(default=90, ge=1, le=90)
    admin_enabled: bool | None = None

    @field_validator("openai_chat_model", mode="before")
    @classmethod
    def blank_model_uses_default(cls, value: object) -> object:
        return "gpt-4.1-mini" if value in (None, "") else value

    @field_validator(
        "openai_api_key",
        "database_url",
        "chat_log_hash_key",
        "admin_api_token",
        "abuse_hash_key",
        "staging_service_token",
        mode="before",
    )
    @classmethod
    def blank_optional_secret_is_unset(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("data_url")
    @classmethod
    def normalize_data_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("DATA_URL must be an http(s) origin")
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_environment_secrets(self) -> "Settings":
        if self.admin_enabled is None:
            self.admin_enabled = self.app_env in {"development", "test", "staging"}
        if self.app_env in {"staging", "production"}:
            required = {
                "CHAT_LOG_HASH_KEY": self.chat_log_hash_key,
                "ABUSE_HASH_KEY": self.abuse_hash_key,
            }
            for name, secret in required.items():
                if secret is None or len(secret.get_secret_value()) < 32:
                    raise ValueError(f"{name} must contain at least 32 characters")
            if self.database_url is None:
                raise ValueError("DATABASE_URL is required outside local development/test")
        return self

    def secret_value(self, value: SecretStr | None) -> str | None:
        return value.get_secret_value() if value is not None else None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
