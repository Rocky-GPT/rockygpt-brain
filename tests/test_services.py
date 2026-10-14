"""Shuttle and dining questions, end to end through the engine with a scripted model.

The model only names what to look up; the dates, times, periods and menus are code, so these
tests read the packet the engine returns."""

import asyncio
import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.engine import ChatEngine, ChatResult
from rockygpt_brain.retrieval import MemoryEntityFacts
from rockygpt_brain.turn import intake
from rockygpt_brain.writer_view import writer_view
from test_engine import ScriptedGateway, completion, finish

EASTERN = ZoneInfo("America/New_York")
TUESDAY_10AM = datetime(2026, 10, 13, 10, 0, tzinfo=EASTERN)
COLLECTED = datetime(2026, 10, 13, 6, 0, tzinfo=EASTERN)


def _row(table: str, n: int, **fields: Any) -> dict[str, Any]:
    return {"id": f"{table}-{n}", "source_key": "dining" if table != "trip" else "transportation",
            "canonical_url": "https://example.edu/source", "freshness_sla_hours": 36,
            "collected_at": COLLECTED, "content_hash": f"{table}-{n}", **fields}


def _dining() -> list[dict[str, Any]]:
    week = {
        "Monday": "Breakfast: 08:00 AM - 10:30 AM; Lunch: 11:00 AM - 02:00 PM; "
                  "Dinner: 05:00 PM - 08:00 PM; Late Night: 09:00 PM - 11:00 PM",
        "Tuesday": "Breakfast: 08:00 AM - 10:30 AM; Lunch: 11:00 AM - 02:00 PM; "
                   "Dinner: 05:00 PM - 08:00 PM; Late Night: 09:00 PM - 11:00 PM",
        "Wednesday": "Breakfast: 08:00 AM - 10:30 AM; Lunch: 11:00 AM - 02:00 PM; "
                     "Dinner: 05:00 PM - 08:00 PM",
        "Thursday": "Breakfast: 08:00 AM - 10:30 AM; Lunch: 11:00 AM - 02:00 PM; "
                    "Dinner: 05:00 PM - 08:00 PM",
        "Friday": "Breakfast: 08:00 AM - 10:30 AM; Lunch: 11:00 AM - 02:00 PM",
        "Saturday": "Brunch: 10:30 AM - 03:00 PM; Dinner: 05:00 PM - 07:00 PM",
        "Sunday": "Closed",
    }
    rows = [_row("dining", i, source_record_key=f"Birch Tree Inn:{day}", name="Birch Tree Inn",
                 day=day, schedule=text, valid_from=None, valid_until=None)
            for i, (day, text) in enumerate(week.items())]
    # A dated exception: Wednesday October 14 only, dinner ends early.
    rows.append(_row(
        "dining", 20, source_record_key="Birch Tree Inn:Wednesday:2026-10-14:2026-10-14",
        name="Birch Tree Inn", day="Wednesday", schedule="Dinner: 05:00 PM - 06:00 PM",
        valid_from="2026-10-14", valid_until="2026-10-14"))
    return rows


def _menu() -> list[dict[str, Any]]:
    items = [
        ("2026-10-13", "Lunch", "Savory", "Braised Pot Roast", 443, "ENTREE", False, False),
        ("2026-10-13", "Lunch", "Grown", "Steamed Brown Rice", 218, "1 CUP", True, True),
        ("2026-10-13", "Dinner", "Slices", "Pepperoni Pizza", 244, "SLC=1/8", False, False),
        ("2026-10-14", "Dinner", "Savory", "Roast Turkey", 310, "ENTREE", False, False),
    ]
    return [_row("menu", i, source_record_key=f"{d}:{meal}:{station}:{name}", meal=meal,
                 station=station, name=name, calories=cal, portion_size=portion, vegan=vegan,
                 vegetarian=veg, label_coverage={"vegan": "published", "vegetarian": "published",
                                                  "allergens": "published"},
                 allergens=[], valid_from=d, valid_until=d)
            for i, (d, meal, station, name, cal, portion, vegan, veg) in enumerate(items)]


