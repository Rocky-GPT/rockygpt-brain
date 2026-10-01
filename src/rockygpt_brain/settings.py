"""Explicit server configuration; importing Brain never reads credentials."""

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.resources import files
from typing import Literal

Environment = Literal["development", "production"]


class ConfigurationError(ValueError):
    """A required server setting is absent or invalid (never includes its value)."""


@dataclass(frozen=True)
class Prices:
    model: str
    input_nusd: int
    output_nusd: int
    cached_input_nusd: int
    valid_until: datetime

    def valid(self, now: datetime) -> bool:
        return (
            bool(self.model)
            and self.input_nusd > 0
            and self.output_nusd > 0
            and 0 <= self.cached_input_nusd <= self.input_nusd
            and self.valid_until.tzinfo is not None
            and now < self.valid_until
        )

    def cost(self, input_tokens: int, output_tokens: int, cached_tokens: int = 0) -> int:
        return ((input_tokens - cached_tokens) * self.input_nusd
                + cached_tokens * self.cached_input_nusd + output_tokens * self.output_nusd)

    def metadata(self) -> dict[str, str | int]:
        return {
            "model": self.model,
            "input_nusd_per_token": self.input_nusd,
            "cached_input_nusd_per_token": self.cached_input_nusd,
            "output_nusd_per_token": self.output_nusd,
            "valid_until": self.valid_until.isoformat(),
        }


@dataclass(frozen=True)
class ProviderSettings:
    environment: Environment
    api_key: str = field(repr=False)
    project: str = field(repr=False)
    ledger_url: str = field(repr=False)
    prices: Prices
    max_input_bytes: int = 100_000
    max_output_tokens: int = 1_200
    max_turn_nusd: int = 25_000_000

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "ProviderSettings":
        env = os.environ if environ is None else environ

        def required(name: str) -> str:
            value = env.get(name, "").strip()
            if not value:
                raise ConfigurationError(f"Missing {name}")
            return value

        environment = required("BRAIN_ENVIRONMENT")
        if environment not in {"development", "production"}:
            raise ConfigurationError("Invalid BRAIN_ENVIRONMENT")
        release = json.loads(files("rockygpt_brain").joinpath("provider-release.json").read_text())
        override_names = (
            "BRAIN_OPENAI_MODEL", "BRAIN_OPENAI_INPUT_NUSD_PER_TOKEN",
            "BRAIN_OPENAI_OUTPUT_NUSD_PER_TOKEN", "BRAIN_OPENAI_PRICE_VALID_UNTIL",
        )
        override = any(name in env for name in (*override_names,
                                                "BRAIN_OPENAI_CACHED_INPUT_NUSD_PER_TOKEN"))
        try:
            if override:
                model = required("BRAIN_OPENAI_MODEL")
                input_rate = int(required("BRAIN_OPENAI_INPUT_NUSD_PER_TOKEN"))
                output_rate = int(required("BRAIN_OPENAI_OUTPUT_NUSD_PER_TOKEN"))
                cached_rate = int(env.get(
                    "BRAIN_OPENAI_CACHED_INPUT_NUSD_PER_TOKEN", str(input_rate),
                ))
                expires = datetime.fromisoformat(required("BRAIN_OPENAI_PRICE_VALID_UNTIL"))
            else:
                model = release["model"]
                input_rate = release["input_nusd_per_token"]
                output_rate = release["output_nusd_per_token"]
                cached_rate = release["cached_input_nusd_per_token"]
                expires = datetime.fromisoformat(release["valid_until"])
            max_input = int(env.get("BRAIN_MAX_INPUT_BYTES", "100000"))
            max_output = int(env.get("BRAIN_MAX_OUTPUT_TOKENS", "1200"))
            max_turn = int(env.get("BRAIN_MAX_TURN_NUSD", "25000000"))
        except (ValueError, OverflowError, KeyError) as error:
            raise ConfigurationError("Invalid provider pricing configuration") from error
        prices = Prices(model, input_rate, output_rate, cached_rate, expires)
        if not prices.valid(datetime.now(UTC)):
            raise ConfigurationError("Provider pricing is invalid or expired")
        if not (1 <= max_input <= 100_000 and 1 <= max_output <= 8_000
                and 1 <= max_turn <= 1_000_000_000):
            raise ConfigurationError("Invalid provider execution limits")
        return cls(
            environment=environment,  # type: ignore[arg-type]
            api_key=required("BRAIN_OPENAI_API_KEY"),
            project=required("BRAIN_OPENAI_PROJECT"),
            ledger_url=required("BRAIN_LEDGER_DATABASE_URL"),
            prices=prices, max_input_bytes=max_input, max_output_tokens=max_output,
            max_turn_nusd=max_turn,
        )
