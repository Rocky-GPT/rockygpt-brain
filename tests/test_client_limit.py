"""The student site's per-student limit, counted once in the Brain for every copy of the site."""

from fastapi.testclient import TestClient

from rockygpt_brain.api.app import create_app
from rockygpt_brain.api.client_limit import CLIENT_LIMIT, NETWORK_LIMIT, ClientLimit
from rockygpt_brain.boundary import SAFETY_TEXTS
from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.engine import ChatEngine, ChatResult, answered
from rockygpt_brain.turn import Turn

QUESTION = {"messages": [{"role": "user", "content": "What time is it?"}]}

TAB, OTHER_TAB, NETWORK = "a" * 64, "b" * 64, "c" * 64


class CountingEngine(ChatEngine):
    def __init__(self) -> None:
        self.gateway = object()  # type: ignore[assignment]
        self.turn_seconds = 1.0
        self.output = "text"
        self.calls = 0

    async def readiness(self) -> bool:
        return True

    async def answer(self, turn: Turn, request: ChatRequest, *,
                     floor_situation: str | None = None) -> ChatResult:
        self.calls += 1
        return answered(turn, "A controlled test answer.", "answered")


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def test_a_tab_gets_twelve_a_minute_and_a_network_address_one_hundred_twenty() -> None:
    clock = Clock()
    limit = ClientLimit(clock)
    assert all(limit.take(TAB, NETWORK) is None for _ in range(CLIENT_LIMIT))
    assert limit.take(TAB, NETWORK) == 60
    assert limit.take(OTHER_TAB, NETWORK) is None  # another student on the same network
    clock.now += 45
    assert limit.take(TAB, NETWORK) == 15
    clock.now += 15
    assert limit.take(TAB, NETWORK) is None  # a new minute
    # Made-up tab values still stop at the address's ceiling.
    fresh = ClientLimit(clock)
    tabs = [f"{n:064x}" for n in range(NETWORK_LIMIT + 1)]
    assert [fresh.take(tab, NETWORK) for tab in tabs].count(None) == NETWORK_LIMIT


def test_no_key_or_a_key_the_site_could_not_have_made_is_not_counted() -> None:
    limit = ClientLimit(Clock())
    assert all(limit.take(None, None) is None for _ in range(100))
    assert all(limit.take("not-a-digest", "x") is None for _ in range(100))


def test_the_brain_refuses_past_the_limit_but_always_sends_the_emergency_text() -> None:
    engine = CountingEngine()
    headers = {"x-rockygpt-client-key": TAB}
    with TestClient(create_app(engine, service_token="", environment="development")) as client:
        for _ in range(CLIENT_LIMIT):
            assert client.post("/v1/chat", json=QUESTION, headers=headers).status_code == 200
        refused = client.post("/v1/chat", json=QUESTION, headers=headers)
        emergency = client.post("/v1/chat", headers=headers, json={"messages": [
            {"role": "user", "content": "my roommate just collapsed and isnt breathing"}]})
        other = client.post("/v1/chat", json=QUESTION, headers={"x-rockygpt-client-key": OTHER_TAB})
    assert refused.status_code == 429 and refused.json()["error"]["code"] == "rate_limited"
    assert refused.headers["retry-after"] == "60" and refused.json()["error"]["retryable"] is True
    assert engine.calls == CLIENT_LIMIT + 1  # the other tab; the emergency text needs no engine
    assert emergency.status_code == 200 and emergency.json()["answer"] == SAFETY_TEXTS["medical"]
    assert other.status_code == 200