def _trips() -> list[dict[str, Any]]:
    def trip(n: int, route: str, day: str, seq: int, leaves: str, back: str,
             stops: list[tuple[str, str]]) -> dict[str, Any]:
        return _row("trip", n, source_record_key=f"{route}:{seq}:{leaves}", route_name=route,
                    service_day=day, sequence=seq, departure=leaves, arrival=back,
                    stops=[{"location": place, "time": at} for place, at in stops],
                    valid_from=None, valid_until=None)
    return [
        trip(1, "Weekday Roadrunner Express", "weekday", 0, "7:00 AM", "7:55 AM",
             [("Garden State Plaza", "7:30 AM")]),
        trip(2, "Weekday Roadrunner Express", "weekday", 1, "12:00 PM", "1:00 PM",
             [("Ramsey Rt 17 Train", "12:10 PM"), ("Garden State Plaza", "12:30 PM")]),
        trip(3, "Weekday Roadrunner Express", "weekday", 2, "9:40 PM", "10:45 PM",
             [("Ramsey Rt 17 Train", "9:50 PM"), ("Garden State Plaza", "10:15 PM")]),
        trip(4, "Saturday Roadrunner Express", "saturday", 0, "9:00 AM", "9:30 AM",
             [("Ramsey Rt 17 Train", "9:10 AM")]),
    ]


