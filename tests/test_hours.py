"""Hours: an office's linked schedule records, read through the shared fact reader.

The schedules are the graph's existing `campus_hours` records. The reader groups the weekday rows
of one named schedule into one value, keeps its validity window and the official sentence, and
never decides between schedules that disagree.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import psycopg
import pytest

from rockygpt_brain.answers import render_facts
from rockygpt_brain.retrieval import (
    EvidenceUnavailable,
    MemoryEntityFacts,
    PostgresEntityFacts,
)
from rockygpt_brain.retrieval.entity_facts import (
    Snapshot,
    linked_schedules,
    validate_entities,
)

NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
WEEK = {"Monday": "8:30am-4:30pm", "Tuesday": "8:30am-4:30pm", "Wednesday": "8:30am-4:30pm",
        "Thursday": "8:30am-4:30pm", "Friday": "8:30am-4:30pm",
        "Saturday": "Hours unavailable", "Sunday": "Hours unavailable"}
NOTE = "Fall/Spring Hours: 8:30 A.M. - 4:30 P.M. Monday - Friday"


def schedule_rows(name: str = "Registrar", week: dict[str, str] | None = None, *,
                  since: str | None = "2026-08-26", until: str | None = "2026-12-16",
                  note: str | None = NOTE, url: str | None = "https://example.edu/registrar/",
                  tag: str = "a", collected: datetime = NOW) -> list[dict[str, Any]]:
    return [{
        "id": f"{tag}-{name}-{day}", "source_key": "campus-hours",
        "source_record_key": f"{name}:{day}", "name": name, "day": day, "schedule": text,
        "notes": note, "source_url": url, "collected_at": collected, "valid_from": since,
        "valid_until": until, "content_hash": f"hash-{tag}-{day}",
        "canonical_url": "https://example.edu/campus-hours/", "freshness_sla_hours": 4_320,
    } for day, text in (week or WEEK).items()]


def office(entity_id: str = "registrar", name: str = "Registrar",
           schedules: list[str] | None = None) -> dict[str, Any]:
    keys = [f"{schedule}:{day}" for schedule in (schedules or [name]) for day in DAYS]
    return {
        "id": entity_id, "kind": "office", "name": name, "aliases": [],
        "links": [
            {"collection": "contacts", "source_key": "campus-directory",
             "source_record_keys": [f"office:{entity_id}"]},
            *([{"collection": "campus_hours", "source_key": "campus-hours",
                "source_record_keys": keys}] if schedules != [] else []),
        ],
    }


def contact() -> dict[str, Any]:
    return {
        "id": "contact-1", "source_key": "campus-directory",
        "source_record_key": "office:registrar",
        "name": "Registrar", "email": "reg@example.edu", "phone": "(201) 555-0100",
        "office": "D224", "collected_at": NOW, "freshness_sla_hours": 168,
        "canonical_url": "https://example.edu/directory", "content_hash": "contact-hash",
    }


def reader(rows: list[dict[str, Any]], entity: dict[str, Any] | None = None) -> MemoryEntityFacts:
    return MemoryEntityFacts(
        dataset_version="release-1", identity_hash="identities-1",
        entities=[entity or office()], contacts=[contact()], schedules=rows, now=lambda: NOW)


def hours(service: MemoryEntityFacts, **kwargs: Any) -> dict[str, Any]:
    return service.get_office_facts(
        "registrar", ["hours"], "release-1", identity_hash="identities-1", **kwargs)


def hours_property(result: dict[str, Any]) -> dict[str, Any]:
    return next(p for p in result["properties"] if p["key"] == "hours")


def test_a_weeks_rows_become_one_value_with_its_window_note_and_source() -> None:
    result = hours(reader(schedule_rows()))
    prop = hours_property(result)
    assert prop["status"] == "known" and prop["category"] == "schedule"
    assert len(prop["values"]) == 1
    value = prop["values"][0]["value"]
    assert value["schedule"] == "Registrar" and value["notes"] == [NOTE]
    assert [d["day"] for d in value["days"]] == list(DAYS)
    assert value["days"][0] == {"day": "Monday", "hours": "8:30am-4:30pm"}
    assert value["days"][5] == {"day": "Saturday", "hours": "Hours unavailable"}
    source = next(s for s in result["sources"] if s["id"] == prop["values"][0]["source_ids"][0])
    assert source["collection"] == "campus_hours"
    assert (source["valid_from"], source["valid_until"]) == ("2026-08-26", "2026-12-16")
    assert source["citation_urls"] == ["https://example.edu/registrar/"]
    assert source["validity"] == "current" and source["freshness"] == "fresh"
    assert len(source["record_ids"]) == 7 and result["evidence_count"] == 1 + 7
    assert result["complete"] is True


def test_the_hours_read_keeps_the_order_the_caller_asked_for() -> None:
    service = reader(schedule_rows())
    result = service.get_office_facts(
        "registrar", ["hours", "email"], "release-1", identity_hash="identities-1")
    assert [p["key"] for p in result["properties"]] == ["hours", "email"]
    assert result["mapping_version"] == "entity-facts-4"


def test_hours_are_not_read_unless_asked_for() -> None:
    reads: list[str] = []

    class Counting(MemoryEntityFacts):
        @contextmanager
        def snapshot(self) -> Iterator[Snapshot]:
            with super().snapshot() as snap:
                def counted(entity: dict[str, Any]) -> list[dict[str, Any]]:
                    reads.append(entity["id"])
                    return snap.schedule_reader(entity)

                yield replace(snap, schedule_reader=counted)

    service = Counting(dataset_version="release-1", identity_hash="identities-1",
                       entities=[office()], contacts=[contact()], schedules=schedule_rows(),
                       now=lambda: NOW)
    plain = service.get_office_facts(
        "registrar", ["email"], "release-1", identity_hash="identities-1")
    assert reads == [] and [p["key"] for p in plain["properties"]] == ["email"]
    assert plain["evidence_count"] == 1  # Schedule rows are not counted when not read.
    assert all(s["collection"] == "contacts" for s in plain["sources"])
    hours(service)
    assert reads == ["registrar"]


def test_an_office_with_no_schedule_link_has_unknown_hours() -> None:
    service = reader([], office(schedules=[]))
    prop = hours_property(hours(service))
    assert prop["status"] == "unknown" and prop["values"] == []
    assert hours(service)["complete"] is True  # No link means unknown, not missing evidence.


def test_a_linked_schedule_that_is_missing_is_called_out_only_when_hours_are_asked() -> None:
    service = reader(schedule_rows()[:6])  # Sunday's record is linked but absent.
    result = hours(service)
    assert result["complete"] is False
    missing = "Linked schedule evidence is missing: campus-hours/Registrar:Sunday."
    assert missing in result["caveats"]
    contact_only = service.get_office_facts(
        "registrar", ["email"], "release-1", identity_hash="identities-1")
    assert contact_only["complete"] is True


def test_two_named_schedules_in_the_same_window_are_separate_answers_not_a_conflict() -> None:
    desk = {d: "9:00am-9:00pm" for d in DAYS}
    rows = (schedule_rows("Library (Main Building)", {d: "7:45am-12:00am" for d in DAYS},
                          note="Fall Semester", tag="m")
            + schedule_rows("Research Help Desk", desk, note="Fall Semester", tag="r"))
    entity = office(schedules=["Library (Main Building)", "Research Help Desk"])
    prop = hours_property(hours(reader(rows, entity)))
    assert prop["status"] == "known"
    assert [v["value"]["schedule"] for v in prop["values"]] == [
        "Library (Main Building)", "Research Help Desk"]


def test_the_same_schedule_with_different_hours_in_overlapping_windows_is_a_conflict() -> None:
    other = {d: "9:00am-5:00pm" for d in DAYS}
    rows = schedule_rows(tag="a") + schedule_rows(week=other, tag="b", since="2026-09-01",
                                                  until="2026-12-31")
    prop = hours_property(hours(reader(rows)))
    assert prop["status"] == "conflicting" and len(prop["values"]) == 2


def test_the_same_schedule_in_disjoint_windows_is_multiple_not_a_conflict() -> None:
    summer = {d: "8:00am-5:15pm" for d in DAYS[:4]} | {"Friday": "Closed"}
    rows = schedule_rows(tag="a") + schedule_rows(week=summer, tag="b", since="2027-06-01",
                                                  until="2027-08-15")
    prop = hours_property(hours(reader(rows)))
    assert prop["status"] == "multiple" and len(prop["values"]) == 2


def test_two_readings_of_one_day_in_one_window_are_a_conflict() -> None:
    changed = dict(WEEK, Friday="8:30am-3:00pm")
    rows = schedule_rows(tag="a") + schedule_rows(week={"Friday": changed["Friday"]}, tag="b")
    prop = hours_property(hours(reader(rows)))
    assert prop["status"] == "conflicting"
    fridays = sorted(next(d["hours"] for d in v["value"]["days"] if d["day"] == "Friday")
                     for v in prop["values"])
    assert fridays == ["8:30am-3:00pm", "8:30am-4:30pm"]


def test_a_conflict_on_a_later_weekday_keeps_every_reading_with_its_own_source() -> None:
    # Monday has one record and Friday two: both readings start from Monday's row.
    changed = {"Friday": "8:30am-3:00pm"}
    rows = schedule_rows(tag="a") + schedule_rows(week=changed, tag="b")
    result = hours(reader(rows))
    prop = hours_property(result)
    assert prop["status"] == "conflicting" and len(prop["values"]) == 2
    source_ids = [s["id"] for s in result["sources"] if s["collection"] == "campus_hours"]
    assert len(source_ids) == len(set(source_ids)) == 2
    assertion_ids = [a["id"] for a in prop["assertions"]]
    assert len(assertion_ids) == len(set(assertion_ids)) == 2
    text = render_facts(result).text  # Rendering a conflict must not raise.
    assert "conflicting published records" in text and text.count("Published value:") == 2


def test_identical_records_for_one_day_are_one_reading() -> None:
    rows = schedule_rows(tag="a") + schedule_rows(tag="b")
    prop = hours_property(hours(reader(rows)))
    assert prop["status"] == "known" and len(prop["values"]) == 1
    assert len(prop["values"][0]["assertion_ids"]) == 1  # One reading, however many copies.


def test_a_window_that_has_ended_or_not_begun_is_marked_and_not_current() -> None:
    expired = hours(reader(schedule_rows()), as_of=datetime(2027, 1, 5, tzinfo=UTC))
    assert expired["sources"][-1]["validity"] == "expired"
    future = hours(reader(schedule_rows(since="2027-01-20", until="2027-05-20")))
    assert future["sources"][-1]["validity"] == "future"
    # The first and last published days still count as current (campus calendar days).
    first = hours(reader(schedule_rows()), as_of=datetime(2026, 8, 26, 4, 30, tzinfo=UTC))
    last = hours(reader(schedule_rows()), as_of=datetime(2026, 12, 16, 20, tzinfo=UTC))
    assert first["sources"][-1]["validity"] == last["sources"][-1]["validity"] == "current"


def test_an_old_capture_is_stale_and_says_so_when_rendered() -> None:
    old = datetime(2026, 3, 1, tzinfo=UTC)  # More than 180 days before the turn.
    result = hours(reader(schedule_rows(collected=old)))
    assert result["sources"][-1]["freshness"] == "stale"
    text = render_facts(result).text
    assert "dated observation; current value unverified" in text
    assert render_facts(result).supported is False


def test_an_unusable_citation_is_not_shown_as_a_current_value() -> None:
    rendered = render_facts(hours(reader(schedule_rows(url="http://insecure.example/"))))
    # The record's own page link is unusable, so the source's canonical page is cited instead.
    assert "https://example.edu/campus-hours/" in rendered.text
    nothing = schedule_rows(url=None)
    for row in nothing:
        row["canonical_url"] = None
    unciteable = render_facts(hours(reader(nothing)))
    assert "lack a usable secure citation" in unciteable.text and unciteable.supported is False


def test_rendering_groups_days_with_the_same_hours_and_keeps_the_published_note() -> None:
    rendered = render_facts(hours(reader(schedule_rows())))
    assert "**Registrar**" in rendered.text
    assert ("Hours: Monday to Friday: 8:30am-4:30pm; Saturday and Sunday: Hours unavailable. "
            "Published note: Fall/Spring Hours: 8:30 A.M. - 4:30 P.M. Monday - Friday"
            ) in rendered.text
    assert "published validity 2026-08-26 through 2026-12-16" in rendered.text
    assert rendered.supported is True and rendered.complete is True
    assert [c["url"] for c in rendered.citations] == ["https://example.edu/registrar/"]
    assert rendered.citations[0]["collection"] == "campus_hours"


def test_rendering_names_a_schedule_only_when_it_adds_something() -> None:
    own = render_facts(hours(reader(schedule_rows("Center for Student Involvement (CSI)"),
                                    office(name="Center for Student Involvement",
                                           schedules=["Center for Student Involvement (CSI)"]))))
    assert "Center for Student Involvement \\(CSI\\). " not in own.text
    rows = (schedule_rows("Library (Main Building)", {d: "7:45am-12:00am" for d in DAYS},
                          tag="m")
            + schedule_rows("Research Help Desk", {d: "9:00am-9:00pm" for d in DAYS}, tag="r"))
    entity = office(name="Library", schedules=["Library (Main Building)", "Research Help Desk"])
    both = render_facts(hours(reader(rows, entity))).text
    assert "Hours: Library \\(Main Building\\). Monday to Sunday: 7:45am-12:00am" in both
    assert "Hours: Research Help Desk. Monday to Sunday: 9:00am-9:00pm" in both
    renamed = render_facts(hours(reader(schedule_rows("Game Lab"), office(
        name="Library", schedules=["Game Lab"])))).text
    assert "Hours: Game Lab. Monday to Friday: 8:30am-4:30pm" in renamed


def test_a_weekday_with_no_record_is_never_covered_by_a_span() -> None:
    week = {"Monday": "9am-5pm", "Tuesday": "9am-5pm", "Thursday": "9am-5pm", "Saturday": "9am-5pm"}
    text = render_facts(hours(reader(schedule_rows(week=week)))).text
    assert "Monday and Tuesday: 9am-5pm; Thursday: 9am-5pm; Saturday: 9am-5pm" in text
    assert "Monday to" not in text and "Wednesday" not in text
    gap = {"Monday": "9am-5pm", "Wednesday": "9am-5pm", "Friday": "9am-5pm"}
    assert "Monday: 9am-5pm; Wednesday: 9am-5pm; Friday: 9am-5pm" in render_facts(
        hours(reader(schedule_rows(week=gap)))).text


def test_a_qualified_schedule_name_is_shown_even_for_one_schedule() -> None:
    qualified = render_facts(hours(reader(
        schedule_rows("Library Research Help Desk", {d: "9am-9pm" for d in DAYS}, tag="r"),
        office(name="Library", schedules=["Library Research Help Desk"])))).text
    assert "Hours: Library Research Help Desk. Monday to Sunday: 9am-9pm" in qualified


def test_text_is_quoted_not_executed() -> None:
    rows = schedule_rows(note="<b>Open</b> [x](http://evil.example) *now*")
    text = render_facts(hours(reader(rows))).text
    assert "<b>" not in text and "&lt;b&gt;" in text
    assert "\\[x\\]\\(http://evil.example\\)" in text and "\\*now\\*" in text


@pytest.mark.parametrize("bad", [{"schedule": ""}, {"day": ""}, {"name": ""}, {"id": ""}])
def test_a_schedule_row_missing_its_name_day_hours_or_id_fails_the_read(
        bad: dict[str, Any]) -> None:
    rows = schedule_rows()
    rows[0].update(bad)
    with pytest.raises(EvidenceUnavailable):
        hours(reader(rows))


def test_duplicate_schedule_row_ids_fail_the_read() -> None:
    rows = schedule_rows()
    rows[1]["id"] = rows[0]["id"]
    with pytest.raises(EvidenceUnavailable, match="Duplicate or missing schedule"):
        hours(reader(rows))


def test_a_row_that_is_not_linked_to_the_office_fails_the_read() -> None:
    class Leaky(MemoryEntityFacts):
        def snapshot(self) -> Any:
            outer = super().snapshot()

            class Wrapped:
                def __enter__(self_inner) -> Any:  # noqa: N805
                    snap = outer.__enter__()
                    stray = schedule_rows("Elsewhere", tag="z")[:1]
                    return SimpleNamespace(
                        **{**snap.__dict__, "schedule_reader": lambda entity: stray})

                def __exit__(self_inner, *args: Any) -> None:  # noqa: N805
                    outer.__exit__(*args)

            return Wrapped()

    service = Leaky(dataset_version="release-1", identity_hash="identities-1",
                    entities=[office()], contacts=[contact()], schedules=[], now=lambda: NOW)
    with pytest.raises(EvidenceUnavailable, match="no exact canonical identity link"):
        hours(service)


def test_too_many_schedule_rows_fail_instead_of_being_cut_short() -> None:
    # Each link may name up to 128 records; two links can still pass the link check and then
    # return more rows than one read allows.
    rows = []
    for n in range(20):
        rows += schedule_rows(f"Schedule {n}", tag=f"s{n}")
    entity = office()
    entity["links"] = [
        entity["links"][0],
        {"collection": "campus_hours", "source_key": "campus-hours",
         "source_record_keys": [f"Schedule {n}:{day}" for n in range(10) for day in DAYS]},
        {"collection": "campus_hours", "source_key": "campus-hours",
         "source_record_keys": [f"Schedule {n}:{day}" for n in range(10, 20) for day in DAYS]},
    ]
    with pytest.raises(EvidenceUnavailable, match="exceeds the bounded read"):
        hours(reader(rows, entity))


def test_schedule_links_are_validated_and_may_be_shared() -> None:
    link = {"collection": "campus_hours", "source_key": "campus-hours",
            "source_record_keys": ["Registrar:Monday"]}
    shared = [
        {"id": "a", "kind": "office", "name": "A", "aliases": [], "links": [deepcopy(link)]},
        {"id": "b", "kind": "office", "name": "B", "aliases": [], "links": [deepcopy(link)]},
    ]
    assert len(validate_entities(shared)) == 2
    for broken in ({"source_record_keys": "Registrar:Monday"}, {"source_key": ""},
                   {"source_record_keys": [1]}, {"source_record_keys": ["x"] * 129}):
        entities = [{**shared[0], "links": [{**link, **broken}]}]
        with pytest.raises(EvidenceUnavailable, match="schedule identity link"):
            validate_entities(entities)
    assert linked_schedules(shared[0], schedule_rows("Elsewhere")[:1]) == []
    assert len(linked_schedules(shared[0], schedule_rows()[:2])) == 1  # Only Monday is linked.


def test_postgres_reads_schedules_by_exact_link_only_when_hours_are_asked(
        monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, Any]] = []
    entity = office()
    rows = schedule_rows()

    class Cursor:
        def __init__(self, result: list[dict[str, Any]]) -> None:
            self.result = result

        def fetchall(self) -> list[dict[str, Any]]:
            return deepcopy(self.result)

    class Connection:
        def __enter__(self) -> "Connection":
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def execute(self, query: str, params: Any = None) -> Cursor:
            calls.append((query, params))
            if "dataset_versions v" in query:
                return Cursor([{
                    "dataset_id": "dataset-uuid", "dataset_version": "release-1",
                    "identity_hash": "identities-1",
                    "registry": {"schema_version": 1, "entities": [entity]},
                    "alias_sources": []}])
            if "campus_contacts c" in query:
                return Cursor([contact()])
            if "campus_hours h" in query:
                return Cursor(rows)
            return Cursor([])

    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: Connection())
    service = PostgresEntityFacts("postgresql://example.invalid/test")
    result = service.get_office_facts("registrar", ["hours"], "release-1")
    assert hours_property(result)["status"] == "known"
    query, params = next(call for call in calls if "campus_hours h" in call[0])
    assert "l.source_record_keys ? h.source_record_key" in query
    assert "to_jsonb(h) ->> 'source_url'" in query  # Tolerates a database without the column.
    assert "Registrar:Monday" in params[0] and params[1] == "dataset-uuid" and params[2] == 129
    calls.clear()
    service.get_office_facts("registrar", ["email"], "release-1")
    assert not [call for call in calls if "campus_hours h" in call[0]]
