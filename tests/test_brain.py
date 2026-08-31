from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from rockygpt_brain.api.contracts import (
    HISTORY_EXCHANGES,
    ChatRequest,
    ChatSuccess,
    ChatTurn,
)
from rockygpt_brain.brain.brain import (
    RAG_WORK_IN_PROGRESS,
    Brain,
    PlanRejected,
    TurnIdentity,
    _citations,
)
from rockygpt_brain.brain.execute.schema import (
    CAMPUS_DATA,
    INSUFFICIENT_EVIDENCE,
    OWN_KNOWLEDGE,
    WEB,
    Execution,
)
from rockygpt_brain.brain.plan.schema import (
    Filter,
    Lane,
    Operation,
    Plan,
)
from rockygpt_brain.brain.resolve.schema import Filled, Resolution
from rockygpt_brain.brain.resolve.validate import contaminated
from rockygpt_brain.brain.understand.schema import Reading, Unresolved
from rockygpt_brain.brain.understand.validate import incoherent, narrowed
from rockygpt_brain.brain.write.schema import Draft
from rockygpt_brain.context.memory import MemoryStore
from rockygpt_brain.errors import (
    BadRequest,
    DatasetUnavailable,
    Internal,
    ServiceError,
    Unauthorized,
    Unavailable,
    Unsupported,
)
from rockygpt_brain.services.openai import _is_exhausted
from rockygpt_brain.services.rag.client import Passage

TZ = ZoneInfo("America/New_York")
NOW = datetime(2031, 3, 6, 18, 30, tzinfo=UTC)
CLOCK = NOW.astimezone(TZ).isoformat()


class FakeModel:
    configured = True

    def __init__(self, sufficient_evidence: bool = True) -> None:
        self.seen: dict[str, Any] = {}
        self.sufficient_evidence = sufficient_evidence

    async def answer(
        self,
        question: str,
        context: list[dict[str, Any]],
        current_time: str,
        style_mode: str | None,
        response_mode: str | None,
        grounding: dict[str, Any],
    ) -> Draft:
        self.seen = {
            "question": question,
            "context": context,
            "currentTime": current_time,
            "grounding": grounding,
        }
        return Draft(
            sufficient_evidence=self.sufficient_evidence,
            answer="written",
            suggested_questions=["a"],
        )


class FakePlanner:
    configured = True

    def __init__(
        self,
        plan: Plan | None = None,
        fails: bool = False,
        read: Reading | None = None,
        resolution: Resolution | None = None,
    ) -> None:
        self._plan = plan or Plan()
        self._read = read or Reading(normalized="q")
        self._resolution = resolution
        self._fails = fails
        self.seen: dict[str, Any] = {}
        self.planned_from: str | None = None
        self.understand_calls = 0
        # The conversation only reaches the second reading, so counting it is
        # how a test says "history never touched this question".
        self.resolve_calls = 0
        self.resolved_spans: list[str] | None = None

    async def understand(self, question: str, current_time: str) -> Reading:
        self.seen = {"question": question, "currentTime": current_time}
        self.understand_calls += 1
        if self._fails:
            raise Unavailable("down")
        return self._read

    async def resolve(
        self,
        question: str,
        spans: list[str],
        context: list[dict[str, Any]],
        current_time: str,
    ) -> Resolution:
        self.resolve_calls += 1
        self.resolved_spans = list(spans)
        self.seen = {**self.seen, "context": context}
        return self._resolution or Resolution(resolved=question)

    async def plan(self, resolved: str, current_time: str) -> Plan:
        self.planned_from = resolved
        return self._plan


class FakeData:
    def __getattr__(self, capability: str) -> Any:
        async def looked_up(query: dict[str, Any]) -> list[dict[str, Any]]:
            return []

        return looked_up


class FakeRag:
    def __init__(self, passages: list[Passage] | None = None) -> None:
        self._passages = passages or []
        self.asked: str | None = None

    async def retrieve(self, topic: str, limit: int) -> list[Passage]:
        self.asked = topic
        return self._passages[:limit]


class FakeWeb:
    configured = True

    def __init__(self, results: list[dict[str, Any]] | None = None) -> None:
        self.results = results or []
        self.searched: str | None = None

    async def search(self, query: str) -> list[dict[str, Any]]:
        self.searched = query
        return self.results


