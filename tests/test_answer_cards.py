"""The dev UI's Answer Cards reader lists the release's how-to tasks page by page."""

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from rockygpt_brain.api.app import create_app
from rockygpt_brain.engine import ChatEngine
from rockygpt_brain.retrieval import MemoryEntityFacts
from rockygpt_brain.retrieval.answer_cards import listing, task
from rockygpt_brain.retrieval.entity_facts import (
    DatasetChanged,
    InvalidFactRequest,
    _procedure_units,
)

NOW = datetime(2026, 11, 11, 12, tzinfo=UTC)
ASKED = {"x-rockygpt-diagnostics": "1"}
SAFETY = "11111111-1111-1111-1111-111111111111"
ORIENTATION = "22222222-2222-2222-2222-222222222222"
REGISTRAR = "33333333-3333-3333-3333-333333333333"
PERMITS = "https://www.ramapo.edu/publicsafety/parking-permits/"


def page(record: str, url: str, entries: list[dict[str, Any]] | None = None,
         **extra: Any) -> dict[str, Any]:
    return {"id": f"page-{record}", "source_key": "office-pages", "source_record_key": record,
            "url": url, "collected_at": NOW, "freshness_sla_hours": 168, "content_hash": "h",
            "sections": [{"heading": "Permits", "text": "Register online. Pick up at check-in."}],
            **({"howto_entries": entries} if entries is not None else {}), **extra}


def node(entity_id: str, name: str, records: list[str]) -> dict[str, Any]:
    return {"id": entity_id, "kind": "office", "name": name, "aliases": [],
            "links": [{"collection": "office_sections", "source_key": "office-pages",
                       "source_record_keys": records}] if records else []}


PERMIT = {"task": "Get a student parking permit", "applies_to": "Students who park on campus",
          "handled_by": "", "term": "Fall 2026", "section": "Permits",
          "steps": [{"text": "Register online.", "quote": "Register online"},
                    {"text": "  ", "quote": "no words, so not a step"},
                    {"text": "Pick up at check-in.", "quote": "Pick up at check-in"}]}
APPEAL = {"task": "Appeal a parking ticket", "applies_to": "", "handled_by": "", "term": "",
          "section": "Permits", "steps": [{"text": "File the appeal form."}]}
NO_STEPS = {"task": "A task with no step is not listed", "steps": []}


def facts() -> MemoryEntityFacts:
    return MemoryEntityFacts(
        dataset_version="release-1", identity_hash="identities-1", contacts=[],
        entities=[node(SAFETY, "Public Safety", ["permits", "plain"]),
                  node(ORIENTATION, "Orientation", ["permits", "faq"]),
                  node(REGISTRAR, "Registrar", [])],
        records={"office_sections": [
            page("permits", PERMITS, [PERMIT, NO_STEPS, APPEAL],
                 page_modified_at="2026-08-17T14:00:00Z"),
            page("plain", "https://www.ramapo.edu/publicsafety/about/"),
            page("faq", "https://www.ramapo.edu/orientation/faq/",
                 [{"task": "Find your orientation date", "section": "Permits",
                   "steps": [{"text": "Check your email.", "quote": "Check your email"}]}]),
            # A page no node links is not in a release graph, so it is never listed.
            page("unlinked", "https://www.ramapo.edu/nobody/", [PERMIT])]},
        now=lambda: NOW)


def test_the_list_holds_every_task_under_its_page_with_the_nodes_that_link_it() -> None:
    found = listing(facts())
    assert (found["dataset_version"], found["identity_hash"]) == ("release-1", "identities-1")
    assert found["cards"] is None  # tasks are not joined into cards yet
    assert found["totals"] == {"pages_read": 3, "pages_with_tasks": 2, "tasks": 3, "steps": 4}
    assert [p["path"] for p in found["pages"]] == ["/orientation/faq",
                                                   "/publicsafety/parking-permits"]
    permits = found["pages"][1]
    assert permits["record_id"] == "page-permits" and permits["url"] == PERMITS
    assert permits["updated"] == "2026-08-17" and found["pages"][0]["updated"] is None
    assert permits["nodes"] == [SAFETY, ORIENTATION]
    assert permits["tasks"] == [
        {"key": "e0", "task": "Get a student parking permit", "term": "Fall 2026",
         "applies_to": "Students who park on campus", "section": "Permits", "steps": 2},
        {"key": "e2", "task": "Appeal a parking ticket", "section": "Permits", "steps": 1}]
    assert found["nodes"] == [
        {"entity_id": ORIENTATION, "name": "Orientation", "kind": "office", "pages": 2,
         "tasks": 3},
        {"entity_id": SAFETY, "name": "Public Safety", "kind": "office", "pages": 1, "tasks": 2}]


def test_a_task_has_the_key_a_how_to_read_gives_it() -> None:
    """The meaning index and every how-to answer name a task by its page and this key."""
    rows = facts().records["office_sections"]
    for found_page in listing(facts())["pages"]:
        row = next(r for r in rows if r["id"] == found_page["record_id"])
        offered = [(key, unit["heading"]) for key, unit, _, _ in _procedure_units(row)
                   if key.startswith("e")]
        assert [(t["key"], t["task"]) for t in found_page["tasks"]] == offered


