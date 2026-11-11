"""The dev UI's Answer Cards reader lists the release's how-to tasks page by page."""

import array
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from rockygpt_brain.api.app import create_app
from rockygpt_brain.engine import ChatEngine
from rockygpt_brain.retrieval import MemoryEntityFacts, card_join
from rockygpt_brain.retrieval.answer_cards import join_input, listing, task
from rockygpt_brain.retrieval.entity_facts import (
    DatasetChanged,
    InvalidFactRequest,
    _procedure_units,
)
from rockygpt_brain.retrieval.graph_store import (
    POINTER,
    GraphOnlyFacts,
    publish_release_graph,
)
from rockygpt_brain.retrieval.meaning import MEANING_SUFFIX, VECTORS_SUFFIX

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
    assert found["cards"] is None  # this release was built without cards
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


TODAY = date(2026, 11, 11)


def test_the_cards_are_joined_from_every_task_and_every_nodes_names() -> None:
    version, identity, tasks, names = join_input(facts(), TODAY)
    assert (version, identity) == ("release-1", "identities-1")
    assert [(t.record_id, t.key, t.name, t.steps, t.nodes) for t in tasks] == [
        ("page-faq", "e0", "Find your orientation date", 1, (ORIENTATION,)),
        ("page-permits", "e0", "Get a student parking permit", 2, (SAFETY, ORIENTATION)),
        ("page-permits", "e2", "Appeal a parking ticket", 1, (SAFETY, ORIENTATION))]
    permit = tasks[1]
    assert permit.url == PERMITS and permit.updated == date(2026, 8, 17)
    assert permit.applies_to == "Students who park on campus" and permit.handled_by is None
    assert permit.over is None and not permit.old and tasks[0].updated is None
    # An office none of whose pages is linked can still be named as the one that handles a task.
    assert names == {SAFETY: ["Public Safety"], ORIENTATION: ["Orientation"],
                     REGISTRAR: ["Registrar"]}
    # A term that has ended, and a page three years old, as of the day the cards are built.
    later = join_input(facts(), date(2029, 9, 1))[2][1]
    assert later.over == "Fall 2026" and later.old


class WithCards(MemoryEntityFacts):
    cards: dict[str, Any] | None = None

    def answer_cards(self) -> dict[str, Any] | None:
        return self.cards


def test_the_list_carries_the_releases_cards() -> None:
    source = facts()
    held = WithCards(dataset_version=source.dataset_version, identity_hash=source.identity_hash,
                     entities=source.entities, contacts=[], records=source.records,
                     now=lambda: NOW)
    cards = [{"id": "c1", "status": "ready", "flags": [], "notes": ["owner_has_no_page"],
              "owners": [REGISTRAR],
              "members": [{"record_id": "page-permits", "key": "e0", "role": "winner"}]}]
    held.cards = card_join.cards_file("release-1", "identities-1", TODAY, cards)
    found = listing(held)
    assert found["cards"] == {"built_on": "2026-11-11", "items": cards,
                              "rules": {"join_mean": card_join.JOIN_MEAN,
                                        "join_least": card_join.JOIN_LEAST,
                                        "big_group": card_join.BIG_GROUP}}
    # The owner is named even though none of its pages lists a task.
    assert {"entity_id": REGISTRAR, "name": "Registrar", "kind": "office", "pages": 0,
            "tasks": 0} in found["nodes"]
    assert found["pages"] == listing(facts())["pages"]
    # Cards built for another release are never shown.
    held.cards = card_join.cards_file("release-0", "identities-1", TODAY, cards)
    assert listing(held)["cards"] is None


