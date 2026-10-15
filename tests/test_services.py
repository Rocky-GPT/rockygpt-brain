"""Shuttle and dining questions, end to end through the engine with a scripted model.

The model only names what to look up; the dates, times, periods and menus are code, so these
tests read the packet the engine returns."""

import asyncio
import json
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.engine import ChatEngine, ChatResult
from rockygpt_brain.retrieval import MemoryEntityFacts
from rockygpt_brain.shuttle import SPECIAL_TEXT
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
    assert [stop["name"] for stop in state["published_stops"]] == ["Garden State Plaza",
                                                                    "Ramsey Rt 17 Train"]
    assert state["published_venues"] == [{"name": "Birch Tree Inn", "also_called": ["Dining Hall"],
                                          "publishes": ["hours", "menu"]}]


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
    (gap,) = [m for m in packet["missing"] if m["predicate"] == "menu"]
    assert gap["dates_published"] == ["2026-10-13", "2026-10-14"]  # What it does have.
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


def _lunch_menu() -> MemoryEntityFacts:
    items = [  # (meal, station, name, calories, portion, vegan, vegetarian, allergens, coverage)
        ("Lunch", "Grown", "Steamed Brown Rice", 218, "1 CUP", True, True, [], "published"),
        ("Lunch", "Grown", "Tofu Stir Fry", 370, "ENTREE", False, True, ["Soy"], "published"),
        ("Lunch", "Savory", "Braised Pot Roast", 443, "ENTREE", False, False, [], "published"),
        ("Lunch", "Sizzle", "Black Bean Burger", 360, "SANDWICH", True, True,
         ["Soy", "Wheat"], "published"),
        ("Lunch", "Slices", "Cheese Pizza", 244, "SLC=1/8", False, True,
         ["Milk", "Wheat"], "published"),
        ("Lunch", "Spoonful", "Three Mushroom Barley Soup", 90, "8 OZ", True, True,
         ["Wheat"], "published"),
        ("Lunch", "Spoonful", "New England Clam Chowder", 210, "8 OZ", False, False,
         ["Milk", "Shellfish"], "published"),
        ("Lunch", "Stacked", "Hummus Plate", 150, "1 EA", True, True, None, "not_published"),
        ("Dinner", "Slices", "Pepperoni Pizza", 260, "SLC=1/8", False, False,
         ["Milk", "Wheat"], "published"),
    ]
    rows = [_row("menu", i, source_record_key=f"2026-10-13:{meal}:{station}:{name}", meal=meal,
                 station=station, name=name, calories=cal, portion_size=portion, vegan=vegan,
                 vegetarian=veg, allergens=allergens,
                 label_coverage={"vegan": "published", "vegetarian": "published",
                                 "allergens": coverage},
                 valid_from="2026-10-13", valid_until="2026-10-13")
            for i, (meal, station, name, cal, portion, vegan, veg, allergens,
                    coverage) in enumerate(items)]
    entities = [{"id": "birch", "name": "Birch Tree Inn", "kind": "venue", "aliases": [],
                 "links": [{"collection": "menu", "source_key": "dining",
                            "source_record_keys": [r["source_record_key"] for r in rows]}]}]
    return MemoryEntityFacts(dataset_version="r", identity_hash="i", entities=entities,
                             contacts=[], records={"menu": rows}, now=lambda: TUESDAY_10AM)


def _menu_value(**filters: Any) -> dict[str, Any]:
    request = ChatRequest.model_validate({"messages": [{"role": "user", "content": "menu?"}]})
    arguments = {"query": "Birch Tree Inn", "fields": ["menu"], "day": "today", "meal": "lunch",
                 "diet": None, "items": [], "with_allergens": [], "without_allergens": [],
                 **filters}
    engine = ChatEngine(ScriptedGateway(completion("graph_lookup", {"requests": [arguments]}),
                                        finish()), _lunch_menu(),
                        output="json")  # type: ignore[arg-type]
    result = asyncio.run(engine.answer(intake(request, now=TUESDAY_10AM), request))
    (menu,) = [f for f in result.body["facts"]["facts"] if f["predicate"] == "menu"]
    value: dict[str, Any] = menu["value"]
    return value


