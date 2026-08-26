"""A question goes in, a plan and an answer come out."""

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
    Operation,
    Plan,
)
from rockygpt_brain.brain.understand.schema import Reference, Understanding
from rockygpt_brain.brain.understand.validate import unresolved
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
    """BRAIN #3, faked. `supported` mirrors what a real one is asked to judge."""

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
        read: Understanding | None = None,
    ) -> None:
        self._plan = plan or Plan()
        self._read = read or Understanding(normalized="q", resolved="q")
        self._fails = fails
        self.seen: dict[str, Any] = {}
        self.planned_from: str | None = None

    async def understand(
        self,
        question: str,
        context: list[dict[str, Any]],
        current_time: str,
    ) -> Understanding:
        self.seen = {"question": question, "context": context, "currentTime": current_time}
        if self._fails:
            raise Unavailable("down")
        return self._read

    async def plan(self, resolved: str, current_time: str) -> Plan:
        self.planned_from = resolved
        return self._plan


class FakeData:
    """Every lookup, answering nothing.

    These tests are about the lifecycle, not about any one capability, so each
    method returns an empty list. `__getattr__` covers the registry as it grows:
    a capability added tomorrow needs no edit here to be exercised.
    """

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


async def ask(
    message: str = "anything",
    memory: MemoryStore | None = None,
    rid: str = "r",
    planner: FakePlanner | None = None,
) -> tuple[ChatSuccess, FakeModel]:
    model = FakeModel()
    # One fake satisfies both ports: it answers understand and plan alike.
    brains = planner or FakePlanner()
    brain = Brain(model, brains, brains, FakeData(), FakeWeb(), FakeRag(), memory or MemoryStore())
    response = await brain.answer(
        ChatRequest(message=message, now=NOW), TurnIdentity(rid, "s", None, "client")
    )
    return response, model


# The answer half, unchanged


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
    """Nothing was resolved, so `normalized` and `resolved` match."""
    plan = Plan(
        freshness="stable",
    )
    response, _ = await ask("what is the capital of France", planner=FakePlanner(plan))
    assert response.brain_trace.context == {}


async def test_a_reworded_question_is_not_a_borrowed_one() -> None:
    """BRAIN #1 said the question needed no conversation, so there is no stage."""
    plan = Plan(
        freshness="stable",
    )
    response, _ = await ask("are you srueeee?", planner=FakePlanner(plan))
    assert response.brain_trace.context == {}, "a corrected spelling is not context"


async def test_the_context_stage_breaks_down_how_the_question_was_read() -> None:
    read = Understanding(
        normalized="population of it",
        references=[Reference(text="it", refers_to="Paris")],
        used_turns=[0],
        uses_context=True,
        resolved="population of Paris",
    )
    memory = MemoryStore()
    await ask("Capital of france", memory, "r1")
    planner = FakePlanner(Plan(freshness="stable"), read=read)
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
    """A miscounted index is a wrong annotation, not a wrong answer."""
    read = Understanding(
        normalized="population of it",
        references=[Reference(text="it", refers_to="Paris")],
        used_turns=[7],
        uses_context=True,
        resolved="population of Paris",
    )
    response, _ = await ask("population of it", planner=FakePlanner(read=read))
    assert response.brain_trace.context["contextUsed"] == []


async def test_an_empty_history_is_taken_at_its_word() -> None:
    """A client that says the conversation is empty is not overruled."""
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
    """Omitting the field means "I do not track this", so the brain fills in."""
    memory = MemoryStore()
    await ask("first", memory, "r1")
    _, model = await ask("second", memory, "r2")
    assert model.seen["context"], "a client with no history of its own gets ours"


async def test_both_paths_see_the_same_distance_back() -> None:
    """A client that sends history and one that omits it get the same depth."""
    memory = MemoryStore()
    for turn in range(HISTORY_EXCHANGES + 4):
        await ask(f"q{turn}", memory, f"r{turn}")

    _, model = await ask("follow up", memory, "last")
    assert len(model.seen["context"]) == HISTORY_EXCHANGES, "the memory fallback"

    sent = [ChatTurn(role="user", content="x")] * (HISTORY_EXCHANGES * 2)
    ChatRequest(message="m", history=sent)  # the contract accepts the same depth


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


# The planning half


async def test_the_planner_is_told_the_same_question_and_time() -> None:
    planner = FakePlanner()
    await ask("a question", planner=planner)
    assert planner.seen["question"] == "a question"
    assert planner.seen["currentTime"] == NOW.astimezone(TZ).isoformat()