MENU = "What is on the menu today for breakfast and lunch?"


async def ask(
    message: str = "anything",
    memory: MemoryStore | None = None,
    rid: str = "r",
    planner: FakePlanner | None = None,
    data: Any | None = None,
) -> tuple[ChatSuccess, FakeModel]:
    model = FakeModel()
    brains = planner or FakePlanner()
    brain = Brain(
        model,
        brains,
        brains,
        data or FakeData(),
        FakeWeb(),
        FakeRag(),
        memory or MemoryStore(),
    )
    response = await brain.answer(
        ChatRequest(message=message, now=NOW), TurnIdentity(rid, "s", None, "client")
    )
    return response, model


async def test_the_question_reaches_the_model_unchanged() -> None:
    _, model = await ask("a question")
    assert model.seen["question"] == "a question"


async def test_the_answer_comes_back_unchanged() -> None:
    response, _ = await ask()
    assert response.answer == "written"


async def test_the_model_is_told_the_local_time() -> None:
    _, model = await ask()
    assert model.seen["currentTime"] == NOW.astimezone(TZ).isoformat()


async def test_nothing_is_cited_because_nothing_is_looked_up() -> None:
    response, _ = await ask()
    assert response.citations == []
    assert response.ui_actions == []


async def test_a_follow_up_sees_the_earlier_turn() -> None:
    memory = MemoryStore()
    await ask("first", memory, "r1")
    response, model = await ask("second", memory, "r2")
    assert model.seen["context"], "the second turn sees the first"
    assert response.brain_trace.memory["earlierTurns"] == model.seen["context"], (
        "what the trace shows is what the model was given"
    )


async def test_a_question_that_stands_alone_has_no_context_stage() -> None:
    plan = Plan(
        freshness="stable",
    )
    response, _ = await ask("what is the capital of France", planner=FakePlanner(plan))
    assert response.brain_trace.context == {}


async def test_a_reworded_question_is_not_a_borrowed_one() -> None:
    plan = Plan(
        freshness="stable",
    )
    response, _ = await ask("are you srueeee?", planner=FakePlanner(plan))
    assert response.brain_trace.context == {}, "a corrected spelling is not context"


async def test_the_context_stage_breaks_down_how_the_question_was_read() -> None:
    memory = MemoryStore()
    await ask("Capital of france", memory, "r1")
    planner = FakePlanner(
        Plan(freshness="stable"),
        read=Reading(
            normalized="population of it",
            unresolved=[Unresolved(text="it")],
            needs_context=True,
        ),
        resolution=Resolution(
            references=[Filled(text="it", refers_to="Paris")],
            used_turns=[0],
            resolved="population of Paris",
        ),
    )
    response, _ = await ask("population of it", memory, "r2", planner=planner)
    context = response.brain_trace.context
    assert context["references"] == [{"text": "it", "refersTo": "Paris"}]
    assert len(context["contextUsed"]) == 1, "looked up from the turn it named"
    read_out = response.brain_trace.understanding
    assert read_out["resolvedQuestion"] == "population of Paris"
    assert read_out["usesContext"] is True
    assert planner.planned_from == "population of Paris", (
        "the plan is built from the resolved question, never the words typed"
    )


async def test_a_turn_position_that_does_not_exist_is_dropped() -> None:
    planner = FakePlanner(
        read=Reading(
            normalized="population of it",
            unresolved=[Unresolved(text="it")],
            needs_context=True,
        ),
        resolution=Resolution(
            references=[Filled(text="it", refers_to="Paris")],
            used_turns=[7],
            resolved="population of Paris",
        ),
    )
    response, _ = await ask("population of it", planner=planner)
    assert response.brain_trace.context["contextUsed"] == []


async def test_an_empty_history_is_taken_at_its_word() -> None:
    memory = MemoryStore()
    await ask("first", memory, "r1")

    model = FakeModel()
    brain = Brain(model, FakePlanner(), FakePlanner(), FakeData(), FakeWeb(), FakeRag(), memory)
    response = await brain.answer(
        ChatRequest(message="second", history=[], now=NOW),
        TurnIdentity("r2", "s", None, "client"),
    )
    assert model.seen["context"] == [], "the earlier turn is not resurrected"
    assert response.brain_trace.memory["earlierTurns"] == []


