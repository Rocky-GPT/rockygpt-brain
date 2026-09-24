"""One versioned release; deployment secrets and balances are separate."""

import hashlib
import json
import os
from datetime import date
from importlib.metadata import version
from importlib.resources import files
from importlib.resources.abc import Traversable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Environment = Literal["development", "production"]
MONTHLY_CAP_NUSD = 10_000_000_000


class ConfigurationError(Exception):
    """The trusted deployment is incomplete or inconsistent."""


class Price(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: str
    valid_from: date
    valid_until: date
    input_nusd: int = Field(gt=0)
    cached_input_nusd: int = Field(ge=0)
    output_nusd: int = Field(ge=0)
    source: str

    @model_validator(mode="after")
    def valid(self) -> "Price":
        if self.cached_input_nusd > self.input_nusd or self.valid_until <= self.valid_from:
            raise ValueError("Invalid price configuration")
        return self


RoutingMode = Literal["off", "shadow", "active"]
RoutingProvider = Literal["typesafe", "openrouter"]


class RoutingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: str
    model: Literal["jev-1.13.0"]
    timeout_seconds: float = Field(gt=0, le=2)
    threshold: float = Field(ge=0.9, le=1)
    max_candidates: int = Field(gt=0, le=24)
    price: Price


class Release(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: str
    provider: Literal["openai"]
    model: Literal["gpt-5.4"]
    draft_reasoning: Literal["none", "low", "medium"]
    continuation_reasoning: Literal["none", "low", "medium"]
    review_reasoning: Literal["medium"]
    draft_output_tokens: int = Field(gt=0, le=128000)
    review_output_tokens: int = Field(gt=0, le=128000)
    max_input_tokens: int = Field(gt=0, le=128000)
    max_draft_calls: int = Field(gt=0, le=3)
    max_model_calls: int = Field(gt=0, le=4)
    max_tool_calls: int = Field(gt=0, le=8)
    max_retrieval_rounds: int = Field(gt=0, le=2)
    max_turn_cost_nusd: int = Field(gt=0, le=MONTHLY_CAP_NUSD)
    turn_seconds: float = Field(gt=0, le=45)
    http_turn_seconds: float = Field(gt=0, le=47)
    answer_reserve_seconds: float = Field(gt=0)
    review_reserve_seconds: float = Field(gt=0)
    active_turns: int = Field(gt=0)
    review_policy: Literal["generated_prose_single_check"]
    price: Price
    routing: RoutingConfig

    def draft_effort(self, call_index: int) -> Literal["none", "low", "medium"]:
        return self.draft_reasoning if call_index == 0 else self.continuation_reasoning

    @model_validator(mode="after")
    def bounded_path(self) -> "Release":
        if not (
            self.max_draft_calls < self.max_model_calls
            and self.max_retrieval_rounds < self.max_draft_calls
            and self.review_reserve_seconds < self.answer_reserve_seconds < self.turn_seconds
            and self.turn_seconds < self.http_turn_seconds
        ):
            raise ValueError("Release must reserve a writer and one check within its deadline")
        return self


RELEASE = Release.model_validate_json(files("rockygpt_brain").joinpath("release.json").read_text())


def configuration_hash() -> str:
    """Includes prompts, schemas, runtime source, and behavior-affecting dependencies."""
    root = files("rockygpt_brain")
    digest = hashlib.sha256(RELEASE.model_dump_json().encode())

    def visit(directory: Traversable, prefix: str = "") -> None:
        for item in sorted(directory.iterdir(), key=lambda item: item.name):
            if item.is_dir() and item.name != "__pycache__":
                visit(item, prefix + item.name + "/")
            elif item.name.endswith((".py", ".md")):
                digest.update((prefix + item.name).encode())
                digest.update(item.read_bytes())

    visit(root)
    for package in ("openai", "httpx", "psycopg", "pydantic", "fastapi"):
        digest.update(f"{package}={version(package)}".encode())
    return digest.hexdigest()


class Deployment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    environment: Environment
    api_key: str = Field(repr=False, min_length=1)
    project: str = Field(min_length=1)
    ledger_url: str = Field(repr=False, min_length=1)
    routing_mode: RoutingMode = "off"
    routing_provider: RoutingProvider = "typesafe"
    typesafe_api_key: str | None = Field(default=None, repr=False)
    openrouter_api_key: str | None = Field(default=None, repr=False)

    @property
    def routing_api_key(self) -> str | None:
        if self.routing_provider == "openrouter":
            return self.openrouter_api_key
        return self.typesafe_api_key

    @model_validator(mode="after")
    def routing_credentials(self) -> "Deployment":
        if self.routing_mode != "off" and not self.routing_api_key:
            raise ValueError("Routing requires a credential for its provider")
        return self


def load_deployment() -> Deployment:
    """Only one environment's secrets may be mounted in a process. No legacy fallback."""
    try:
        deployment = Deployment.model_validate(
            {
                "environment": os.environ["BRAIN_ENVIRONMENT"],
                "api_key": os.environ["BRAIN_OPENAI_API_KEY"],
                "project": os.environ["BRAIN_OPENAI_PROJECT"],
                "ledger_url": os.environ["BRAIN_LEDGER_DATABASE_URL"],
                "routing_mode": os.getenv("BRAIN_ROUTING_MODE", "off"),
                "routing_provider": os.getenv("BRAIN_ROUTING_PROVIDER") or "typesafe",
                "typesafe_api_key": os.getenv("BRAIN_TYPESAFE_API_KEY") or None,
                "openrouter_api_key": os.getenv("BRAIN_OPENROUTER_API_KEY") or None,
            }
        )
        if os.getenv("OPENAI_CHAT_MODEL", RELEASE.model) != RELEASE.model:
            raise ValueError("Environment model override would break release parity")
        expected = os.getenv("BRAIN_EXPECTED_CONFIG_HASH")
        if expected and expected != configuration_hash():
            raise ValueError("Release identity mismatch")
        return deployment
    except (KeyError, ValueError) as error:
        raise ConfigurationError("Brain deployment is not configured") from error


if __name__ == "__main__":
    print(json.dumps({"version": RELEASE.version, "configurationHash": configuration_hash()}))