def facts() -> MemoryEntityFacts:
    def link(collection: str, source: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {"collection": collection, "source_key": source,
                "source_record_keys": [r["source_record_key"] for r in rows]}

    dining, menu, trips = _dining(), _menu(), _trips()
    by_route = {name: [t for t in trips if t["route_name"] == name]
                for name in ("Weekday Roadrunner Express", "Saturday Roadrunner Express")}
    return MemoryEntityFacts(
        dataset_version="release-1", identity_hash="identities-1",
        entities=[
            {"id": "birch", "name": "Birch Tree Inn", "kind": "venue", "aliases": ["Dining Hall"],
             "links": [link("dining_hours", "dining", dining), link("menu", "dining", menu)]},
            {"id": "route-weekday", "name": "Weekday Roadrunner Express", "kind": "route",
             "aliases": [], "links": [link("shuttle_trips", "transportation",
                                           by_route["Weekday Roadrunner Express"])]},
            {"id": "route-saturday", "name": "Saturday Roadrunner Express", "kind": "route",
             "aliases": [], "links": [link("shuttle_trips", "transportation",
                                           by_route["Saturday Roadrunner Express"])]},
        ],
        contacts=[],
        records={"dining_hours": dining, "menu": menu, "shuttle_trips": trips},
        now=lambda: TUESDAY_10AM,
    )


def ask(question: str, *steps: Any, now: datetime = TUESDAY_10AM) -> ChatResult:
    request = ChatRequest.model_validate({"messages": [{"role": "user", "content": question}]})
    engine = ChatEngine(ScriptedGateway(*steps), facts(), output="json")  # type: ignore[arg-type]
    return asyncio.run(engine.answer(intake(request, now=now), request))


def shuttle(stop: str | None, day: str | None, which: str) -> Any:
    return completion("shuttle_lookup", {"requests": [{"stop": stop, "day": day, "which": which}]})


def lookup(query: str, fields: list[str], day: str | None = None) -> Any:
    return completion("graph_lookup", {"requests": [
        {"query": query, "fields": fields, "day": day}]})


def derived(result: ChatResult, predicate: str) -> dict[str, Any]:
    packet = result.body["facts"]
    return next(d for d in packet["derived_facts"] if d["predicate"] == predicate)


def test_the_next_shuttle_to_a_stop_is_the_earliest_departure_after_now() -> None:
    result = ask("next shuttle to the train station?",
                 shuttle("Ramsey Rt 17 Train", "today", "next"), finish())
    answer = derived(result, "departures")["value"]
    assert [t["leaves_campus"] for t in answer["trips"]] == ["12:00 PM", "9:40 PM"]
    assert answer["trips"][0]["reached_at"] == "12:10 PM"
    assert answer["trips"][0]["route"] == "Weekday Roadrunner Express"
    packet = result.body["facts"]
    assert packet["status"] == "complete" and packet["request"]["intent"] == "shuttle"
    assert any(n["type"] == "shuttle_scope" for n in packet["notices"])
    # The writer is handed the one derived answer, not the three timetables.
    view = result.body["writerInput"]
    assert [d["predicate"] for d in view["derived_facts"]] == ["departures"]
    assert not any(f["predicate"] == "trips" for f in view.get("facts", []))


def test_after_the_last_trip_the_next_days_first_is_named() -> None:
    late = datetime(2026, 10, 13, 23, 30, tzinfo=EASTERN)
    result = ask("next shuttle", shuttle(None, None, "next"), finish(), now=late)
    answer = derived(result, "departures")["value"]
    assert answer["none_left"] is True and answer["last_left_at"] == "9:40 PM"
    assert answer["next_service"]["date"] == "2026-10-14"
    assert answer["next_service"]["trips"][0]["leaves_campus"] == "7:00 AM"


def test_a_weekday_names_its_own_date_and_a_saturday_uses_the_saturday_timetable() -> None:
    result = ask("first shuttle saturday", shuttle(None, "saturday", "first"), finish())
    entry = derived(result, "departures")
    assert entry["date"] == "2026-10-17" and entry["day"] == "Saturday"
    assert entry["value"]["trips"][0]["route"] == "Saturday Roadrunner Express"
    assert "already_left" not in entry["value"]


def test_a_stop_the_timetable_does_not_list_is_said_with_the_stops_it_does() -> None:
    result = ask("shuttle to the airport", shuttle("Newark Airport", None, "next"), finish())
    answer = derived(result, "departures")["value"]
    assert answer["stop_unknown"] is True and answer["asked"] == "Newark Airport"
    assert "Ramsey Rt 17 Train" in answer["published_stops"]


def test_the_models_stop_and_stop_list_come_from_the_published_timetable() -> None:
    gateway = ScriptedGateway(finish())
    request = ChatRequest.model_validate({"messages": [{"role": "user", "content": "hi"}]})
    engine = ChatEngine(gateway, facts(), output="json")  # type: ignore[arg-type]
    asyncio.run(engine.answer(intake(request, now=TUESDAY_10AM), request))
    state = json.loads(gateway.inputs[0][1]["content"])
    assert state["published_stops"] == ["Garden State Plaza", "Ramsey Rt 17 Train"]
    assert state["published_venues"] == [{"name": "Birch Tree Inn", "also_called": ["Dining Hall"]}]


def test_a_venue_says_which_meal_is_on_and_what_comes_next() -> None:
    noon = datetime(2026, 10, 13, 12, 15, tzinfo=EASTERN)
    result = ask("is birch open?", lookup("Birch Tree Inn", ["hours"], "today"), finish(), now=noon)
    entry = derived(result, "hours_on")
    assert entry["open_now"] == {"state": "open", "now": "12:15pm", "closes": "2:00pm",
                                 "meal": "Lunch"}
    assert [p["label"] for p in entry["value"]["periods"]] == [
        "Breakfast", "Lunch", "Dinner", "Late Night"]
    gap = datetime(2026, 10, 13, 20, 30, tzinfo=EASTERN)
    closed = derived(ask("is birch open?", lookup("Birch Tree Inn", ["hours"], "today"),
                         finish(), now=gap), "hours_on")
    assert closed["open_now"] == {"state": "closed", "now": "8:30pm", "next_opens": "9:00pm",
                                  "next_meal": "Late Night"}


def test_a_dated_exception_replaces_the_weekly_row_for_that_date_only() -> None:
    wednesday = ask("birch hours wednesday", lookup("Birch Tree Inn", ["hours"], "wednesday"),
                    finish())
    entries = [d for d in wednesday.body["facts"]["derived_facts"] if d["predicate"] == "hours_on"]
    assert [e["value"]["hours"] for e in entries] == ["Dinner: 05:00 PM - 06:00 PM"]
    assert wednesday.body["facts"]["status"] == "complete"
    thursday = ask("birch hours thursday", lookup("Birch Tree Inn", ["hours"], "thursday"),
                   finish())
    (entry,) = [d for d in thursday.body["facts"]["derived_facts"] if d["predicate"] == "hours_on"]
    assert entry["value"]["hours"].startswith("Breakfast: 08:00 AM")


def test_a_menu_is_read_for_the_date_asked_and_only_that_date() -> None:
    result = ask("what's for dinner tonight?", lookup("Birch Tree Inn", ["menu"], "today"),
                 finish())
    packet = result.body["facts"]
    (menu,) = [f for f in packet["facts"] if f["predicate"] == "menu"]
    assert menu["value"]["date"] == "2026-10-13"
    assert {m["meal"] for m in menu["value"]["meals"]} == {"Lunch", "Dinner"}
    dinner = next(m for m in menu["value"]["meals"] if m["meal"] == "Dinner")
    assert dinner["stations"] == [{"station": "Slices", "items": [{
        "name": "Pepperoni Pizza", "calories": 244, "portion": "SLC=1/8",
        "vegan": False, "vegetarian": False}]}]
    assert packet["request"]["intent"] == "menu"
    tomorrow = ask("menu tomorrow", lookup("Birch Tree Inn", ["menu"], "tomorrow"), finish())
    (found,) = [f for f in tomorrow.body["facts"]["facts"] if f["predicate"] == "menu"]
    assert found["value"]["date"] == "2026-10-14"
    assert found["value"]["meals"][0]["stations"][0]["items"][0]["name"] == "Roast Turkey"


def test_a_date_with_no_published_menu_is_not_called_closed() -> None:
    result = ask("menu friday", lookup("Birch Tree Inn", ["menu"], "friday"), finish())
    packet = result.body["facts"]
    assert not [f for f in packet["facts"] if f["predicate"] == "menu"]
    assert any(m["predicate"] == "menu" for m in packet["missing"])
    assert packet["status"] == "insufficient"


def lookup_meal(query: str, fields: list[str], day: str | None, meal: str | None) -> Any:
    return completion("graph_lookup", {"requests": [
        {"query": query, "fields": fields, "day": day, "meal": meal}]})


def test_a_menu_can_be_asked_for_one_meal_of_the_day() -> None:
    result = ask("lunch menu", lookup_meal("Birch Tree Inn", ["menu"], "today", "lunch"), finish())
    (menu,) = [f for f in result.body["facts"]["facts"] if f["predicate"] == "menu"]
    assert [m["meal"] for m in menu["value"]["meals"]] == ["Lunch"]
    names = [i["name"] for s in menu["value"]["meals"][0]["stations"] for i in s["items"]]
    assert names == ["Steamed Brown Rice", "Braised Pot Roast"]  # Stations in order: Grown, Savory.
    # A meal the published menu of that date does not list is said not to be listed, with the
    # meals it does list. It is not "no menu", and it is not an empty meal.
    none = ask("brunch menu", lookup_meal("Birch Tree Inn", ["menu"], "today", "brunch"), finish())
    (menu,) = [f for f in none.body["facts"]["facts"] if f["predicate"] == "menu"]
    assert menu["value"] == {"date": "2026-10-13", "meals": [], "meal_not_listed": "brunch",
                             "meals_listed": ["Lunch", "Dinner"]}
    assert not none.body["facts"]["missing"] and none.body["facts"]["status"] == "complete"


def test_an_hours_question_for_one_meal_is_answered_with_that_period() -> None:
    result = ask("what time is lunch?", lookup_meal("Birch Tree Inn", ["hours"], "today", "lunch"),
                 finish())
    value = derived(result, "hours_on")["value"]
    assert value["meal_asked"] == "lunch"
    assert value["periods"] == [{"label": "Lunch", "opens": "11:00am", "closes": "2:00pm"}]
    brunch = ask("when is brunch?", lookup_meal("Birch Tree Inn", ["hours"], "today", "brunch"),
                 finish())
    shown = derived(brunch, "hours_on")["value"]
    assert shown["meal_not_served"] is True and len(shown["periods"]) == 4


def test_an_exception_whose_dates_have_passed_is_not_part_of_a_venues_hours() -> None:
    entities = [{"id": "v", "name": "Dunkin'", "kind": "venue", "aliases": [], "links": [{
        "collection": "dining_hours", "source_key": "dining", "source_record_keys": [
            "Dunkin':Tuesday", "Dunkin':Tuesday:2026-10-01:2026-10-02"]}]}]
    rows = [
        _row("dining", 1, source_record_key="Dunkin':Tuesday", name="Dunkin'", day="Tuesday",
             schedule="07:30 AM - 03:00 PM", valid_from=None, valid_until=None),
        _row("dining", 2, source_record_key="Dunkin':Tuesday:2026-10-01:2026-10-02",
             name="Dunkin'", day="Tuesday", schedule="Hours unavailable",
             valid_from="2026-10-01", valid_until="2026-10-02"),
    ]
    memory = MemoryEntityFacts(dataset_version="r", identity_hash="i", entities=entities,
                               contacts=[], records={"dining_hours": rows},
                               now=lambda: TUESDAY_10AM)
    found = memory.get_office_facts("v", ["hours"], "r", as_of=TUESDAY_10AM)
    (hours,) = found["properties"]
    assert hours["status"] == "known" and len(hours["values"]) == 1
    assert "issues" not in hours and found["complete"] is True


def test_tomorrows_menu_is_an_answer_not_an_unverified_one() -> None:
    """A menu is valid for its own date: asked for that date it is current, though not today's."""
    result = ask("menu tomorrow", lookup("Birch Tree Inn", ["menu"], "tomorrow"), finish())
    assert result.body["status"] == "answered"
    assert result.body["facts"]["status"] == "complete"
    (menu,) = [f for f in result.body["facts"]["facts"] if f["predicate"] == "menu"]
    assert menu["current"] is True


def _atrium() -> MemoryEntityFacts:
    week = {"Monday": "07:30 AM - 07:00 PM", "Tuesday": "07:30 AM - 07:00 PM",
            "Wednesday": "07:30 AM - 07:00 PM", "Thursday": "07:30 AM - 07:00 PM",
            "Friday": "07:30 AM - 07:00 PM", "Saturday": "Hours unavailable",
            "Sunday": "Hours unavailable"}
    rows = [_row("dining", i, source_record_key=f"The Atrium:{day}", name="The Atrium", day=day,
                 schedule=text, valid_from=None, valid_until=None)
            for i, (day, text) in enumerate(week.items())]
    entities = [{"id": "atrium", "name": "The Atrium", "kind": "venue", "aliases": [], "links": [{
        "collection": "dining_hours", "source_key": "dining",
        "source_record_keys": [r["source_record_key"] for r in rows]}]}]
    return MemoryEntityFacts(dataset_version="r", identity_hash="i", entities=entities,
                             contacts=[], records={"dining_hours": rows}, now=lambda: TUESDAY_10AM)


def _atrium_packet(day: str | None) -> dict[str, Any]:
    request = ChatRequest.model_validate({"messages": [{"role": "user", "content": "atrium?"}]})
    engine = ChatEngine(ScriptedGateway(lookup("The Atrium", ["hours"], day), finish()),
                        _atrium(), output="json")  # type: ignore[arg-type]
    result = asyncio.run(engine.answer(intake(request, now=TUESDAY_10AM), request))
    packet: dict[str, Any] = result.body["facts"]
    return packet


def test_weekdays_a_schedule_does_not_know_are_not_said_for_another_day() -> None:
    wednesday = _atrium_packet("tomorrow")
    # The packet keeps the gap; the writer is not shown it beside a day that is answered.
    assert [m["days"] for m in wednesday["missing"]] == [["Saturday", "Sunday"]]
    assert "missing" not in writer_view(wednesday)
    saturday = _atrium_packet("saturday")  # An unknown day is still said to be unknown.
    assert [m["days"] for m in writer_view(saturday)["missing"]] == [["Saturday", "Sunday"]]
    week = _atrium_packet(None)  # No date asked: the whole week, unknowns included.
    assert [m["days"] for m in writer_view(week)["missing"]] == [["Saturday", "Sunday"]]


def test_a_venue_with_one_unlabelled_range_serves_no_meal_by_name() -> None:
    """The Atrium publishes 7:30-7:00 with no meal names: breakfast is neither served nor not."""
    request = ChatRequest.model_validate({"messages": [{"role": "user", "content": "breakfast?"}]})
    engine = ChatEngine(ScriptedGateway(
        lookup_meal("The Atrium", ["hours"], "today", "breakfast"), finish()),
        _atrium(), output="json")  # type: ignore[arg-type]
    result = asyncio.run(engine.answer(intake(request, now=TUESDAY_10AM), request))
    value = derived(result, "hours_on")["value"]
    assert value["meals_not_labelled"] is True and value["meal_asked"] == "breakfast"
    assert "meal_not_served" not in value
    assert value["hours"] == "07:30 AM - 07:00 PM"


def test_where_a_venue_closes_between_its_periods_is_given_not_worked_out() -> None:
    tuesday = ask("birch hours today", lookup("Birch Tree Inn", ["hours"], "today"), finish())
    gaps = derived(tuesday, "hours_on")["value"]["closed_gaps"]
    assert gaps == [
        {"after": "Breakfast", "closed_from": "10:30am", "reopens_at": "11:00am",
         "before": "Lunch"},
        {"after": "Lunch", "closed_from": "2:00pm", "reopens_at": "5:00pm", "before": "Dinner"},
        {"after": "Dinner", "closed_from": "8:00pm", "reopens_at": "9:00pm",
         "before": "Late Night"},
    ]


def test_a_time_the_student_gave_narrows_the_trips_by_code() -> None:
    after = completion("shuttle_lookup", {"requests": [{
        "stop": "Garden State Plaza", "day": None, "which": "next", "after": "1pm", "by": None}]})
    result = ask("a shuttle to the mall after 1pm", after, finish())
    value = derived(result, "departures")["value"]
    assert value["after"] == "1:00 PM"
    assert [t["leaves_campus"] for t in value["trips"]] == ["9:40 PM"]
    by = completion("shuttle_lookup", {"requests": [{
        "stop": "Ramsey Rt 17 Train", "day": "tomorrow", "which": "next", "after": None,
        "by": "12:30 PM"}]})
    reached = ask("which shuttle gets me to the train by 12:30", by, finish())
    got = derived(reached, "departures")["value"]
    assert got["by"] == "12:30 PM" and [t["reached_at"] for t in got["trips"]] == ["12:10 PM"]
    junk = completion("shuttle_lookup", {"requests": [{
        "stop": None, "day": None, "which": "next", "after": "evening", "by": None}]})
    unread = ask("evening shuttle", junk, finish())
    assert derived(unread, "departures")["value"]["time_unreadable"] == "evening"