async def test_a_client_that_sends_no_history_still_gets_the_sessions() -> None:
    memory = MemoryStore()
    await ask("first", memory, "r1")
    _, model = await ask("second", memory, "r2")
    assert model.seen["context"], "a client with no history of its own gets ours"


async def test_both_paths_see_the_same_distance_back() -> None:
    memory = MemoryStore()
    for turn in range(HISTORY_EXCHANGES + 4):
        await ask(f"q{turn}", memory, f"r{turn}")

    _, model = await ask("follow up", memory, "last")
    assert len(model.seen["context"]) == HISTORY_EXCHANGES, "the memory fallback"

    sent = [ChatTurn(role="user", content="x")] * (HISTORY_EXCHANGES * 2)
    ChatRequest(message="m", history=sent)


async def test_the_modes_the_ui_asked_for_are_on_the_turn() -> None:
    model = FakeModel()
    brain = Brain(
        model, FakePlanner(), FakePlanner(), FakeData(), FakeWeb(), FakeRag(), MemoryStore()
    )
    response = await brain.answer(
        ChatRequest(message="m", now=NOW, style_mode="warm", response_mode="concise"),
        TurnIdentity("r", "s", None, "client"),
    )
    assert response.brain_trace.memory["styleMode"] == "warm"
    assert response.brain_trace.memory["responseMode"] == "concise"


async def test_the_planner_is_told_the_same_question_and_time() -> None:
    planner = FakePlanner()
    await ask("a question", planner=planner)
    assert planner.seen["question"] == "a question"
    assert planner.seen["currentTime"] == NOW.astimezone(TZ).isoformat()


async def test_the_plan_is_its_own_stage() -> None:
    """What BRAIN #2 asked for and what Python honoured are both readable.

    The drafted plan carries the count of one the planner reached for; the
    normalized plan is what actually ran, without it. A limit Python declined
    to apply is visible as the difference between the two boxes rather than
    disappearing from the trace.
    """
    plan = Plan(
        a_capability_answers_it=True,
        capability="shuttle",
        filters=[Filter(field="date", value="today")],
        operation=Operation(order_by="departureTime", direction="descending", limit=1),
    )
    response, _ = await ask(planner=FakePlanner(plan))
    assert response.brain_trace.plan == {
        "routing": {"CODE?": "Yes", "RAMAPO?": "—", "ROUTE": "CODE"},
        "capability": "shuttle",
        "filters": {"date": "today"},
        "operation": {"orderBy": "departureTime", "direction": "descending", "limit": 1},
    }
    assert response.brain_trace.normalized_plan == {
        "routing": {"CODE?": "Yes", "RAMAPO?": "—", "ROUTE": "CODE"},
        "capability": "shuttle",
        "filters": {"date": "2031-03-06"},
        "operation": {"orderBy": "departureTime", "direction": "descending"},
    }


async def test_entity_mentions_become_ids_only_in_the_normalized_plan() -> None:
    plan = Plan(
        a_capability_answers_it=True,
        capability="calendar",
        filters=[
            Filter(field="family", value="registration"),
            Filter(field="term", value="Fall 2031"),
        ],
        operation=Operation(compare=["date", "title"]),
    )

    class CalendarData(FakeData):
        async def calendar(self, query: dict[str, Any]) -> list[dict[str, Any]]:
            return [
                {
                    "family": "registration",
                    "kind": "add_drop_deadline",
                    "term": "Fall 2031",
                    "termId": "fall-2031",
                    "date": "Sep. 1",
                    "startsAt": "2031-09-01T04:00:00Z",
                    "title": "Last Day to Add/Drop",
                }
            ]

    response, _ = await ask(planner=FakePlanner(plan), data=CalendarData())
    assert response.brain_trace.plan["filters"] == {
        "family": "registration",
        "term": "Fall 2031",
    }
    assert response.brain_trace.normalized_plan["filters"] == {
        "family": "registration",
        "termId": "fall-2031",
    }


