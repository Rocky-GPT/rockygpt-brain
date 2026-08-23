from rockygpt_brain.config import Settings


def _settings(**overrides: object) -> Settings:
    # _env_file=None so a developer's real .env never leaks into these.
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


class TestBlankEnvironmentValues:
    """`KEY=` in a .env means "not filled in yet", not "the empty string"."""

    def test_blank_model_falls_back_to_the_default(self) -> None:
        # The regression this exists for: a blank value used to win over the
        # default and ship `model: ""` to the provider, which came back as a
        # generic 503 with nothing pointing at configuration.
        assert _settings(OPENAI_CHAT_MODEL="").openai_chat_model == "gpt-4.1-mini"

    def test_whitespace_only_value_also_counts_as_unset(self) -> None:
        assert _settings(OPENAI_CHAT_MODEL="   ").openai_chat_model == "gpt-4.1-mini"

    def test_blank_optional_secret_is_none_not_an_empty_secret(self) -> None:
        # An empty SecretStr is truthy, so a blank DATABASE_URL would read as
        # configured and get dialed at startup.
        settings = _settings(DATABASE_URL="", OPENAI_API_KEY="", ADMIN_API_TOKEN="")
        assert settings.database_url is None
        assert settings.openai_api_key is None
        assert settings.admin_api_token is None

    def test_blank_admin_token_leaves_admin_routes_disabled(self) -> None:
        assert _settings(ADMIN_API_TOKEN="").admin_enabled is False

    def test_blank_staging_token_does_not_gate_requests(self) -> None:
        assert _settings(STAGING_SERVICE_TOKEN="").environment_token_required is False

    def test_real_values_are_untouched(self) -> None:
        settings = _settings(OPENAI_CHAT_MODEL="gpt-4o", HOST="192.0.2.10")
        assert settings.openai_chat_model == "gpt-4o"
        assert settings.host == "192.0.2.10"

    def test_data_url_still_has_its_trailing_slash_stripped(self) -> None:
        assert _settings(DATA_URL="http://example.test/").data_url == "http://example.test"