def _names(value: dict[str, Any]) -> list[str]:
    return [i["name"] for m in value["meals"] for s in m["stations"] for i in s["items"]]


def test_a_menu_keeps_only_the_items_the_student_asked_for() -> None:
    veg = _menu_value(diet="vegetarian")
    assert _names(veg) == ["Steamed Brown Rice", "Tofu Stir Fry", "Black Bean Burger",
                           "Cheese Pizza", "Three Mushroom Barley Soup", "Hummus Plate"]
    assert veg["matched"] == 6 and veg["on_menu"] == 8 and veg["filter"] == {
        "diet": "vegetarian"}
    vegan = _menu_value(diet="vegan")
    assert _names(vegan) == ["Steamed Brown Rice", "Black Bean Burger",
                             "Three Mushroom Barley Soup", "Hummus Plate"]
    soup = _menu_value(items=["soup", "chowder"])
    assert _names(soup) == ["Three Mushroom Barley Soup", "New England Clam Chowder"]
    # A name or a station both count; plurals do not matter.
    assert _names(_menu_value(items=["Sizzle"])) == ["Black Bean Burger"]
    assert _names(_menu_value(items=["burgers"])) == ["Black Bean Burger"]
    # Calories and portion come with the item as published.
    burger = _menu_value(items=["black bean burger"])["meals"][0]["stations"][0]["items"][0]
    assert burger["calories"] == 360 and burger["portion"] == "SANDWICH"


def test_a_food_the_menu_does_not_have_says_what_it_does_have() -> None:
    value = _menu_value(items=["grill"])
    assert value["meals"] == [] and value["matched"] == 0 and value["on_menu"] == 8
    assert value["meals_listed"] == ["Lunch"]
    assert value["stations_listed"] == ["Grown", "Savory", "Sizzle", "Slices", "Spoonful",
                                        "Stacked"]
    both = _menu_value(diet="vegan", items=["pizza"])
    assert both["meals"] == [] and both["filter"] == {"diet": "vegan", "contains": ["pizza"]}


def test_allergens_are_matched_only_as_the_labels_publish_them() -> None:
    dairy = _menu_value(with_allergens=["dairy"])
    assert _names(dairy) == ["Cheese Pizza", "New England Clam Chowder"]
    assert dairy["allergens_published"] == ["Milk", "Shellfish", "Soy", "Wheat"]
    free = _menu_value(without_allergens=["dairy"])
    # The hummus has no published allergen labels, so it is not called dairy-free.
    assert _names(free) == ["Steamed Brown Rice", "Tofu Stir Fry", "Braised Pot Roast",
                            "Black Bean Burger", "Three Mushroom Barley Soup"]
    # Free of several is free of all of them; with several is any of them.
    both = _menu_value(without_allergens=["dairy", "wheat"])
    assert _names(both) == ["Steamed Brown Rice", "Tofu Stir Fry", "Braised Pot Roast"]
    either = _menu_value(with_allergens=["shellfish", "soy"])
    assert _names(either) == ["Tofu Stir Fry", "Black Bean Burger", "New England Clam Chowder"]
    nuts = _menu_value(with_allergens=["tree nuts"])
    assert nuts["meals"] == [] and nuts["matched"] == 0
    assert "Tree Nuts" not in nuts["allergens_published"]


def test_a_part_no_tool_answers_beside_a_shuttle_answer_has_no_office_to_point_at() -> None:
    from rockygpt_brain.engine import UNSUPPORTED_AFTER_LOOKUP_MESSAGE, UNSUPPORTED_PART_MESSAGE
    result = ask("next shuttle to the mall and is it late?", shuttle("Garden State Plaza", None,
                                                                      "next"),
                 finish("unsupported", student_words="is it late"))
    said = [n["approved_text"] for n in result.body["facts"]["notices"]
            if n["type"] == "unsupported"]
    assert said == [UNSUPPORTED_PART_MESSAGE] and UNSUPPORTED_AFTER_LOOKUP_MESSAGE not in said


