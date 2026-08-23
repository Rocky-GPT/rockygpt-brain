"""Environment-driven configuration.

Every value here is read from the process environment (optionally via a
local `.env` file in development). Nothing here is a secret literal; secrets
are only ever read, never written back or logged. See `.env.example` for the
canonical list of variable names.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = Field(default="development", alias="APP_ENV")
    host: str = Field(default="127.0.0.1", alias="HOST")
    port: int = Field(default=8000, alias="PORT")

    openai_api_key: SecretStr | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_chat_model: str = Field(default="gpt-4.1-mini", alias="OPENAI_CHAT_MODEL")

    database_url: SecretStr | None = Field(default=None, alias="DATABASE_URL")

    data_url: str = Field(default="http://127.0.0.1:8100", alias="DATA_URL")

    chat_log_hash_key: SecretStr | None = Field(default=None, alias="CHAT_LOG_HASH_KEY")
    admin_api_token: SecretStr | None = Field(default=None, alias="ADMIN_API_TOKEN")
    abuse_hash_key: SecretStr | None = Field(default=None, alias="ABUSE_HASH_KEY")
    staging_service_token: SecretStr | None = Field(
        default=None, alias="STAGING_SERVICE_TOKEN"
    )

    @model_validator(mode="before")
    @classmethod
    def _blank_means_unset(cls, data: Any) -> Any:
        """Treat a blank environment variable as absent, not as a value.

        `.env` files and deployment consoles both spell "I have not filled
        this in yet" as `KEY=`, but pydantic-settings hands that through as
        the empty string — which is a *value*, so it silently beats the
        field default. `OPENAI_CHAT_MODEL=` shipped `model: ""` to the
        provider and surfaced as a generic "model provider is unavailable"
        503, with nothing pointing at configuration.

        Dropping blank keys here fixes the whole class at once: fields with
        defaults fall back to them, and optional secrets stay `None` rather
        than becoming an empty `SecretStr` — which matters because a
        `SecretStr("")` is truthy, so an unfilled `DATABASE_URL` would
        otherwise read as configured and be dialed.
        """
        if isinstance(data, dict):
            return {
                key: value
                for key, value in data.items()
                if not (isinstance(value, str) and not value.strip())
            }
        return data

    @field_validator("data_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def environment_token_required(self) -> bool:
        return self.staging_service_token is not None and bool(
            self.staging_service_token.get_secret_value()
        )

    @property
    def admin_enabled(self) -> bool:
        return self.admin_api_token is not None and bool(
            self.admin_api_token.get_secret_value()
        )

    @property
    def abuse_signature_enabled(self) -> bool:
        return self.abuse_hash_key is not None and bool(
            self.abuse_hash_key.get_secret_value()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
