"""The reply while it is written (audit item 7): the draft read from streamed arguments, the
gateway streaming the writer's call only for a turn with a sink, and the streamed chat route."""

import asyncio
import json
from typing import Any

from fastapi.testclient import TestClient

from rockygpt_brain import drafts
from rockygpt_brain.api.app import create_app
from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.engine import ChatEngine, ChatResult, answered
from rockygpt_brain.provider import Gateway, TurnBudget
from rockygpt_brain.turn import Turn
from test_provider_gateway import NOW, SETTINGS, FakeLedger, FakeTransport, response


def test_the_text_so_far_is_read_from_partial_arguments() -> None:
    assert drafts.text_so_far('{"te') is None
    assert drafts.text_so_far('{"text":"') == ""
    assert drafts.text_so_far('{"text": "Go to Room D-') == "Go to Room D-"
    assert drafts.text_so_far('{"text":"Line one\\nLine') == "Line one\nLine"
    assert drafts.text_so_far('{"text":"Say \\"hi\\" now"}') == 'Say "hi" now'
    # An escape cut in two waits for its rest.
    assert drafts.text_so_far('{"text":"a\\') == "a"
    assert drafts.text_so_far('{"text":"caf\\u00e') == "caf"
    assert drafts.text_so_far('{"text":"caf\\u00e9!') == "café!"
    assert drafts.text_so_far('{"text":"\\ud83d\\ude00 ok') == "\U0001F600 ok"
    assert drafts.text_so_far('{"text":"\\ud83d') == ""
    # The parts-first writer writes its parts before the text.
    assert drafts.text_so_far('{"parts":[{"asked":"text"}],"text":"Yes') == "Yes"


def test_the_draft_a_student_sees_has_no_section_marks() -> None:
    assert drafts.shown("Fill in the form [3]. Then email it [2, 5].") == \
        "Fill in the form. Then email it."
    assert drafts.shown("Fill in the form [") == "Fill in the form"
    assert drafts.shown("Fill in the form [1") == "Fill in the form"


class StreamingTransport(FakeTransport):
    def __init__(self, ledger: FakeLedger, result: dict[str, Any], pieces: list[str]) -> None:
        super().__init__(ledger, result)
        self.pieces = pieces

    async def stream(self, request: dict[str, Any], remaining_seconds: float,
                     on_arguments: Any) -> dict[str, Any]:
        self.ledger.events.append("stream")
        self.request = request
        so_far = ""
        for piece in self.pieces:
            so_far += piece
            on_arguments(so_far)
        return self.response


def writer_response(text: str) -> dict[str, Any]:
    result = response()
    result["output"] = [{"type": "function_call", "call_id": "call", "name": "write_answer",
                         "arguments": json.dumps({"text": text})}]
    return result


def run_writer(transport: FakeTransport, ledger: FakeLedger, sink: Any) -> Any:
    gateway = Gateway(SETTINGS, ledger=ledger, transport=transport, now=lambda: NOW,
                      monotonic=lambda: 1.0)

    async def go() -> Any:
        drafts.DRAFT_SINK.set(sink)
        return await gateway.complete(
            input=[{"role": "user", "content": "q"}],
            tools=[{"type": "function", "name": "write_answer"}],
            budget=TurnBudget("request", 10.0))
    return asyncio.run(go())


def test_a_turn_with_a_sink_streams_the_writer_and_bills_the_same() -> None:
    ledger = FakeLedger()
    pieces = ['{"te', 'xt":"Fill in', ' the form [3', ']."}']
    transport = StreamingTransport(ledger, writer_response("Fill in the form [3]."), pieces)
    seen: list[str] = []
    result = run_writer(transport, ledger, seen.append)
    assert seen == ["Fill in", "Fill in the form [3", "Fill in the form [3]."]
    assert ledger.events == ["reserve", "stream", "settle"]
    assert result.tool_calls[0].arguments == {"text": "Fill in the form [3]."}


def test_without_a_sink_the_writer_is_not_streamed() -> None:
    ledger = FakeLedger()
    transport = StreamingTransport(ledger, writer_response("Done."), ['{"text":"Done."}'])
    run_writer(transport, ledger, None)
    assert ledger.events == ["reserve", "send", "settle"]


def test_a_sink_that_fails_never_fails_the_answer() -> None:
    def broken(text: str) -> None:
        raise RuntimeError("closed")

    ledger = FakeLedger()
    transport = StreamingTransport(ledger, writer_response("Done."), ['{"text":"Done."}'])
    result = run_writer(transport, ledger, broken)
    assert result.tool_calls[0].arguments == {"text": "Done."}


class DraftingEngine(ChatEngine):
    """Writes its reply through the draft sink the way the gateway does, then answers."""

    def __init__(self) -> None:
        self.gateway = object()  # type: ignore[assignment]
        self.turn_seconds = 5.0
        self.output = "text"

    async def readiness(self) -> bool:
        return True

    async def answer(self, turn: Turn, request: ChatRequest, *,
                     floor_situation: str | None = None) -> ChatResult:
        sink = drafts.DRAFT_SINK.get()  # None for a plain JSON request
        for text in ("Fill in", "Fill in the form [3]."):
            if sink is not None:
                sink(text)
                await asyncio.sleep(0.3)
        return answered(turn, "Fill in the form.", "answered")


def frames(text: str) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for frame in text.split("\n\n"):
        lines = frame.splitlines()
        event = next((line[6:].strip() for line in lines if line.startswith("event:")), "")
        data = [line[5:].strip() for line in lines if line.startswith("data:")]
        if event and data:
            out.append((event, json.loads("\n".join(data))))
    return out


QUESTION = {"messages": [{"role": "user", "content": "How do I appeal a grade?"}]}


def test_a_streamed_request_gets_the_draft_then_the_same_answer() -> None:
    with TestClient(create_app(DraftingEngine(), environment="development")) as client:
        streamed = client.post("/v1/chat", json=QUESTION,
                               headers={"accept": "text/event-stream"})
        plain = client.post("/v1/chat", json=QUESTION)
    assert streamed.status_code == 200
    assert streamed.headers["content-type"].startswith("text/event-stream")
    assert streamed.headers["cache-control"] == "no-store"
    got = frames(streamed.text)
    assert got[0] == ("progress", {"stage": "understanding"})
    assert ("progress", {"stage": "reviewing", "draft": "Fill in"}) in got
    assert ("progress", {"stage": "reviewing", "draft": "Fill in the form."}) in got
    event, result = got[-1]
    assert event == "result" and result["status"] == 200
    assert result["body"]["answer"] == plain.json()["answer"] == "Fill in the form."
    assert result["body"]["requestId"] == streamed.headers["x-request-id"]


def test_without_an_engine_the_stream_ends_with_the_same_error() -> None:
    with TestClient(create_app(environment="development")) as client:
        streamed = client.post("/v1/chat", json=QUESTION,
                               headers={"accept": "text/event-stream"})
    event, result = frames(streamed.text)[-1]
    assert event == "result" and result["status"] == 503
    assert result["body"]["reason"] == "model_not_configured"