def test_all_with_no_stop_says_where_the_shuttle_goes() -> None:
    every = completion("shuttle_lookup", {"requests": [{
        "stop": None, "day": None, "which": "all", "after": None, "by": None, "times": False}]})
    value = derived(ask("where does the shuttle go", every, finish()), "departures")["value"]
    assert value["stops_served"] == ["Garden State Plaza", "Ramsey Rt 17 Train"]
    assert value["trips"] == [] and value["trip_count"] == 3


def test_all_gives_the_trips_only_when_the_times_were_asked_for() -> None:
    def call(times: bool) -> dict[str, Any]:
        args = completion("shuttle_lookup", {"requests": [{
            "stop": "Ramsey Rt 17 Train", "day": None, "which": "all", "after": None, "by": None,
            "times": times}]})
        value: dict[str, Any] = derived(ask("train shuttle", args, finish()),
                                        "departures")["value"]
        return value
    summary, listed = call(False), call(True)
    assert summary["trips"] == [] and summary["trip_count"] == 2
    assert (summary["first_leaves_campus"], summary["last_leaves_campus"]) == ("12:00 PM",
                                                                               "9:40 PM")
    assert [t["leaves_campus"] for t in listed["trips"]] == ["12:00 PM", "9:40 PM"]


def test_the_graph_lists_offices_dining_and_shuttle_each_under_its_own_branch() -> None:
    from rockygpt_brain.retrieval.campus_graph import CampusGraph
    root = CampusGraph(facts()).inspect("ramapo", TUESDAY_10AM)
    assert [c["id"] for c in root["children"]] == ["offices", "dining", "shuttle", "topics"]
    assert "Only the office branch" not in root["scope"]
    assert CampusGraph(facts()).inspect("offices", TUESDAY_10AM)["children"] == []
    dining = CampusGraph(facts()).inspect("dining", TUESDAY_10AM)
    assert [(c["label"], c["kind"]) for c in dining["children"]] == [("Birch Tree Inn", "venue")]
    venue = CampusGraph(facts()).inspect("office:birch", TUESDAY_10AM)
    assert [s["id"] for s in venue["path"]] == ["ramapo", "dining", "office:birch"]
    assert venue["facts"]["entity"]["name"] == "Birch Tree Inn"
    shuttle = CampusGraph(facts()).inspect("shuttle", TUESDAY_10AM)
    assert [c["label"] for c in shuttle["children"]] == [
        "Saturday Roadrunner Express", "Weekday Roadrunner Express"]
    route = CampusGraph(facts()).inspect("route:route-weekday", TUESDAY_10AM)
    assert [s["id"] for s in route["path"]] == ["ramapo", "shuttle", "route:route-weekday"]
    assert route["facts"]["entity"]["name"] == "Weekday Roadrunner Express"
    assert route["facts"]["evidence_count"] == 3


def test_a_venue_lookup_walks_through_dining() -> None:
    from rockygpt_brain.retrieval.campus_graph import CampusGraph
    graph = CampusGraph(facts())
    found = graph.lookup("Birch Tree Inn", ["hours"], TUESDAY_10AM)
    assert found["label"] == "Birch Tree Inn"
    assert [s["id"] for s in graph.path(graph.current_node)] == [
        "ramapo", "dining", "office:birch"]


def test_inspecting_a_venue_reads_its_menu_for_the_campus_date_and_an_office_has_no_menu() -> None:
    from rockygpt_brain.retrieval.campus_graph import CampusGraph
    venue = CampusGraph(facts()).inspect("office:birch", TUESDAY_10AM)
    menu = next(p for p in venue["facts"]["properties"] if p["key"] == "menu")
    assert menu["status"] == "known"
    assert menu["values"][0]["value"]["date"] == TUESDAY_10AM.date().isoformat()
    assert venue["menu_dates"] and TUESDAY_10AM.date().isoformat() in venue["menu_dates"]
    today = TUESDAY_10AM.date().isoformat()
    other = date.fromisoformat(next(d for d in venue["menu_dates"] if d != today))
    picked = CampusGraph(facts()).inspect("office:birch", TUESDAY_10AM, on=other)
    menu = next(p for p in picked["facts"]["properties"] if p["key"] == "menu")
    assert menu["values"][0]["value"]["date"] == other.isoformat()