def test_the_build_writes_the_cards_beside_the_release_and_the_reader_finds_them(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import build_answer_cards as build

    folder = tmp_path / "published"
    publish_release_graph(facts(), folder)
    release_file = folder / json.loads((folder / POINTER).read_text())["file"]
    assert build.main(["--dir", str(folder)]) == 1  # no meaning index yet
    # The two parking tasks' questions mean different things; orientation's is its own.
    units = [["page-faq", "e0", [ORIENTATION]], ["page-permits", "e0", [SAFETY]],
             ["page-permits", "e2", [SAFETY]]]
    meta = {"dataset_version": "release-1", "identity_hash": "identities-1", "model": "test",
            "dimensions": 3, "units": units, "owner": [0, 0, 1, 1, 2, 2],
            "offsets": [0, -1, 0, -1, 0, -1]}
    vectors = [[1, 0, 0], [1, 0, 0], [0, 1, 0], [0, 1, 0], [0, 0, 1], [0, 0, 1]]
    release_file.with_suffix(MEANING_SUFFIX).write_text(json.dumps(meta))
    release_file.with_suffix(VECTORS_SUFFIX).write_bytes(
        array.array("f", [value for vector in vectors for value in vector]).tobytes())
    assert build.main(["--dir", str(folder), "--today", "2026-11-11"]) == 0
    assert "3 cards from 3 tasks on 2 pages" in capsys.readouterr().out
    served = GraphOnlyFacts(folder, now=lambda: NOW)
    held = served.answer_cards()
    assert held is not None and held["built_on"] == "2026-11-11"
    assert [[(m["record_id"], m["key"], m["role"]) for m in found["members"]]
            for found in held["cards"]] == [[("page-faq", "e0", "winner")],
                                            [("page-permits", "e0", "winner")],
                                            [("page-permits", "e2", "winner")]]
    # The undated orientation page is flagged; Public Safety and Orientation both link the
    # permits page, so its tasks have both as owners, together.
    assert [found["status"] for found in held["cards"]] == ["needs_look", "ready", "ready"]
    assert held["cards"][1]["owners"] == sorted([SAFETY, ORIENTATION])
    assert listing(served)["cards"]["items"] == held["cards"]
    # A meaning index built for another release stops the build.
    release_file.with_suffix(MEANING_SUFFIX).write_text(
        json.dumps({**meta, "dataset_version": "release-0"}))
    assert build.main(["--dir", str(folder)]) == 1


def test_an_index_build_joins_its_cards_and_never_fails_for_them(
        tmp_path: Path, capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch) -> None:
    import build_answer_cards
    import build_meaning_index

    folder = tmp_path / "published"
    publish_release_graph(facts(), folder)
    release_file = folder / json.loads((folder / POINTER).read_text())["file"]
    cards_file = release_file.with_suffix(card_join.CARDS_SUFFIX)
    # No index beside the release: the cards are not built, and the index build is told so
    # without failing.
    assert build_meaning_index.join_answer_cards(folder) is False
    said = capsys.readouterr()
    assert "::warning::The answer cards were not built" in said.out
    assert "No meaning index" in said.err and not cards_file.exists()
    # With the index written, the same call builds them.
    meta = {"dataset_version": "release-1", "identity_hash": "identities-1", "model": "test",
            "dimensions": 3, "units": [["page-faq", "e0", [ORIENTATION]]], "owner": [0, 0],
            "offsets": [0, -1]}
    release_file.with_suffix(MEANING_SUFFIX).write_text(json.dumps(meta))
    release_file.with_suffix(VECTORS_SUFFIX).write_bytes(
        array.array("f", [1, 0, 0, 1, 0, 0]).tobytes())
    assert build_meaning_index.join_answer_cards(folder) is True
    assert "3 cards from 3 tasks on 2 pages" in capsys.readouterr().out and cards_file.exists()

    # Whatever goes wrong in the join is said and kept from the index build.
    def broken(argv: list[str]) -> int:
        raise ValueError("a card could not be ranked")

    monkeypatch.setattr(build_answer_cards, "main", broken)
    assert build_meaning_index.join_answer_cards(folder) is False
    said = capsys.readouterr()
    assert "ValueError: a card could not be ranked" in said.err and "::warning::" in said.out