async def test_the_turn_exposes_semantic_and_normalized_plans_as_separate_stages() -> None:
    response, _ = await ask("a question")
    trace = response.brain_trace
    assert trace.question == {"question": "a question"}, "the words, and nothing else"
    assert trace.memory == {"currentTime": CLOCK, "earlierTurns": []}
    assert trace.plan == {
        "routing": {"CODE?": "No", "RAMAPO?": "No", "ROUTE": "GENERAL"},
    }
    assert trace.normalized_plan == {
        "routing": {"CODE?": "No", "RAMAPO?": "No", "ROUTE": "GENERAL"},
        "freshness": "stable",
    }
    assert trace.context == {}, "BRAIN #1 said the question needed no conversation"
    assert trace.understanding == {
        "normalizedQuestion": "q",
        "usesContext": False,
        "resolvedQuestion": "q",
    }
    assert trace.execution == {
        "answerFrom": "ownKnowledge",
        "note": "stable; answered from what the model knows",
    }
    assert trace.answer == {"answer": "written", "sufficientEvidence": True}


async def test_brain_two_is_grounded_on_every_lane() -> None:
    _, model = await ask()
    assert model.seen["grounding"] == {"answerFrom": "ownKnowledge"}


async def test_brain_two_never_runs_on_a_failed_lookup() -> None:
    model = FakeModel()
    unbuilt = FakePlanner(Plan(a_capability_answers_it=True, capability="menu"))
    brain = Brain(
        model,
        unbuilt,
        unbuilt,
        FakeData(),
        FakeWeb(),
        FakeRag(),
        MemoryStore(),
    )
    with pytest.raises(ServiceError):
        await brain.answer(
            ChatRequest(message="m", now=NOW), TurnIdentity("r", "s", None, "client")
        )
    assert model.seen == {}, "BRAIN #3 was never called"


async def test_a_capability_with_no_code_is_caught_before_anything_runs() -> None:
    with pytest.raises(ServiceError) as raised:
        await ask(planner=FakePlanner(Plan(a_capability_answers_it=True, capability="menu")))
    assert raised.value.status_code == 503
    assert isinstance(raised.value.__cause__, PlanRejected)
    assert "menu" in str(raised.value.__cause__)


async def test_the_route_is_the_lane() -> None:
    plan = Plan(a_capability_answers_it=True, capability="shuttle", operation=Operation(limit=1))
    response, _ = await ask(planner=FakePlanner(plan))
    assert response.route == "code"


async def test_a_rejected_plan_ends_the_turn() -> None:
    with pytest.raises(ServiceError) as raised:
        await ask(planner=FakePlanner(Plan(a_capability_answers_it=True, capability="weather")))
    assert "weather" in str(raised.value.__cause__)


async def test_a_planner_outage_ends_the_turn() -> None:
    with pytest.raises(ServiceError):
        await ask(planner=FakePlanner(fails=True))


def _web(*rows: tuple[str, str]) -> Execution:
    return Execution(WEB, results=[{"fact": f, "source": u} for f, u in rows])


def test_a_web_answer_carries_the_pages_it_came_from() -> None:
    found = _citations(_web(("Paris has 2.1m residents.", "https://www.insee.fr/en/stats/1")), NOW)
    assert len(found) == 1
    assert found[0].title == "insee.fr"
    assert str(found[0].url) == "https://www.insee.fr/en/stats/1"
    assert found[0].snippet == "Paris has 2.1m residents."


def test_only_the_web_lane_cites() -> None:
    rows = [{"departureTime": "2:55 PM"}]
    assert _citations(Execution(CAMPUS_DATA, results=rows), NOW) == []
    assert _citations(Execution(OWN_KNOWLEDGE, note="stable"), NOW) == []


def test_two_facts_from_one_page_cite_it_once() -> None:
    found = _citations(
        _web(("First.", "https://insee.fr/a"), ("Second.", "https://insee.fr/a")), NOW
    )
    assert len(found) == 1


def test_a_row_that_cannot_be_cited_is_dropped_not_raised_on() -> None:
    found = _citations(
        _web(
            ("No URL at all.", ""),
            ("Not a URL.", "how about no"),
            ("Fine.", "https://insee.fr/ok"),
        ),
        NOW,
    )
    assert [c.title for c in found] == ["insee.fr"]


# What an earlier turn said, for the checks about what may cross from it.
EARLIER = [
    {"user": "What is on the menu today for breakfast and lunch?", "assistant": "Lunch is served."}
]


def test_a_reading_that_needs_the_conversation_must_name_what_for() -> None:
    assert incoherent(Reading(normalized="Where is it?", needs_context=True))