def test_a_trip_keeps_the_nj_transit_trains_its_row_lists() -> None:
    from rockygpt_brain.retrieval.entity_facts import trips_property
    rows = [_row("trip", 9, source_record_key="R:0:6:10 PM", route_name="R", service_day="weekday",
                 sequence=0, departure="6:10 PM", arrival="7:35 PM",
                 stops=[{"location": "Ramsey Rt 17 Train", "time": "6:20 PM"}],
                 trains={"arrives_from_nyc": ["6:44 PM", "7:07 PM"], "leaves_for_nyc": ["6:31 PM"]},
                 valid_from=None, valid_until=None),
            _row("trip", 10, source_record_key="R:1:8:25 AM", route_name="R", service_day="weekday",
                 sequence=1, departure="8:25 AM", arrival="9:25 AM", stops=[], valid_from=None,
                 valid_until=None)]
    prop, _ = trips_property(rows, TUESDAY_10AM)
    first, second = prop["values"][0]["value"]["trips"]
    assert first["trains"] == {"arrives_from_nyc": ["6:44 PM", "7:07 PM"],
                               "leaves_for_nyc": ["6:31 PM"]}
    assert "trains" not in second


def test_a_special_day_timetable_is_offered_by_name_and_never_answers_a_regular_day() -> None:
    base = facts()
    special_rows = [_row("trip", 50, source_record_key="Special Shuttle Schedule: Sept 7:0:9:00 AM",
                         route_name="Special Shuttle Schedule: September 7th (Labor Day)",
                         service_day="special", sequence=0, departure="9:00 AM", arrival="9:40 AM",
                         stops=[{"location": "Garden State Plaza", "time": "9:20 AM"}],
                         valid_from=None, valid_until=None)]
    entities = [*base.entities, {
        "id": "route-special", "name": "Special Shuttle Schedule: September 7th (Labor Day)",
        "kind": "route", "aliases": [], "links": [{
            "collection": "shuttle_trips", "source_key": special_rows[0]["source_key"],
            "source_record_keys": [special_rows[0]["source_record_key"]]}]}]
    records = {**base.records, "shuttle_trips": [*base.records["shuttle_trips"], *special_rows]}
    message = {"role": "user", "content": "next shuttle"}
    request = ChatRequest.model_validate({"messages": [message]})
    memory = MemoryEntityFacts(dataset_version="release-1", identity_hash="identities-1",
                               entities=entities, contacts=[], records=records,
                               now=lambda: TUESDAY_10AM)
    engine = ChatEngine(ScriptedGateway(shuttle("Garden State Plaza", None, "next"), finish()),
                        memory, output="json")  # type: ignore[arg-type]
    result = asyncio.run(engine.answer(intake(request, now=TUESDAY_10AM), request))
    packet = result.body["facts"]
    said = [n["approved_text"] for n in packet["notices"] if n["type"] == "shuttle_special"]
    assert said == [SPECIAL_TEXT.format(days="September 7th (Labor Day)")]
    assert all("Special" not in f["subject"]["name"] for f in packet["facts"])
    assert derived(result, "departures")["value"]["trips"][0]["route"] != entities[-1]["name"]


