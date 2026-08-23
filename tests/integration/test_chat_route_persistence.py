"""Route-level regression: POST /v1/chat must fail fast on missing
persistence configuration, *before* any paid model call — not compute an
answer via the orchestrator/model and only then discover it can't be
persisted. See api/chat.py's fail-fast check ahead of `run_chat_turn`.

Uses `httpx.AsyncClient` + `httpx.ASGITransport` directly against the app,
rather than `starlette.testclient.TestClient` (which now emits
`StarletteDeprecationWarning: Using httpx with starlette.testclient is
deprecated; install httpx2 instead` on this httpx/Starlette combination).
This keeps the test on the already-installed httpx/pytest stack with no
new dependency. `TestClient`'s `with TestClient(app) as client:` also runs
the app's ASGI lifespan (startup/shutdown) for you; the equivalent here is
driving `app.router.lifespan_context(app)` explicitly as an async context
manager — same effect (the real startup/shutdown code in app.py's
`_build_lifespan` still runs), just spelled out rather than hidden inside
`TestClient`.
"""

from unittest.mock import AsyncMock, patch

import httpx

from rockygpt_brain.app import create_app
from rockygpt_brain.config import Settings


def _settings_without_database() -> Settings:
    return Settings(
        APP_ENV="development",
        DATA_URL="http://127.0.0.1:8100",
        OPENAI_API_KEY="test-key",
        OPENAI_CHAT_MODEL="gpt-test",
        DATABASE_URL=None,
        CHAT_LOG_HASH_KEY=None,
        ADMIN_API_TOKEN=None,
        ABUSE_HASH_KEY=None,
        STAGING_SERVICE_TOKEN=None,
    )


async def test_missing_database_returns_503_without_calling_the_model() -> None:
    app = create_app(settings=_settings_without_database())

    # Patched where it's *used* (rockygpt_brain.api.chat), not where it's
    # defined (rockygpt_brain.brain.orchestrator) — `from ... import
    # run_chat_turn` in chat.py already bound its own module-level name at
    # import time, so patching the orchestrator module's attribute would
    # not affect the reference chat.py actually calls.
    with patch(
        "rockygpt_brain.api.chat.run_chat_turn", new_callable=AsyncMock
    ) as mocked_run_chat_turn:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                response = await client.post("/v1/chat", json={"message": "hello"})

    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "SERVICE_UNAVAILABLE"
    mocked_run_chat_turn.assert_not_called()
