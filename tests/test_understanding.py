"""Step 4: the Jev call. No test reaches Typesafe; a fake stands in for its answers."""

from datetime import datetime
from typing import Any

import httpx
import pytest

from rockygpt_brain.context import Context, build_context
from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.turn import CAMPUS_TZ, Turn
from rockygpt_brain.understanding import (
    NEEDS,
    QUESTIONS,
    TOPICS,
    JevError,
    Understanding,
    request_body,
    understand,
)

NOW = datetime(2026, 9, 30, 14, 5, tzinfo=CAMPUS_TZ)


def read(*lines: str) -> tuple[Turn, Context]:
    request = ChatRequest.model_validate(
        {"messages": [{"role": "user", "content": t} for t in lines]}
    )
    return Turn(lines[-1], "conversation", "request", NOW), build_context(request)


def answers(needs: str, topic: str, history: float = 0.0, danger: float = 0.0,
            multi: float = 0.0) -> dict[str, Any]:
    return {"model": "jev-1.13.0", "answers": {
        "needs": {"type": "choice", "choice": needs},
        "topic": {"type": "choice", "choice": topic},
        "needs_history": {"type": "noul", "noul": history},
        "danger": {"type": "noul", "noul": danger},
        "multi_part": {"type": "noul", "noul": multi},
    }}


def test_the_request_asks_the_five_questions_with_the_spec_options() -> None:
    body = request_body(*read("What's the next shuttle?"))
    assert body["model"] == "jev-1.13.0"
    assert list(body["questions"]) == ["needs", "topic", "needs_history", "danger", "multi_part"]
    assert list(NEEDS) == ["campus_info", "conversation", "outside", "own_account", "unclear"]
    assert list(TOPICS) == [
        "transport", "dining", "offices", "academics", "events", "housing", "none"
    ]
    assert [q["type"] for q in QUESTIONS.values()] == ["choice", "choice", "noul", "noul", "noul"]


def test_jev_is_given_the_turn_and_the_context() -> None:
    turn, context = read("Where is Financial Aid?", "What's their phone number?")
    assert request_body(turn, context)["state"] == {
        "latest_message": "What's their phone number?",
        "recent_messages": [{"role": "user", "content": "Where is Financial Aid?"}],
        "current_topic": "financial aid",
        "referenced_entities": ["Financial Aid"],
        "campus_now": "2026-09-30T14:05:00-04:00",
    }


def test_the_same_turn_and_context_make_the_same_request() -> None:
    lines = ("What's the next shuttle?", "What about tomorrow?")
    assert request_body(*read(*lines)) == request_body(*read(*lines))
    assert request_body(*read(*lines)) != request_body(*read(lines[0]))


@pytest.mark.parametrize(("reply", "expected"), [
    (answers("campus_info", "transport"),
     Understanding("campus_info", "transport", False, False, False)),
    (answers("campus_info", "transport", history=0.98),
     Understanding("campus_info", "transport", True, False, False)),
    (answers("conversation", "none"), Understanding("conversation", "none", False, False, False)),
    (answers("outside", "none"), Understanding("outside", "none", False, False, False)),
    (answers("own_account", "academics"),
     Understanding("own_account", "academics", False, False, False)),
    (answers("campus_info", "offices", 0.1, 0.9, 0.7),
     Understanding("campus_info", "offices", False, True, True)),
])
def test_jevs_answers_become_an_understanding(
        reply: dict[str, Any], expected: Understanding) -> None:
    assert understand(*read("hi"), post=lambda body: reply) == expected


@pytest.mark.parametrize("field", ["needs_history", "danger", "multi_part"])
@pytest.mark.parametrize(("noul", "yes"), [(0.5, True), (0.51, True), (0.49, False), (0, False)])
def test_yes_is_a_probability_of_half_or_more(field: str, noul: float, yes: bool) -> None:
    reply = answers("outside", "none", **{{"needs_history": "history", "multi_part": "multi"}.get(
        field, field): noul})
    assert getattr(understand(*read("hi"), post=lambda body: reply), field) is yes


@pytest.mark.parametrize("bad", [
    {},
    {"answers": {}},
    {"answers": None},
    {"answers": ["not", "a", "dict"]},
    {**answers("campus_info", "transport"), "answers": {"needs": {"choice": "campus_info"}}},
    answers("weather", "transport"),
    answers("campus_info", "sports"),
    answers("campus_info", "transport", multi="yes"),  # type: ignore[arg-type]
])
def test_an_answer_of_the_wrong_shape_is_refused(bad: dict[str, Any]) -> None:
    with pytest.raises(JevError):
        understand(*read("hi"), post=lambda body: bad)


def test_a_failed_call_raises_httpxs_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAIN_TYPESAFE_API_KEY", "test-key")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(
        429, request=httpx.Request("POST", "https://api.typesafe.ai/v1/systemone")))
    with pytest.raises(httpx.HTTPStatusError):
        understand(*read("hi"))


def test_without_a_key_nothing_is_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BRAIN_TYPESAFE_API_KEY", raising=False)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("sent"))
    with pytest.raises(JevError):
        understand(*read("hi"))


def test_a_good_call_sends_the_key_and_the_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAIN_TYPESAFE_API_KEY", "test-key")
    sent: dict[str, Any] = {}

    def post(url: str, **kwargs: Any) -> httpx.Response:
        sent.update(url=url, **kwargs)
        return httpx.Response(
            200, json=answers("campus_info", "transport"), request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(httpx, "post", post)
    turn, context = read("What's the next shuttle?")
    assert understand(turn, context).topic == "transport"
    assert sent["url"] == "https://api.typesafe.ai/v1/systemone"
    assert sent["headers"] == {"Authorization": "Bearer test-key"}
    assert sent["json"] == request_body(turn, context)
    assert sent["timeout"] == 4.0