def test_a_venue_that_posts_its_menu_as_a_file_says_so_without_items() -> None:
    entities = [{"id": "atrium", "name": "The Atrium", "kind": "venue", "aliases": [], "links": [
        {"collection": "menu_documents", "source_key": "dining",
         "source_record_keys": ["The Atrium:menu-document:Atrium Menu Boards 2026"]}]}]
    key = "The Atrium:menu-document:Atrium Menu Boards 2026"
    files = [_row("document", 1, source_record_key=key, name="The Atrium",
                  title="Atrium Menu Boards 2026",
                  url="https://media.example/web/en-us/media/Atrium.pdf",
                  mime_type="application/pdf")]
    memory = MemoryEntityFacts(dataset_version="r", identity_hash="i", entities=entities,
                               contacts=[], records={"menu_documents": files},
                               now=lambda: TUESDAY_10AM)
    read = memory.get_office_facts("atrium", ["menu"], "r", as_of=TUESDAY_10AM,
                                   on=TUESDAY_10AM.date())
    menu = read["properties"][0]
    assert menu["status"] == "unknown" and menu["values"] == []
    assert menu["documents_published"] == [
        {"title": "Atrium Menu Boards 2026",
         "url": "https://media.example/web/en-us/media/Atrium.pdf"}]



def test_a_venue_whose_listing_names_no_menu_page_publishes_no_daily_menu() -> None:
    def venue(dated: bool, menus: list[dict[str, str]]) -> MemoryEntityFacts:
        key = "The Atrium:menu-listing"
        entities = [{"id": "atrium", "name": "The Atrium", "kind": "venue", "aliases": [],
                     "links": [{"collection": "menu_listings", "source_key": "dining",
                                "source_record_keys": [key]}]}]
        listing = _row("listing", 1, source_record_key=key, name="The Atrium", dated=dated,
                       menus=menus, content_hash="a" * 64)
        return MemoryEntityFacts(dataset_version="r", identity_hash="i", entities=entities,
                                 contacts=[], records={"menu_listings": [listing]},
                                 now=lambda: TUESDAY_10AM)

    file = [{"name": "The Atrium Menu", "kind": "file", "uri": "/web/en-us/media/A.pdf"}]
    read = venue(False, file).get_office_facts("atrium", ["menu"], "r", as_of=TUESDAY_10AM,
                                               on=TUESDAY_10AM.date())
    menu = read["properties"][0]
    assert menu["status"] == "not_published" and menu["values"] == []
    assert menu["absence"]["checks"] == [{
        "url": "https://example.edu/source", "section": "The Atrium: menus listed",
        "checked_at": menu["absence"]["checked_at"], "text_sha256": "a" * 64}]
    assert menu["absence"]["reason"].endswith("it links only a menu file.")
    assert menu["absence"]["source_ids"] == ["listing-1"]
    assert any(source["id"] == "listing-1" for source in read["sources"])
    # A listing naming a menu page leaves a date with no menu unknown, never not published.
    page = [{"name": "Birch Tree Inn Menu", "kind": "page", "uri": "/en-us/locations/birch"}]
    read = venue(True, page).get_office_facts("atrium", ["menu"], "r", as_of=TUESDAY_10AM,
                                              on=TUESDAY_10AM.date())
    assert read["properties"][0]["status"] == "unknown"


def test_a_shuttle_answer_about_a_stop_says_where_to_wait_there() -> None:
    base = facts()
    stop = _row("stop", 1, source_record_key="stop:Garden State Plaza", name="Garden State Plaza",
                label="Garden State Plaza", description="A mall at Routes 4 and 17.",
                pickup="the Bus Shelters across from Neiman Marcus")
    entities = [{**e, "links": [*e["links"], {"collection": "shuttle_stops",
                                               "source_key": stop["source_key"],
                                               "source_record_keys": [stop["source_record_key"]]}]}
                if e["kind"] == "route" else e for e in base.entities]
    records = {**base.records, "shuttle_stops": [stop]}
    memory = MemoryEntityFacts(dataset_version="release-1", identity_hash="identities-1",
                               entities=entities, contacts=[], records=records,
                               now=lambda: TUESDAY_10AM)
    message = {"role": "user", "content": "where do I catch the shuttle at the mall"}
    request = ChatRequest.model_validate({"messages": [message]})
    engine = ChatEngine(ScriptedGateway(shuttle("Garden State Plaza", None, "next"), finish()),
                        memory, output="json")  # type: ignore[arg-type]
    result = asyncio.run(engine.answer(intake(request, now=TUESDAY_10AM), request))
    value = derived(result, "departures")["value"]
    assert value["stop_description"] == {"description": "A mall at Routes 4 and 17.",
                                         "pickup": "the Bus Shelters across from Neiman Marcus"}


