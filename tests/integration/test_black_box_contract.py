"""Black-box HTTP integration tests against the actual FastAPI app + ASGI
stack (spec/acceptance.md is explicitly a black-box contract), covering
what `tests/unit`'s function-level tests don't: real request parsing,
real middleware ordering, and real routing/error-envelope behavior end to
end.

Uses `httpx.AsyncClient` + `httpx.ASGITransport` directly against a
`create_app(settings=...)` instance, driving `app.router.lifespan_context`
explicitly — the same already-installed-stack pattern as
`test_chat_route_persistence.py` (no `starlette.testclient`, no new
dependency). Every case here uses an explicit `Settings(...)` for exactly
the configuration it's testing, not implicit environment state.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest

from rockygpt_brain.app import create_app
from rockygpt_brain.config import Settings

MAX_CHAT_BODY_BYTES = 32_768


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "APP_ENV": "development",
        # Port 1 is never bound, so the dataset probe fails by connection
        # refusal no matter what else is running on this machine. Using the
        # real 8100 here made these tests pass only while a local data
        # service happened to be down.
        "DATA_URL": "http://127.0.0.1:1",
        "OPENAI_API_KEY": "test-key",
        "DATABASE_URL": None,
        "CHAT_LOG_HASH_KEY": None,
        "ADMIN_API_TOKEN": None,
        "ABUSE_HASH_KEY": None,
        "STAGING_SERVICE_TOKEN": None,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@asynccontextmanager
async def _client_for(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings=settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


def _assert_error_envelope(response: httpx.Response, *, status_code: int, code: str) -> dict:
    assert response.status_code == status_code
    body = response.json()
    assert isinstance(body.get("requestId"), str) and body["requestId"]
    error = body["error"]
    assert isinstance(error, dict)
    assert error["code"] == code
    assert isinstance(error["message"], str) and error["message"]
    assert isinstance(error["retryable"], bool)
    # Every response carries a stable request id in both the body and the
    # X-Request-Id header (spec/brain-api.openapi.yaml) — the two must
    # agree, not just both independently exist.
    assert response.headers.get("x-request-id") == body["requestId"]
    return body


class TestProbesArePublic:
    async def test_health_bypasses_a_configured_staging_token(self) -> None:
        settings = _settings(STAGING_SERVICE_TOKEN="a" * 32)
        async with _client_for(settings) as client:
            response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] in {"healthy", "ok"}
        assert response.headers.get("x-request-id")

    async def test_readiness_bypasses_a_configured_staging_token(self) -> None:
        settings = _settings(STAGING_SERVICE_TOKEN="a" * 32)
        async with _client_for(settings) as client:
            response = await client.get("/readiness")
        # No DB/data service configured here, so "unready" is expected —
        # the point is it's reachable at all without the env-token header.
        assert response.status_code in {200, 503}
        assert response.headers.get("x-request-id")

    async def test_readiness_reports_both_dependencies_failing_when_unconfigured(self) -> None:
        # No DATABASE_URL, and DATA_URL points at a port nothing is
        # listening on in this test environment — both dependencies must
        # be reported, precisely, not just "some non-2xx status."
        settings = _settings()
        async with _client_for(settings) as client:
            response = await client.get("/readiness")
        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "unready"
        assert set(body["failing"]) == {"database", "dataset"}

    async def test_request_id_header_present_on_every_response(self) -> None:
        async with _client_for(_settings()) as client:
            health_response = await client.get("/health")
            missing_response = await client.get("/does-not-exist")
        assert health_response.headers.get("x-request-id")
        assert missing_response.headers.get("x-request-id")


class TestEnvironmentTokenGate:
    @pytest.mark.parametrize(
        "path,method,body",
        [
            ("/v1/chat", "POST", {"message": "hi"}),
            ("/v1/feedback", "POST", {"requestId": "abc123", "rating": 1}),
        ],
    )
    async def test_missing_staging_token_is_401(
        self, path: str, method: str, body: dict[str, object]
    ) -> None:
        settings = _settings(STAGING_SERVICE_TOKEN="correct-token")
        async with _client_for(settings) as client:
            response = await client.request(method, path, json=body)
        _assert_error_envelope(response, status_code=401, code="UNAUTHORIZED")

    async def test_wrong_staging_token_is_401(self) -> None:
        settings = _settings(STAGING_SERVICE_TOKEN="correct-token")
        async with _client_for(settings) as client:
            response = await client.post(
                "/v1/chat",
                json={"message": "hi"},
                headers={"x-rockygpt-environment-token": "wrong-token"},
            )
        _assert_error_envelope(response, status_code=401, code="UNAUTHORIZED")


class TestChatBodyParsingNeverCrashesTheProcess:
    async def test_invalid_json_syntax_is_400(self) -> None:
        async with _client_for(_settings()) as client:
            response = await client.post(
                "/v1/chat",
                content=b"{not valid json",
                headers={"content-type": "application/json"},
            )
            _assert_error_envelope(response, status_code=400, code="INVALID_REQUEST")
            # The process is still responsive after malformed input.
            follow_up = await client.get("/health")
        assert follow_up.status_code == 200

    @pytest.mark.parametrize("scalar_body", [b"null", b"true", b"42", b'"just a string"'])
    async def test_json_scalar_body_is_400(self, scalar_body: bytes) -> None:
        async with _client_for(_settings()) as client:
            response = await client.post(
                "/v1/chat", content=scalar_body, headers={"content-type": "application/json"}
            )
        _assert_error_envelope(response, status_code=400, code="INVALID_REQUEST")

    async def test_unknown_field_is_400(self) -> None:
        async with _client_for(_settings()) as client:
            response = await client.post(
                "/v1/chat", json={"message": "hi", "unexpectedField": "x"}
            )
        _assert_error_envelope(response, status_code=400, code="INVALID_REQUEST")

    async def test_invalid_role_is_400(self) -> None:
        async with _client_for(_settings()) as client:
            response = await client.post(
                "/v1/chat",
                json={"message": "hi", "history": [{"role": "system", "content": "x"}]},
            )
        _assert_error_envelope(response, status_code=400, code="INVALID_REQUEST")

    async def test_over_limit_message_length_is_400(self) -> None:
        async with _client_for(_settings()) as client:
            response = await client.post("/v1/chat", json={"message": "x" * 2001})
        _assert_error_envelope(response, status_code=400, code="INVALID_REQUEST")

    async def test_oversized_body_is_413_and_process_survives(self) -> None:
        oversized_message = "x" * (MAX_CHAT_BODY_BYTES + 1000)
        async with _client_for(_settings()) as client:
            response = await client.post("/v1/chat", json={"message": oversized_message})
            _assert_error_envelope(response, status_code=413, code="PAYLOAD_TOO_LARGE")
            follow_up = await client.get("/health")
        assert follow_up.status_code == 200


class TestUnsupportedPathAndMethod:
    async def test_unknown_path_is_404_with_stable_envelope(self) -> None:
        async with _client_for(_settings()) as client:
            response = await client.get("/v1/does-not-exist")
        _assert_error_envelope(response, status_code=404, code="NOT_FOUND")

    async def test_unsupported_method_is_405_with_stable_envelope(self) -> None:
        async with _client_for(_settings()) as client:
            response = await client.put("/v1/chat", json={"message": "hi"})
        _assert_error_envelope(response, status_code=405, code="INVALID_REQUEST")


class TestAdminRoutes:
    async def test_admin_routes_are_absent_when_disabled(self) -> None:
        settings = _settings(ADMIN_API_TOKEN=None)
        async with _client_for(settings) as client:
            response = await client.get("/v1/admin/logs")
        _assert_error_envelope(response, status_code=404, code="NOT_FOUND")

    async def test_admin_routes_require_bearer_independent_of_env_token_gate(self) -> None:
        # STAGING_SERVICE_TOKEN is deliberately left unset here, so a 401
        # can only come from the admin route's own independent bearer
        # check (spec/acceptance.md: "Admin endpoints independently
        # enforce bearer authentication"), not from the shared env-token
        # middleware gate.
        settings = _settings(ADMIN_API_TOKEN="admin-secret")
        async with _client_for(settings) as client:
            missing_auth = await client.get("/v1/admin/logs")
            wrong_auth = await client.get(
                "/v1/admin/logs", headers={"authorization": "Bearer wrong-token"}
            )
        _assert_error_envelope(missing_auth, status_code=401, code="UNAUTHORIZED")
        _assert_error_envelope(wrong_auth, status_code=401, code="UNAUTHORIZED")

    async def test_admin_routes_reachable_with_correct_bearer(self) -> None:
        # No DATABASE_URL here: this proves the auth layer accepts the
        # correct token and lets the request reach the route body, which
        # then fails closed at exactly 503 SERVICE_UNAVAILABLE for missing
        # persistence — not merely "not a 401."
        settings = _settings(ADMIN_API_TOKEN="admin-secret")
        async with _client_for(settings) as client:
            response = await client.get(
                "/v1/admin/logs", headers={"authorization": "Bearer admin-secret"}
            )
        _assert_error_envelope(response, status_code=503, code="SERVICE_UNAVAILABLE")