def test_a_reading_that_names_a_gap_cannot_also_stand_alone() -> None:
    assert incoherent(
        Reading(normalized="Where is it?", unresolved=[Unresolved(text="it")], needs_context=False)
    )


def test_the_two_agreeing_is_what_a_coherent_reading_looks_like() -> None:
    assert not incoherent(Reading(normalized="Capital of France?"))
    assert not incoherent(
        Reading(normalized="Where is it?", unresolved=[Unresolved(text="it")], needs_context=True)
    )


def test_a_clock_word_is_not_a_gap_the_conversation_fills() -> None:
    """The rewrite this prevents: "tonight" resolved to an earlier dinner answer.

    A question about events kept its subject only because the planner ignored
    the resolution. The span was never a gap — the hour is known without asking
    anyone — so it is dropped and the question freezes where it stands.
    """
    read = narrowed(
        Reading(
            normalized="What events are happening tonight?",
            unresolved=[Unresolved(text="tonight")],
            needs_context=True,
        )
    )
    assert read.unresolved == []
    assert read.needs_context is False
    assert not incoherent(read)


def test_a_real_gap_beside_a_clock_word_survives() -> None:
    read = narrowed(
        Reading(
            normalized="What about the one after that tonight?",
            unresolved=[Unresolved(text="tonight"), Unresolved(text="the one after that")],
            needs_context=True,
        )
    )
    assert [span.text for span in read.unresolved] == ["the one after that"]
    assert read.needs_context is True


def test_a_span_the_question_does_not_contain_is_refused() -> None:
    problem = incoherent(
        Reading(
            normalized="When is the next shuttle?",
            unresolved=[Unresolved(text="the one after that")],
            needs_context=True,
        )
    )
    assert "not a span of the question" in problem


def test_a_resolution_that_fills_only_its_span_is_kept() -> None:
    assert not contaminated(
        "What about the one after that?",
        ["the one after that"],
        Resolution(
            references=[Filled(text="the one after that", refers_to="the shuttle after 11:00 AM")],
            resolved="When does the shuttle after 11:00 AM depart?",
        ),
        EARLIER,
    )


def test_a_resolution_that_drops_what_the_question_stated_is_refused() -> None:
    problem = contaminated(
        "What about breakfast and dinner there?",
        ["there"],
        Resolution(
            references=[Filled(text="there", refers_to="Birch")],
            resolved="What is on the menu for breakfast at Birch?",
        ),
        EARLIER,
    )
    assert "dinner" in problem


def test_a_meal_only_the_conversation_named_may_not_join_the_question() -> None:
    """The contamination this design exists to make impossible.

    Asked about breakfast and dinner after a turn about breakfast and lunch,
    the resolution came back naming all three, and the lookup answered a
    question nobody asked.
    """
    problem = contaminated(
        "What about breakfast and dinner there?",
        ["there"],
        Resolution(
            references=[Filled(text="there", refers_to="Birch")],
            resolved="What is on the menu for breakfast, lunch and dinner at Birch?",
        ),
        EARLIER,
    )
    assert "lunch" in problem


async def test_a_self_contained_question_never_reaches_the_conversation() -> None:
    """The invariant, end to end: history cannot reach a question that stands alone.

    Not because the reading decided to ignore it — because the reading that
    decided the meaning was never shown it, and the second reading did not run.
    """
    memory = MemoryStore()
    await ask("What is on the menu today for breakfast and lunch?", memory, "r1")

    brains = FakePlanner(read=Reading(normalized=MENU))
    _, _ = await ask(MENU, memory, "r2", planner=brains)
    assert brains.understand_calls == 1
    assert brains.resolve_calls == 0, "the conversation was never opened"
    assert brains.planned_from == MENU, "planned from the question exactly as asked"


async def test_a_pointing_question_is_filled_from_the_conversation() -> None:
    brains = FakePlanner(
        read=Reading(
            normalized="What about that one?",
            unresolved=[Unresolved(text="that one")],
            needs_context=True,
        ),
        resolution=Resolution(
            references=[Filled(text="that one", refers_to="the Route 17 shuttle")],
            used_turns=[0],
            resolved="When does the Route 17 shuttle leave?",
        ),
    )
    await ask("What about that one?", planner=brains)
    assert brains.resolve_calls == 1
    assert brains.resolved_spans == ["that one"]
    assert brains.planned_from == "When does the Route 17 shuttle leave?"


