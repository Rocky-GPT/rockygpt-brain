"""Operational context and unverified previews remain separate from final answers."""

import asyncio
import json
from threading import Event
from typing import Any
from unittest.mock import Mock, patch

import pytest
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from rockygpt_brain.api.app import app
from rockygpt_brain.api.stream import stream_turn
from rockygpt_brain.contracts import ChatMessage
from rockygpt_brain.engine import run_turn
from rockygpt_brain.progress import ProgressUpdate, TurnCancelled
from test_api import deployment_environment, gateway_context  # noqa: F401
from test_engine import NOW, RECORD, answer, review, search, tools


def test_stages_follow_actual_tools_and_review() -> None:
    client, data = Mock(), Mock()
    client.create.side_effect = [
        tools(search()),
        answer("D-224", "campus_fact", [RECORD["id"]]),
        review(),
    ]
    data.search.return_value = {"status": "ok", "records": [RECORD]}
    stages: list[ProgressUpdate] = []
    run_turn(
        [ChatMessage(role="user", content="Registrar location?")],
        client=client,
        data=data,
        model="test",
        now=NOW,
        progress=stages.append,
    )
    assert [update["stage"] for update in stages] == [
        "understanding",
        "retrieving",
        "composing",
        "reviewing",
    ]
    assert stages[0]["subjects"] == []
    assert stages[-1]["subjects"] == [{"topic": "contacts"}]
    assert stages[-1]["draft"] == "D-224"
    assert all("draft" not in update for update in stages[:-1])


def test_disconnect_stops_before_next_paid_call() -> None:
    client, data = Mock(), Mock()
    client.create.return_value = tools(search())
    data.search.return_value = {"status": "ok", "records": [RECORD]}

    def progress(stage: ProgressUpdate) -> None:
        if stage["stage"] == "composing":
            raise TurnCancelled()

    with pytest.raises(TurnCancelled):
        run_turn(
            [ChatMessage(role="user", content="Registrar location?")],
            client=client,
            data=data,
            model="test",
            now=NOW,
            progress=progress,
        )
    assert client.create.call_count == 1


def test_progress_arrives_while_answer_is_still_pending() -> None:
    async def exercise() -> None:
        gate = asyncio.Event()
        updates: asyncio.Queue[ProgressUpdate] = asyncio.Queue()
        stopped = Event()

        async def work() -> dict[str, object]:
            await gate.wait()
            return {"answer": "Validated final answer"}

        worker = asyncio.create_task(work())
        stream = stream_turn(
            worker,
            updates,
            stopped,
            1,
            lambda status, reason: JSONResponse({"reason": reason}, status),
        )
        assert '"stage":"connecting"' in await anext(stream)
        updates.put_nowait(
            {"stage": "retrieving", "subjects": [{"topic": "menu", "meal": "latenight"}]}
        )
        frame = await anext(stream)
        assert '"stage":"retrieving"' in frame
        assert '"meal":"latenight"' in frame
        assert not worker.done()
        gate.set()
        result = await anext(stream)
        assert '"answer":"Validated final answer"' in result
        await stream.aclose()
        assert stopped.is_set()

    asyncio.run(exercise())


def test_stream_deadline_does_not_cancel_accounted_worker() -> None:
    async def exercise() -> None:
        gate = asyncio.Event()
        updates: asyncio.Queue[ProgressUpdate] = asyncio.Queue()
        stopped = Event()

        async def work() -> dict[str, object]:
            await gate.wait()
            return {"answer": "Late answer"}

        worker = asyncio.create_task(work())
        stream = stream_turn(
            worker,
            updates,
            stopped,
            0,
            lambda status, reason: JSONResponse({"reason": reason}, status),
        )
        frames = [frame async for frame in stream]
        assert '"status":504' in frames[-1] and "model_timeout" in frames[-1]
        assert stopped.is_set() and not worker.done()
        gate.set()
        await worker

    asyncio.run(exercise())


def test_sse_final_error_preserves_status_request_id_and_retryability() -> None:
    from rockygpt_brain.accounting import PaidCallError

    with (
        patch.dict("os.environ", {"STAGING_SERVICE_TOKEN": ""}),
        patch("rockygpt_brain.api.app.open_gateway", return_value=gateway_context()),
        patch("rockygpt_brain.api.app.CampusData"),
        patch("rockygpt_brain.api.app.run_turn", side_effect=PaidCallError("model_timeout")),
    ):
        response = TestClient(app).post(
            "/v1/chat",
            headers={"accept": "text/event-stream"},
            json={"messages": [{"role": "user", "content": "Hello"}]},
        )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]
    result = frames[-1]
    assert result["status"] == 504
    assert result["body"]["requestId"] == response.headers["x-request-id"]
    assert result["body"]["error"]["retryable"] is True
    assert result["body"]["error"]["code"] == "model_timeout"


def test_json_clients_keep_the_existing_contract() -> None:
    def result(*args: Any, **kwargs: Any) -> dict[str, object]:
        assert kwargs["progress"] is None
        return {"answer": "Hello", "status": "answered", "metrics": {}}

    with (
        patch.dict("os.environ", {"STAGING_SERVICE_TOKEN": ""}),
        patch("rockygpt_brain.api.app.open_gateway", return_value=gateway_context()),
        patch("rockygpt_brain.api.app.CampusData"),
        patch("rockygpt_brain.api.app.run_turn", side_effect=result),
    ):
        response = TestClient(app).post(
            "/v1/chat", json={"messages": [{"role": "user", "content": "Hello"}]}
        )
    assert response.headers["content-type"] == "application/json"
    assert response.json()["answer"] == "Hello"


def test_menu_context_comes_from_validated_lookup_and_persists_into_review() -> None:
    client, data = Mock(), Mock()
    lookup = search(collection="menu", date_from="2026-09-04")
    args = json.loads(lookup.arguments)
    args["filters"] = {"meal": "Late Night"}
    lookup.arguments = json.dumps(args)
    record = {**RECORD, "id": "menu:sample", "collection": "menu"}
    client.create.side_effect = [
        tools(lookup),
        answer("Unchecked draft", "campus_fact", [record["id"]]),
        review("unsupported_claim"),
    ]
    data.search.return_value = {"status": "ok", "records": [record]}
    updates: list[ProgressUpdate] = []
    result = run_turn(
        [ChatMessage(role="user", content="Explain late-night options")],
        client=client,
        data=data,
        model="test",
        now=NOW,
        progress=updates.append,
    )
    expected = [{"topic": "menu", "meal": "latenight", "date_from": "2026-09-04"}]
    assert updates[1]["subjects"] == expected
    assert updates[-1] == {"stage": "reviewing", "subjects": expected, "draft": "Unchecked draft"}
    assert all("draft" not in update for update in updates[:-1])
    assert "Unchecked draft" not in result["answer"]
    # A new request starts with no context from the previous conversation.
    client.create.side_effect = [answer(), review()]
    updates.clear()
    run_turn(
        [ChatMessage(role="user", content="Hello")],
        client=client,
        data=data,
        model="test",
        now=NOW,
        progress=updates.append,
    )
    assert updates[0] == {"stage": "understanding", "subjects": []}
    assert all(update["subjects"] == [] for update in updates)


def test_unknown_meal_and_query_prose_never_enter_progress() -> None:
    from rockygpt_brain.data import SearchQuery
    from rockygpt_brain.progress import search_subject

    query = SearchQuery(
        collection="menu",
        query="private user text",
        date_from=NOW.date(),
        filters={"meal": "arbitrary model prose"},
    )
    assert search_subject(query) == {"topic": "menu", "date_from": "2026-09-04"}