def test_one_task_shows_each_step_beside_the_pages_words() -> None:
    found = task(facts(), ORIENTATION, "page-permits", "e0")
    assert found["task"] == {
        "key": "e0", "task": "Get a student parking permit", "term": "Fall 2026",
        "applies_to": "Students who park on campus", "section": "Permits",
        "steps": [{"text": "Register online.", "quote": "Register online"},
                  {"text": "Pick up at check-in.", "quote": "Pick up at check-in"}]}
    assert found["page"]["url"] == PERMITS and found["page"]["updated"] == "2026-08-17"
    assert found["page"]["other_tasks"] == [{"key": "e2", "task": "Appeal a parking ticket"}]
    assert [n["name"] for n in found["nodes"]] == ["Orientation", "Public Safety"]
    # A step the page gave no quote for says so, never a made-up one.
    assert task(facts(), SAFETY, "page-permits", "e2")["task"]["steps"] == [
        {"text": "File the appeal form.", "quote": None}]


@pytest.mark.parametrize(("node_id", "record_id", "key"), [
    (REGISTRAR, "page-permits", "e0"),   # a node that links no page
    ("nobody", "page-permits", "e0"),    # no such node
    (SAFETY, "page-faq", "e0"),          # a page that node does not link
    (SAFETY, "page-permits", "e1"),      # an entry with no step is not a task
    (SAFETY, "page-plain", "e0"),        # a page with no task list
])
def test_a_task_that_is_not_in_the_release_is_refused(node_id: str, record_id: str,
                                                      key: str) -> None:
    with pytest.raises(InvalidFactRequest):
        task(facts(), node_id, record_id, key)


def test_a_pin_to_another_release_is_refused() -> None:
    with pytest.raises(DatasetChanged):
        listing(facts(), dataset_version="release-0", identity_hash="identities-1")
    with pytest.raises(DatasetChanged):
        task(facts(), SAFETY, "page-permits", "e0", dataset_version="release-1",
             identity_hash="identities-0")


class NoGateway:
    async def complete(self, **_: Any) -> Any:  # pragma: no cover - the routes never call a model
        raise AssertionError("a dev route must not call the model")

    async def ready(self) -> bool:
        return True


def client(environment: str = "development") -> TestClient:
    return TestClient(create_app(ChatEngine(NoGateway(), facts()), service_token="",
                                 environment=environment))


def test_the_routes_serve_the_list_and_one_task_only_when_asked_in_development() -> None:
    one = f"/v1/dev/answer-cards/task?node_id={SAFETY}&record_id=page-permits&key=e0"
    with client() as http:
        assert http.get("/v1/dev/answer-cards").status_code == 404
        assert http.get(one).status_code == 404
        listed = http.get("/v1/dev/answer-cards", headers=ASKED)
        assert listed.status_code == 200 and listed.headers["cache-control"] == "no-store"
        assert listed.json() == listing(facts())
        opened = http.get(one, headers=ASKED)
        assert opened.status_code == 200
        assert opened.json() == task(facts(), SAFETY, "page-permits", "e0")
    application = create_app(ChatEngine(NoGateway(), facts()), service_token="t",  # noqa: S106
                             environment="production")
    with TestClient(application) as http:
        for path in ("/v1/dev/answer-cards", one):
            assert http.get(path, headers={**ASKED, "x-rockygpt-environment-token": "t"}
                            ).status_code == 404


def test_the_routes_say_why_a_request_is_refused() -> None:
    pins = "dataset_version=release-0&identity_hash=identities-1"
    with client() as http:
        assert http.get("/v1/dev/answer-cards?dataset_version=release-1",
                        headers=ASKED).json()["error"]["code"] == "invalid_request"
        changed = http.get(f"/v1/dev/answer-cards?{pins}", headers=ASKED)
        assert changed.status_code == 409 and changed.json()["error"]["code"] == "dataset_changed"
        task_url = f"/v1/dev/answer-cards/task?node_id={SAFETY}&record_id=page-permits"
        assert http.get(f"{task_url}&key=e0&{pins}", headers=ASKED).status_code == 409
        assert http.get(f"{task_url}&key=e1", headers=ASKED).status_code == 422
        assert http.get(f"{task_url}&key=0", headers=ASKED).status_code == 422


def test_the_list_is_read_once_for_a_release() -> None:
    """A release never changes: the second read is the kept list, not 2,500 pages read again."""
    engine = ChatEngine(NoGateway(), facts())
    reads = 0
    snapshot = engine.facts.snapshot

    def counted() -> Any:
        nonlocal reads
        reads += 1
        return snapshot()

    engine.facts.snapshot = counted  # type: ignore[method-assign]
    with TestClient(create_app(engine, service_token="", environment="development")) as http:
        first = http.get("/v1/dev/answer-cards", headers=ASKED).json()
        after_first = reads
        assert http.get("/v1/dev/answer-cards", headers=ASKED).json() == first
        # The second read only asks which release is active.
        assert reads == after_first + 1 and after_first >= 2