async def test_a_reading_that_contradicts_itself_is_refused_before_the_conversation() -> None:
    brains = FakePlanner(read=Reading(normalized="Where is it?", needs_context=True))
    with pytest.raises(Unavailable):
        await ask("Where is it?", planner=brains)
    assert brains.resolve_calls == 0, "nothing to fill, so nothing was opened"


async def test_a_resolution_that_imports_from_the_conversation_is_refused() -> None:
    memory = MemoryStore()
    await ask("What is on the menu today for breakfast and lunch?", memory, "r1")

    brains = FakePlanner(
        read=Reading(
            normalized="What about breakfast and dinner there?",
            unresolved=[Unresolved(text="there")],
            needs_context=True,
        ),
        resolution=Resolution(
            references=[Filled(text="there", refers_to="Birch")],
            resolved="What is on the menu for breakfast, lunch and dinner at Birch?",
        ),
    )
    with pytest.raises(Unavailable):
        await ask("What about breakfast and dinner there?", memory, "r2", planner=brains)


def test_retryability_is_a_property_of_the_cause_not_a_choice() -> None:
    assert Unavailable("x").retryable, "a passing fault is worth another try"
    assert DatasetUnavailable("x").retryable, "campus data comes back"
    assert not Unsupported("x").retryable, "waiting does not build a capability"
    assert not BadRequest("x").retryable
    assert not Unauthorized("x").retryable
    assert not Internal("x").retryable


def test_no_raise_site_can_state_its_own_retryability() -> None:
    with pytest.raises(TypeError):
        Unavailable("x", retryable=False)  # type: ignore[call-arg]


def test_a_spent_account_is_not_advertised_as_retryable() -> None:

    class Spent(Exception):
        code = "credit_balance_exhausted"

    assert _is_exhausted(Spent())
    assert _is_exhausted(Exception("Error code: 429 - insufficient_quota"))
    assert not _is_exhausted(Exception("connection reset by peer"))


def _logs(memory: MemoryStore) -> list[Any]:
    return memory.list_logs(search=None, routes=set(), origins=set(), limit=50).logs


async def test_a_turn_that_failed_still_reaches_the_log() -> None:
    memory = MemoryStore()
    model = FakeModel()
    unbuilt = FakePlanner(Plan(a_capability_answers_it=True, capability="menu"))
    brain = Brain(model, unbuilt, unbuilt, FakeData(), FakeWeb(), FakeRag(), memory)

    with pytest.raises(ServiceError):
        await brain.answer(
            ChatRequest(message="what is on the menu", now=NOW),
            TurnIdentity("r1", "s", None, "client"),
        )

    logged = _logs(memory)
    assert len(logged) == 1, "the failure was lost"
    assert logged[0].user_message == "what is on the menu"
    assert logged[0].debug_info["result"]["failed"], "the log does not say why"


async def test_a_failed_turn_is_not_offered_back_as_conversation() -> None:
    memory = MemoryStore()
    unbuilt = FakePlanner(Plan(a_capability_answers_it=True, capability="menu"))
    brain = Brain(FakeModel(), unbuilt, unbuilt, FakeData(), FakeWeb(), FakeRag(), memory)

    with pytest.raises(ServiceError):
        await brain.answer(
            ChatRequest(message="what is on the menu", now=NOW),
            TurnIdentity("r1", "s", None, "client"),
        )

    assert memory.history("s") == [], "an error was offered back as something said"
    assert len(_logs(memory)) == 1, "and it should still be logged"


async def test_a_failure_records_how_far_the_turn_got() -> None:
    memory = MemoryStore()
    unbuilt = FakePlanner(Plan(a_capability_answers_it=True, capability="menu"))
    brain = Brain(FakeModel(), unbuilt, unbuilt, FakeData(), FakeWeb(), FakeRag(), memory)

    with pytest.raises(ServiceError):
        await brain.answer(
            ChatRequest(message="anything", now=NOW),
            TurnIdentity("r1", "s", None, "client"),
        )

    logged = _logs(memory)[0]
    # The refused plan is recorded, not withheld. This asserted the opposite
    # until 2026-08-29 — "the plan was refused, so none was recorded" — which
    # left the one column that says what the model actually wrote blank on
    # exactly the turns someone reads the log to understand. The reason names
    # the rule that fired; only the plan beside it says what tripped the rule.
    assert logged.tool_arguments["capability"] == "menu"
    assert "capability" in logged.debug_info["result"]["failed"]