def test_each_published_stop_says_what_it_is_from_its_own_description() -> None:
    # "first shuttle to the mall sunday" was sent with no stop (2026-10-15): the model saw only
    # names. The stop-descriptions page says which stop is the mall.
    stop = _row("stop", 1, source_record_key="stop:Garden State Plaza", name="Garden State Plaza",
                label="Garden State Plaza", pickup="the Bus Shelters",
                description="Located at Routes 4 and 17 in Paramus, this mall stop is often "
                            "used for the movies (https://example.edu/amc). Other stores too.")
    base = facts()
    entities = [{**e, "links": [*e["links"], {"collection": "shuttle_stops",
                                               "source_key": stop["source_key"],
                                               "source_record_keys": [stop["source_record_key"]]}]}
                if e["kind"] == "route" else e for e in base.entities]
    memory = MemoryEntityFacts(dataset_version="release-1", identity_hash="identities-1",
                               entities=entities, contacts=[],
                               records={**base.records, "shuttle_stops": [stop]},
                               now=lambda: TUESDAY_10AM)
    gateway = ScriptedGateway(finish())
    request = ChatRequest.model_validate({"messages": [{"role": "user", "content": "hi"}]})
    engine = ChatEngine(gateway, memory, output="json")  # type: ignore[arg-type]
    asyncio.run(engine.answer(intake(request, now=TUESDAY_10AM), request))
    state = json.loads(gateway.inputs[0][1]["content"])
    assert state["published_stops"][0] == {
        "name": "Garden State Plaza",
        "is": "Located at Routes 4 and 17 in Paramus, this mall stop is often used for the movies. "
              "Other stores too."}
    assert state["published_stops"][1] == {"name": "Ramsey Rt 17 Train"}


def test_the_campus_stop_trips_leave_from_is_offered_and_answers_where_to_catch_it() -> None:
    base = facts()
    campus = _row("stop", 2, source_record_key="stop:Ramapo College (Bradley Center)",
                  name="Ramapo College (Bradley Center)", label="Ramapo College (Bradley Center)",
                  description="The bus stop on campus is located outside the Bradley Center.",
                  pickup=None)
    link = {"collection": "shuttle_stops", "source_key": campus["source_key"],
            "source_record_keys": [campus["source_record_key"]]}
    entities = [{**e, "links": [*e["links"], link]} if e["kind"] == "route" else e
                for e in base.entities]
    memory = MemoryEntityFacts(dataset_version="release-1", identity_hash="identities-1",
                               entities=entities, contacts=[],
                               records={**base.records, "shuttle_stops": [campus]},
                               now=lambda: TUESDAY_10AM)
    gateway = ScriptedGateway(shuttle("Ramapo College (Bradley Center)", None, "all"), finish())
    request = ChatRequest.model_validate(
        {"messages": [{"role": "user", "content": "where does the shuttle stop on campus"}]})
    engine = ChatEngine(gateway, memory, output="json")  # type: ignore[arg-type]
    result = asyncio.run(engine.answer(intake(request, now=TUESDAY_10AM), request))
    state = json.loads(gateway.inputs[0][1]["content"])
    assert state["published_stops"][-1] == {
        "name": "Ramapo College (Bradley Center)",
        "is": "The bus stop on campus is located outside the Bradley Center.", "departs_here": True}
    value = derived(result, "departures")["value"]
    assert value["departs_from"] == "Ramapo College (Bradley Center)"
    assert value["stop_description"] == {
        "description": "The bus stop on campus is located outside the Bradley Center."}
    assert "stop_unknown" not in value
