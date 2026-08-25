"""PYTHON runs the lane, and the generic operations are applied in Python."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from rockygpt_brain.core.execute import run
from rockygpt_brain.core.plan import Filter, Lane, Operation, Plan
from rockygpt_brain.core.validate import Rejected
from rockygpt_brain.services.data import DataUnavailable

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


def shuttle(filters: dict[str, str] | None = None, **operation: Any) -> Plan:
    return Plan(
        lane=Lane.CODE,
        capability="shuttle",
        filters=[Filter(field=k, value=v) for k, v in (filters or {}).items()],
        operation=Operation(**operation),
    )


# The lookup happens


async def test_a_shuttle_plan_runs_and_says_so() -> None:
    execution = await run(shuttle({"date": "2031-03-06"}), NOW, FakeData())
    assert execution.ran is True
    assert len(execution.results) == 3


async def test_the_plans_filters_become_the_services_query() -> None:
    data = FakeData()
    await run(shuttle({"date": "2031-03-06", "destination": "Garden State Plaza"}), NOW, data)
    assert data.query["serviceDate"] == "2031-03-06"
    assert data.query["destination"] == "Garden State Plaza"


async def test_the_service_is_never_asked_to_choose() -> None:
    """`selection` stays "all" so its vocabulary never leaks back into a plan."""
    data = FakeData()
    await run(shuttle({"date": "2031-03-06"}, order_by="departureTime", limit=1), NOW, data)
    assert data.query["selection"] == "all"


async def test_a_time_filter_asks_only_for_what_is_left() -> None:
    data = FakeData()
    await run(shuttle({"departingAfter": "2031-03-06T13:30:00-05:00"}), NOW, data)
    assert data.query["timeScope"] == "remaining"
    assert data.query["asOf"] == "2031-03-06T13:30:00-05:00"


# The operations are applied in Python


async def test_descending_with_a_limit_of_one_is_the_last_trip() -> None:
    execution = await run(
        shuttle({}, order_by="departureTime", direction="descending", limit=1), NOW, FakeData()
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
        shuttle({}, order_by="departureTime", direction="ascending", limit=1), NOW, FakeData()
    )
    assert execution.results[0]["departureTime"] == "7:00 AM"


async def test_times_sort_as_times_not_as_text() -> None:
    """`10:30 PM` sorts after `7:00 AM`, which string ordering gets wrong."""
    execution = await run(shuttle({}, order_by="departureTime"), NOW, FakeData())
    assert [row["departureTime"] for row in execution.results] == [
        "7:00 AM",
        "1:15 PM",
        "10:30 PM",
    ]


async def test_count_answers_with_how_many_matched() -> None:
    execution = await run(shuttle({}, count=True), NOW, FakeData())
    assert execution.count == 3
    assert execution.results == []


async def test_count_is_of_matches_not_of_what_a_limit_kept() -> None:
    execution = await run(shuttle({}, count=True, limit=1), NOW, FakeData())
    assert execution.count == 3


async def test_a_record_is_cut_down_to_the_fields_the_capability_publishes() -> None:
    execution = await run(shuttle({}, limit=1), NOW, FakeData())
    assert "evidenceIds" not in execution.results[0]


# What BRAIN #2 is given


async def test_what_ran_is_what_brain_two_answers_from() -> None:
    execution = await run(shuttle({}, limit=1), NOW, FakeData())
    assert execution.grounding() == execution.results


async def test_looking_and_finding_none_is_not_the_same_as_not_looking() -> None:
    """The one distinction the summary exists to draw."""
    found_none = await run(shuttle({}, limit=1), NOW, FakeData(records=[]))
    never_looked = await run(Plan(lane=Lane.GENERAL), NOW, FakeData())

    assert found_none.summary() == {"results": []}, "an empty list, not a missing one"
    assert "results" not in never_looked.summary()

    assert found_none.grounding() == [], "BRAIN #2 is told the lookup came back empty"
    assert never_looked.grounding() is None, "BRAIN #2 is told nothing was looked up"


async def test_a_count_reports_the_count_and_not_an_empty_list() -> None:
    execution = await run(shuttle({}, count=True), NOW, FakeData())
    assert execution.summary() == {"count": 3}


async def test_a_lane_that_did_not_run_grounds_nothing() -> None:
    """None, not an empty list — "nothing was looked up" is not "found none"."""
    execution = await run(Plan(lane=Lane.GENERAL), NOW, FakeData())
    assert execution.ran is False
    assert execution.grounding() is None


async def test_a_capability_without_an_executor_says_which_one() -> None:
    execution = await run(shuttle({}).model_copy(update={"capability": "menu"}), NOW, FakeData())
    assert execution.ran is False
    assert "menu" in execution.note


async def test_a_data_outage_costs_the_lookup_not_the_turn() -> None:
    execution = await run(shuttle({}), NOW, FakeData(fails=True))
    assert execution.ran is False
    assert execution.grounding() is None
    assert "did not happen" in execution.note


async def test_a_rejected_plan_never_reaches_the_data_service() -> None:
    data = FakeData()
    execution = await run(Rejected("no capability named 'weather'"), NOW, data)
    assert execution.ran is False
    assert data.query == {}
