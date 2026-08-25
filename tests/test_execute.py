"""PYTHON runs the lane, and the generic operations are applied in Python."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

import pytest

from rockygpt_brain.brain.plan.schema import Filter, Lane, Operation, Plan
from rockygpt_brain.brain.plan.validate import check
from rockygpt_brain.core.execute import run
from rockygpt_brain.errors import ServiceError
from rockygpt_brain.safety.responses import CONCERNS
from rockygpt_brain.safety.schema import Concern
from rockygpt_brain.services.data import DataUnavailable
from rockygpt_brain.services.web import WebUnavailable

TZ = ZoneInfo("America/New_York")
NOW = datetime(2031, 3, 6, 18, 30, tzinfo=UTC).astimezone(TZ)


def trip(
    route: str, departs: str, arrives: str, destination: str = "Ramapo College"
) -> dict[str, Any]:
    return {
        "route": route,
        "departure": {"location": "Ramapo College", "time": departs},
        "arrival": {"location": destination, "time": arrives},
        "matchedOrigin": {"location": "Ramapo College", "time": departs},
        "matchedDestination": {"location": destination, "time": arrives},
        "evidenceIds": ["source:1"],
    }


TRIPS = [
    trip("Route 17", "7:00 AM", "7:20 AM"),
    trip("Mall", "1:15 PM", "2:00 PM", "Garden State Plaza"),
    trip("Route 17", "10:30 PM", "10:50 PM"),
]


class FakeData:
    def __init__(self, records: list[dict[str, Any]] | None = None, fails: bool = False) -> None:
        self._records = TRIPS if records is None else records
        self._fails = fails
        self.query: dict[str, Any] = {}

    async def shuttle(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        self.query = query
        if self._fails:
            raise DataUnavailable("connection refused")
        return self._records


class FakeWeb:
    configured = True

    def __init__(self, results: list[dict[str, Any]] | None = None, fails: bool = False) -> None:
        self._results = results if results is not None else [FACT]
        self._fails = fails
        self.searched: str | None = None

    async def search(self, query: str) -> list[dict[str, Any]]:
        self.searched = query
        if self._fails:
            raise WebUnavailable("no web search is configured")
        return self._results


FACT = {
    "fact": "Paris has 2,084,894 residents.",
    "source": "https://insee.fr/x",
    "publishedAt": "2026-01-01",
}


def shuttle(filters: dict[str, str] | None = None, **operation: Any) -> Plan:
    return Plan(
        lane=Lane.CODE,
        capability="shuttle",
        filters=[Filter(field=k, value=v) for k, v in (filters or {}).items()],
        operation=Operation(**operation),
    )


# The lookup happens


async def test_a_shuttle_plan_runs_and_says_so() -> None:
    execution = await run(shuttle({"date": "2031-03-06"}), NOW, FakeData(), FakeWeb())
    assert execution.ran is True
    assert len(execution.results) == 3


async def test_the_plans_filters_become_the_services_query() -> None:
    data = FakeData()
    plan = shuttle({"date": "2031-03-06", "destination": "Garden State Plaza"})
    await run(plan, NOW, data, FakeWeb())
    assert data.query["serviceDate"] == "2031-03-06"
    assert data.query["destination"] == "Garden State Plaza"


async def test_the_service_is_never_asked_to_choose() -> None:
    """`selection` stays "all" so its vocabulary never leaks back into a plan."""
    data = FakeData()
    plan = shuttle({"date": "2031-03-06"}, order_by="departureTime", limit=1)
    await run(plan, NOW, data, FakeWeb())
    assert data.query["selection"] == "all"


async def test_a_time_filter_asks_only_for_what_is_left() -> None:
    data = FakeData()
    await run(shuttle({"departingAfter": "2031-03-06T13:30:00-05:00"}), NOW, data, FakeWeb())
    assert data.query["timeScope"] == "remaining"
    assert data.query["asOf"] == "2031-03-06T13:30:00-05:00"


# The operations are applied in Python


async def test_descending_with_a_limit_of_one_is_the_last_trip() -> None:
    execution = await run(
        shuttle({}, order_by="departureTime", direction="descending", limit=1),
        NOW,
        FakeData(),
        FakeWeb(),
    )
    assert execution.results == [
        {
            "departureTime": "10:30 PM",
            "arrivalTime": "10:50 PM",
            "route": "Route 17",
            "origin": "Ramapo College",
            "destination": "Ramapo College",
        }
    ]


async def test_ascending_with_a_limit_of_one_is_the_first_trip() -> None:
    execution = await run(
        shuttle({}, order_by="departureTime", direction="ascending", limit=1),
        NOW,
        FakeData(),
        FakeWeb(),
    )
    assert execution.results[0]["departureTime"] == "7:00 AM"


async def test_times_sort_as_times_not_as_text() -> None:
    """`10:30 PM` sorts after `7:00 AM`, which string ordering gets wrong."""
    execution = await run(shuttle({}, order_by="departureTime"), NOW, FakeData(), FakeWeb())
    assert [row["departureTime"] for row in execution.results] == [
        "7:00 AM",
        "1:15 PM",
        "10:30 PM",
    ]


async def test_count_answers_with_how_many_matched() -> None:
    execution = await run(shuttle({}, count=True), NOW, FakeData(), FakeWeb())
    assert execution.count == 3
    assert execution.results == []


async def test_count_is_of_matches_not_of_what_a_limit_kept() -> None:
    execution = await run(shuttle({}, count=True, limit=1), NOW, FakeData(), FakeWeb())
    assert execution.count == 3


async def test_a_record_is_cut_down_to_the_fields_the_capability_publishes() -> None:
    execution = await run(shuttle({}, limit=1), NOW, FakeData(), FakeWeb())
    assert "evidenceIds" not in execution.results[0]


# What BRAIN #3 is given


async def test_what_ran_is_what_brain_two_answers_from() -> None:
    execution = await run(shuttle({}, limit=1), NOW, FakeData(), FakeWeb())
    assert execution.grounding() == {
        "answerFrom": "campusData",
        "results": execution.results,
    }


async def test_looking_and_finding_none_is_not_the_same_as_not_looking() -> None:
    """The one distinction the summary exists to draw."""
    found_none = await run(shuttle({}, limit=1), NOW, FakeData(records=[]), FakeWeb())
    never_looked = await run(Plan(lane=Lane.GENERAL), NOW, FakeData(), FakeWeb())

    assert found_none.summary() == {"answerFrom": "campusData", "results": []}, (
        "an empty list, not a missing one"
    )
    assert "results" not in never_looked.summary()

    assert found_none.grounding() == {"answerFrom": "campusData", "results": []}, (
        "the lookup ran and matched nothing"
    )
    assert never_looked.grounding() == {"answerFrom": "ownKnowledge"}


async def test_a_count_reports_the_count_and_not_an_empty_list() -> None:
    execution = await run(shuttle({}, count=True), NOW, FakeData(), FakeWeb())
    assert execution.summary() == {"answerFrom": "campusData", "count": 3}


async def test_general_is_not_reported_as_a_missing_executor() -> None:
    """It is the lane that means "no lookup", not one still to be built."""
    execution = await run(Plan(lane=Lane.GENERAL), NOW, FakeData(), FakeWeb())
    assert "no executor" not in execution.note
    assert execution.grounding() == {"answerFrom": "ownKnowledge"}


async def test_a_lane_still_to_be_built_ends_the_turn() -> None:
    with pytest.raises(ServiceError) as raised:
        await run(Plan(lane=Lane.RAG, topic="parking"), NOW, FakeData(), FakeWeb())
    assert "RAG" in str(raised.value.__cause__)


async def test_the_trace_shows_what_brain_two_was_handed() -> None:
    """`answerFrom` in the trace is the same value that crossed the boundary."""
    for execution in (
        await run(shuttle({}, limit=1), NOW, FakeData(), FakeWeb()),
        await run(shuttle({}, count=True), NOW, FakeData(), FakeWeb()),
        await run(Plan(lane=Lane.GENERAL), NOW, FakeData(), FakeWeb()),
    ):
        assert execution.summary()["answerFrom"] == execution.grounding()["answerFrom"]


# A general question with a shelf life


def general(freshness: Literal["stable", "current"], query: str | None = None) -> Plan:
    return Plan(lane=Lane.GENERAL, freshness=freshness, query=query)


async def test_a_stable_question_never_reaches_the_web() -> None:
    web = FakeWeb()
    execution = await run(general("stable"), NOW, FakeData(), web)
    assert web.searched is None
    assert execution.grounding() == {"answerFrom": "ownKnowledge"}


async def test_a_current_question_is_answered_from_the_web() -> None:
    web = FakeWeb()
    plan = general("current", "current population of Paris")
    execution = await run(plan, NOW, FakeData(), web)
    assert web.searched == "current population of Paris"
    assert execution.grounding() == {"answerFrom": "web", "results": [FACT]}


async def test_a_web_fact_carries_where_it_came_from() -> None:
    execution = await run(general("current", "anything"), NOW, FakeData(), FakeWeb())
    assert set(execution.results[0]) == {"fact", "source", "publishedAt"}


async def test_a_search_outage_ends_the_turn() -> None:
    with pytest.raises(ServiceError):
        await run(general("current", "anything"), NOW, FakeData(), FakeWeb(fails=True))


async def test_a_lane_that_did_not_run_grounds_nothing() -> None:
    """None, not an empty list — "nothing was looked up" is not "found none"."""
    execution = await run(Plan(lane=Lane.GENERAL), NOW, FakeData(), FakeWeb())
    assert execution.ran is False
    assert execution.grounding() == {"answerFrom": "ownKnowledge"}


async def test_a_capability_without_an_executor_ends_the_turn() -> None:
    plan = shuttle({}).model_copy(update={"capability": "menu"})
    with pytest.raises(ServiceError) as raised:
        await run(plan, NOW, FakeData(), FakeWeb())
    assert "menu" in str(raised.value.__cause__), "the trace still says which one"
    assert raised.value.retryable is False, "no executor is not a blip"


async def test_a_data_outage_ends_the_turn() -> None:
    """Degrading here is how an invented departure time gets written."""
    with pytest.raises(ServiceError) as raised:
        await run(shuttle({}), NOW, FakeData(fails=True), FakeWeb())
    assert raised.value.code == "DATASET_UNAVAILABLE"
    assert raised.value.retryable is True


async def test_a_concern_is_acted_on_before_any_lane_runs() -> None:
    """No capability, no executor, no network — and still an answer.

    The point of acting on the concern first: the turns that most need an
    answer are the ones least able to wait for campus data to come back.
    """
    checked = check(Plan(safety=[Concern.EMERGENCY], lane=Lane.CODE, capability="nope"), NOW)
    assert isinstance(checked, Plan)
    execution = await run(checked, NOW, _Unreachable(), _Unreachable())
    assert execution.summary()["answerFrom"] == "safety"
    assert execution.grounding()["results"] == [
        {"concern": "emergency", "must": CONCERNS[Concern.EMERGENCY]}
    ]


async def test_every_concern_is_enforced_not_only_the_first() -> None:
    """A question can be two things at once, and both need answering."""
    checked = check(Plan(safety=[Concern.PRIVACY, Concern.SECRET], lane=Lane.GENERAL), NOW)
    assert isinstance(checked, Plan)
    grounding = (await run(checked, NOW, _Unreachable(), _Unreachable())).grounding()
    assert [r["concern"] for r in grounding["results"]] == ["privacy", "secret"]


async def test_what_python_wrote_is_what_brain_three_is_handed() -> None:
    """The emergency numbers must not be summarised away between here and there."""
    checked = check(Plan(safety=[Concern.EMERGENCY], lane=Lane.GENERAL), NOW)
    assert isinstance(checked, Plan)
    grounding = (await run(checked, NOW, _Unreachable(), _Unreachable())).grounding()
    must = grounding["results"][0]["must"]
    assert "988" in must and "741741" in must and "911" in must


class _Unreachable:
    """Every outbound call, refusing. A safety turn must not make one."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"a safety turn must not call {name}")


async def test_the_web_is_searched_with_the_dated_query_not_the_planners() -> None:
    """The anchoring is the point of having two fields; searching `query` undoes it."""
    web = FakeWeb()
    checked = check(Plan(lane=Lane.GENERAL, freshness="current", query="population of France"), NOW)
    assert isinstance(checked, Plan)
    await run(checked, NOW, _Unreachable(), web)
    assert web.searched == f"population of France as of {NOW:%Y-%m-%d}"