async def test_the_plan_is_its_own_stage() -> None:
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
        "filters": {"date": "2031-03-06"},
        "operation": {"orderBy": "departureTime", "direction": "descending", "limit": 1},
    }


async def test_the_turn_reads_end_to_end_as_four_stages() -> None:
    response, _ = await ask("a question")
    trace = response.brain_trace
    assert trace.question == {"question": "a question"}, "the words, and nothing else"
    assert trace.memory == {"currentTime": CLOCK, "earlierTurns": []}
    assert trace.plan == {
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
    """Every turn hands BRAIN #3 an instruction, even when nothing was looked up."""
    _, model = await ask()
    assert model.seen["grounding"] == {"answerFrom": "ownKnowledge"}


async def test_brain_two_never_runs_on_a_failed_lookup() -> None:
    """No stage compensates for the one before it, so there is nothing to write."""
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
    """Every lane runs now, so what is left unbuilt is a capability.

    The registry lists only what has code, so an unknown name is refused while
    the plan is still being checked — a stage earlier than it used to fail, and
    retryable because the next plan may name something Rocky can do.
    """
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
    """The registry refusing a plan is a failure, not a cue to answer anyway."""
    with pytest.raises(ServiceError) as raised:
        await ask(planner=FakePlanner(Plan(a_capability_answers_it=True, capability="weather")))
    assert "weather" in str(raised.value.__cause__)


async def test_a_planner_outage_ends_the_turn() -> None:
    """Everything downstream is read against the plan, so there is nothing to run."""
    with pytest.raises(ServiceError):
        await ask(planner=FakePlanner(fails=True))


def _web(*rows: tuple[str, str]) -> Execution:
    return Execution(WEB, results=[{"fact": f, "source": u} for f, u in rows])


def test_a_web_answer_carries_the_pages_it_came_from() -> None:
    """The point of the lane: an answer off the open web is checkable."""
    found = _citations(_web(("Paris has 2.1m residents.", "https://www.insee.fr/en/stats/1")), NOW)
    assert len(found) == 1
    assert found[0].title == "insee.fr"  # the host, and `www.` is not part of it
    assert str(found[0].url) == "https://www.insee.fr/en/stats/1"
    assert found[0].snippet == "Paris has 2.1m residents."


def test_only_the_web_lane_cites() -> None:
    """Campus rows are Rocky's own records — there is no page to point a reader at."""
    rows = [{"departureTime": "2:55 PM"}]
    assert _citations(Execution(CAMPUS_DATA, results=rows), NOW) == []
    assert _citations(Execution(OWN_KNOWLEDGE, note="stable"), NOW) == []


def test_two_facts_from_one_page_cite_it_once() -> None:
    """The client dedupes on `title|url`; deduping here keeps the two in step."""
    found = _citations(
        _web(("First.", "https://insee.fr/a"), ("Second.", "https://insee.fr/a")), NOW
    )
    assert len(found) == 1


def test_a_row_that_cannot_be_cited_is_dropped_not_raised_on() -> None:
    """The answer is already written. Losing it to a bad URL is the worse outcome."""
    found = _citations(
        _web(
            ("No URL at all.", ""),
            ("Not a URL.", "how about no"),
            ("Fine.", "https://insee.fr/ok"),
        ),
        NOW,
    )
    assert [c.title for c in found] == ["insee.fr"]


def _read(normalized: str, resolved: str, refs: list[tuple[str, str]]) -> Understanding:
    return Understanding(
        normalized=normalized,
        resolved=resolved,
        uses_context=True,
        references=[Reference(text=t, refers_to=r) for t, r in refs],
    )


def test_a_resolution_that_carried_the_referent_through_is_planned_from() -> None:
    assert not unresolved(_read("Population of it", "Population of Paris?", [("it", "Paris")]))


def test_a_referent_reworded_on_the_way_in_still_counts() -> None:
    """BRAIN #1 keeps the word and appends the date. That is a resolution, not a failure."""
    assert not unresolved(
        _read(
            "What about tomorrow",
            "What is the first shuttle for tomorrow, 2026-08-26?",
            [("tomorrow", "the day after today, 2026-08-26")],
        )
    )


def test_a_question_that_needed_context_and_came_back_unchanged_is_refused() -> None:
    assert unresolved(_read("Population of it", "Population of it", [("it", "Paris")]))


def test_a_referent_that_never_reached_the_question_is_refused() -> None:
    """The Italy case: `it` was found, and then dropped on the way in."""
    assert unresolved(
        _read("Population of it", "Population of the capital of France", [("it", "Paris")])
    )


def test_a_self_contained_question_is_never_second_guessed() -> None:
    """`usesContext` false means there was nothing to carry, so there is nothing to check."""
    read = Understanding(normalized="Capital of France?", resolved="Capital of France?")
    assert not unresolved(read)


def test_retryability_is_a_property_of_the_cause_not_a_choice() -> None:
    """The field a client acts on, and the one that used to be set by hand.

    Told to retry a missing key or a spent account, a client retries forever
    against something no attempt fixes. Told not to retry a model that
    hiccuped, it abandons a turn that would have worked. Neither is a decision
    a raise site should be able to get wrong.
    """
    assert Unavailable("x").retryable, "a passing fault is worth another try"
    assert DatasetUnavailable("x").retryable, "campus data comes back"
    assert not Unsupported("x").retryable, "waiting does not build a capability"
    assert not BadRequest("x").retryable
    assert not Unauthorized("x").retryable
    assert not Internal("x").retryable


def test_no_raise_site_can_state_its_own_retryability() -> None:
    """The constructor takes a message. There is no argument to get wrong."""
    with pytest.raises(TypeError):
        Unavailable("x", retryable=False)  # type: ignore[call-arg]


def test_a_spent_account_is_not_advertised_as_retryable() -> None:
    """The failure that hid behind "temporarily unavailable" for three probes."""

    class Spent(Exception):
        code = "credit_balance_exhausted"

    assert _is_exhausted(Spent())
    assert _is_exhausted(Exception("Error code: 429 - insufficient_quota"))
    assert not _is_exhausted(Exception("connection reset by peer"))


def _logs(memory: MemoryStore) -> list[Any]:
    return memory.list_logs(search=None, routes=set(), origins=set(), limit=50).logs


async def test_a_turn_that_failed_still_reaches_the_log() -> None:
    """The bug this guards: the log showed only successes and looked complete.

    A turn that raised was recorded nowhere, so an admin reading the log saw a
    clean run and no sign of the questions Rocky could not answer.
    """
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
    """It goes in the log, not the history.

    `history` feeds BRAIN #1, which resolves follow-ups against what was said.
    An error message is not something a later question can refer back to, and
    offering it as context is worse than the gap it leaves.
    """
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
    """A turn that reached a plan records it; the stage that failed is visible."""
    memory = MemoryStore()
    unbuilt = FakePlanner(Plan(a_capability_answers_it=True, capability="menu"))
    brain = Brain(FakeModel(), unbuilt, unbuilt, FakeData(), FakeWeb(), FakeRag(), memory)

    with pytest.raises(ServiceError):
        await brain.answer(
            ChatRequest(message="anything", now=NOW),
            TurnIdentity("r1", "s", None, "client"),
        )

    logged = _logs(memory)[0]
    assert logged.tool_arguments == {}, "the plan was refused, so none was recorded"
    assert "capability" in logged.debug_info["result"]["failed"]


async def test_a_successful_turn_is_recorded_exactly_once() -> None:
    """The `finally` must not double-write what the success path already wrote."""
    memory = MemoryStore()
    await ask("hello", memory)
    assert len(_logs(memory)) == 1
    assert len(memory.history("s")) == 1


async def _documents(model: FakeModel) -> ChatSuccess:
    """A turn that reaches the RAG lane and retrieves something."""
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
    """The failure this exists to stop: a policy invented in the voice of a document.

    Retrieval running is not evidence. When what came back does not answer the
    question, the honest reply is that it does not — written by Python, because
    a model that has just judged its evidence thin still writes something.
    """
    response = await _documents(FakeModel(sufficient_evidence=False))
    assert response.answer == INSUFFICIENT_EVIDENCE
    assert response.brain_trace.answer["sufficientEvidence"] is False


async def test_an_abstention_cites_nothing() -> None:
    """A citation on an abstention points a reader at a page that does not say it."""
    response = await _documents(FakeModel(sufficient_evidence=False))
    assert response.citations == []


async def test_supported_passages_are_answered_and_cited_as_normal() -> None:
    response = await _documents(FakeModel(sufficient_evidence=True))
    assert response.answer == "written"
    assert [str(c.url) for c in response.citations] == ["https://x.edu/a"]


async def test_only_the_documents_lane_is_held_to_this_yet() -> None:
    """campusData and web could follow, but each needs its own measurement first."""
    model = FakeModel(sufficient_evidence=False)
    brains = FakePlanner(Plan())
    brain = Brain(model, brains, brains, FakeData(), FakeWeb(), FakeRag(), MemoryStore())
    response = await brain.answer(
        ChatRequest(message="anything", now=NOW), TurnIdentity("r1", "s", None, "client")
    )
    assert response.answer == "written", "the general lane is unchanged for now"