async def test_a_successful_turn_is_recorded_exactly_once() -> None:
    memory = MemoryStore()
    await ask("hello", memory)
    assert len(_logs(memory)) == 1
    assert len(memory.history("s")) == 1


async def _documents(model: FakeModel) -> ChatSuccess:
    brains = FakePlanner(Plan(specific_to_ramapo=True, topic="guest policy"))
    rag = FakeRag(
        [Passage("Guests must carry ID.", "housing", "Residence Life", "https://x.edu/a")]
    )
    brain = Brain(
        model,
        brains,
        brains,
        FakeData(),
        FakeWeb(),
        rag,
        MemoryStore(),
        rag_enabled=True,
    )
    return await brain.answer(
        ChatRequest(message="what is the guest policy", now=NOW),
        TurnIdentity("r1", "s", None, "client"),
    )


async def test_rag_is_gated_while_code_is_being_tested() -> None:
    model = FakeModel()
    brains = FakePlanner(Plan(specific_to_ramapo=True, topic="guest policy"))
    rag = FakeRag(
        [Passage("Guests must carry ID.", "housing", "Residence Life", "https://x.edu/a")]
    )
    brain = Brain(model, brains, brains, FakeData(), FakeWeb(), rag, MemoryStore())

    response = await brain.answer(
        ChatRequest(message="what is the guest policy", now=NOW),
        TurnIdentity("r1", "s", None, "client"),
    )

    assert response.route == "rag"
    assert response.answer == RAG_WORK_IN_PROGRESS
    assert response.citations == []
    assert response.suggested_questions == []
    assert response.brain_trace.execution == {
        "answerFrom": "ragDisabled",
        "note": "disabled while CODE is being tested",
    }
    assert rag.asked is None, "the disabled RAG lane retrieved documents"
    assert model.seen == {}, "BRAIN #3 was asked to write a disabled RAG answer"


async def test_passages_that_do_not_answer_the_question_produce_no_answer() -> None:
    response = await _documents(FakeModel(sufficient_evidence=False))
    assert response.answer == INSUFFICIENT_EVIDENCE
    assert response.brain_trace.answer["sufficientEvidence"] is False


async def test_an_abstention_cites_nothing() -> None:
    response = await _documents(FakeModel(sufficient_evidence=False))
    assert response.citations == []


async def test_supported_passages_are_answered_and_cited_as_normal() -> None:
    response = await _documents(FakeModel(sufficient_evidence=True))
    assert response.answer == "written"
    assert [str(c.url) for c in response.citations] == ["https://x.edu/a"]


async def test_only_the_documents_lane_is_held_to_this_yet() -> None:
    model = FakeModel(sufficient_evidence=False)
    brains = FakePlanner(Plan())
    brain = Brain(model, brains, brains, FakeData(), FakeWeb(), FakeRag(), MemoryStore())
    response = await brain.answer(
        ChatRequest(message="anything", now=NOW), TurnIdentity("r1", "s", None, "client")
    )
    assert response.answer == "written", "the general lane is unchanged for now"


async def test_a_narrowing_that_matched_nothing_is_not_reported_as_nothing_existing() -> None:
    narrowed = Plan(
        a_capability_answers_it=True,
        lane=Lane.CODE,
        capability="courses",
        filters=[Filter(field="subject", value="CS")],
        operation=Operation(limit=5),
    )
    response, _ = await ask(planner=FakePlanner(plan=narrowed))
    assert "no computer science" not in response.answer.lower()
    assert "subject" in response.answer and "CS" in response.answer, "it names what was searched"
    assert "not the same as there being none" in response.answer


async def test_an_unnarrowed_lookup_that_found_nothing_is_still_the_model_to_answer() -> None:
    whole = Plan(
        a_capability_answers_it=True,
        lane=Lane.CODE,
        capability="courses",
        operation=Operation(limit=5),
    )
    response, _ = await ask(planner=FakePlanner(plan=whole))
    assert response.answer == "written", "BRAIN #3 still writes it"
