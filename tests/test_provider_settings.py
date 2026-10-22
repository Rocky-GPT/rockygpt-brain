"""Configuration checks use synthetic credentials and never open connections."""

import json
from datetime import datetime
from importlib.resources import files

import pytest

from rockygpt_brain.settings import ConfigurationError, ProviderSettings


def environment() -> dict[str, str]:
    return {
        "BRAIN_ENVIRONMENT": "development", "BRAIN_OPENAI_API_KEY": "fake-private-key",
        "BRAIN_OPENAI_PROJECT": "fake-project", "BRAIN_LEDGER_DATABASE_URL": "fake-private-dsn",
        "BRAIN_OPENAI_MODEL": "fake-model", "BRAIN_OPENAI_INPUT_NUSD_PER_TOKEN": "125",
        "BRAIN_OPENAI_OUTPUT_NUSD_PER_TOKEN": "500",
        "BRAIN_OPENAI_PRICE_VALID_UNTIL": "2099-01-01T00:00:00+00:00",
    }


def test_explicit_config_redacts_credentials_and_defaults_cache_conservatively() -> None:
    settings = ProviderSettings.from_env(environment())
    assert "fake-private" not in repr(settings)
    assert settings.prices.cached_input_nusd == settings.prices.input_nusd
    assert settings.max_turn_nusd == 25_000_000


@pytest.mark.parametrize("key,value", [
    ("BRAIN_ENVIRONMENT", "test"), ("BRAIN_OPENAI_API_KEY", ""),
    ("BRAIN_OPENAI_INPUT_NUSD_PER_TOKEN", "-1"),
    ("BRAIN_OPENAI_OUTPUT_NUSD_PER_TOKEN", "NaN"),
    ("BRAIN_OPENAI_CACHED_INPUT_NUSD_PER_TOKEN", "1000"),
    ("BRAIN_OPENAI_PRICE_VALID_UNTIL", "2000-01-01T00:00:00Z"),
    ("BRAIN_OPENAI_PRICE_VALID_UNTIL", "2099-01-01"),
    ("BRAIN_MAX_INPUT_BYTES", "100001"), ("BRAIN_MAX_OUTPUT_TOKENS", "0"),
])
def test_missing_or_invalid_values_fail_closed(key: str, value: str) -> None:
    env = environment()
    env[key] = value
    with pytest.raises(ConfigurationError):
        ProviderSettings.from_env(env)


def test_partial_price_override_is_rejected() -> None:
    env = environment()
    del env["BRAIN_OPENAI_OUTPUT_NUSD_PER_TOKEN"]
    with pytest.raises(ConfigurationError):
        ProviderSettings.from_env(env)


def test_bundled_release_has_review_date_and_conservative_cache_write_price() -> None:
    release = json.loads(files("rockygpt_brain").joinpath("provider-release.json").read_text())
    assert release["model"] == "gpt-6-luna"
    assert release["input_nusd_per_token"] == 125
    assert release["cached_input_nusd_per_token"] == 10
    assert release["output_nusd_per_token"] == 500
    assert datetime.fromisoformat(release["valid_until"]).tzinfo is not None
    assert release["source"].startswith("https://developers.openai.com/")
