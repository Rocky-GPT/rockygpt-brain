"""The how-to tasks a release holds and the answer cards joined from them, for the dev UI's
Answer Cards page.

An answer card is one task a student asks about ("Apply for a parking permit"): the office that
handles it, the page whose steps are said and the other pages that list it. The night job lists
tasks page by page (an office page's how-to entries), then joins the same task across pages into
one card by rules (retrieval/card_join.py, scripts/build_answer_cards.py) and writes the cards
beside the release. This reader shows both: every task as its page lists it, under its page, with
the nodes whose links hold that page; and the cards, when the release has them.

A task is keyed as the meaning index keys it, by its page's record id and "e<n>" (its place in the
page's list, see `_procedure_units`), so a task here is exactly a unit a how-to read can offer. A
page no node links is not in a release graph, so it is not listed. This reads, never answers:
nothing here feeds a chat reply.
"""

from datetime import date
from typing import Any
from urllib.parse import urlsplit

from rockygpt_brain.retrieval.card_join import Task
from rockygpt_brain.retrieval.entity_facts import (
    DatasetChanged,
    EntityFacts,
    InvalidFactRequest,
    Snapshot,
    _json_safe,
    _procedure_units,
    linked_rows,
    old_page,
    page_updated,
    unit_over,
)
from rockygpt_brain.retrieval.projection import clean

PAGES = "office_sections"
# What a task's page says about it, beside its name and steps.
_TASK_FIELDS = ("applies_to", "handled_by", "term", "section")


def _pin(snapshot: Snapshot, dataset_version: str | None, identity_hash: str | None) -> None:
    if (dataset_version is not None and dataset_version != snapshot.dataset_version) or (
            identity_hash is not None and identity_hash != snapshot.identity_hash):
        raise DatasetChanged("The active dataset changed; open the current list.")


def _links_pages(entity: dict[str, Any]) -> bool:
    return any(link.get("collection") == PAGES for link in entity.get("links") or [])


def _pages(snapshot: Snapshot, entity: dict[str, Any]) -> list[dict[str, Any]]:
    return linked_rows(entity, snapshot.record_reader(entity, PAGES), PAGES)


def _tasks(row: dict[str, Any]) -> list[dict[str, Any]]:
    """A page's tasks, each with its steps and the page's own words behind every step. Which
    entries count, and their keys, are `_procedure_units`'s: the units a how-to read offers."""
    entries = row.get("howto_entries")
    tasks: list[dict[str, Any]] = []
    for key, unit, _, _ in _procedure_units(row):
        if not key.startswith("e") or not isinstance(entries, list):
            continue
        steps: list[dict[str, Any]] = []
        for step in entries[int(key[1:])]["steps"]:
            text = clean(step.get("text")) if isinstance(step, dict) else None
            if isinstance(text, str) and text:
                quote = clean(step.get("quote"))
                steps.append({"text": text,
                              "quote": quote if isinstance(quote, str) and quote else None})
        tasks.append({"key": key, "task": unit["heading"],
                      **{field: unit[field] for field in _TASK_FIELDS if unit.get(field)},
                      "steps": steps})
    return tasks


def _page(row: dict[str, Any]) -> dict[str, Any]:
    url = row.get("url") if isinstance(row.get("url"), str) else None
    updated = page_updated(row)
    return {"record_id": str(row["id"]), "url": url,
            "path": (urlsplit(url).path.rstrip("/") or "/") if url else None,
            "updated": updated.isoformat() if updated else None,
            "collected_at": _json_safe(row.get("collected_at"))}


def _node(entity: dict[str, Any]) -> dict[str, Any]:
    return {"entity_id": entity["id"], "name": entity["name"], "kind": entity["kind"]}


def listing(facts: EntityFacts, *, dataset_version: str | None = None,
            identity_hash: str | None = None) -> dict[str, Any]:
    """Every task of the active release, under its page: the page, the nodes that link it, and
    each task's name, who it is for, who the page says handles it, its term and its step count.
    `nodes` lists every node that links a page with a task, and every node a card names as its
    owner. `cards` holds the release's answer cards (the day they were built, the rules'
    settings, and each card's status, flags, owners and tasks by page record and key, the winner
    first); it is null for a release built without them."""
    pages: dict[str, dict[str, Any]] = {}
    nodes: dict[str, dict[str, Any]] = {}
    pages_read: set[str] = set()
    steps = 0
    with facts.snapshot() as snapshot:
        _pin(snapshot, dataset_version, identity_hash)
        version, identity = snapshot.dataset_version, snapshot.identity_hash
        for entity in snapshot.entities:
            if not _links_pages(entity):
                continue
            for row in _pages(snapshot, entity):
                record_id = str(row["id"])
                pages_read.add(record_id)
                page = pages.get(record_id)
                if page is None:
                    tasks = _tasks(row)
                    steps += sum(len(item["steps"]) for item in tasks)
                    page = pages[record_id] = {
                        **_page(row), "nodes": [],
                        "tasks": [{**item, "steps": len(item["steps"])} for item in tasks]}
                if page["tasks"] and entity["id"] not in page["nodes"]:
                    page["nodes"].append(entity["id"])
                    node = nodes.setdefault(entity["id"], {**_node(entity), "pages": 0,
                                                           "tasks": 0})
                    node["pages"] += 1
                    node["tasks"] += len(page["tasks"])
        cards = facts.answer_cards()
        if cards is not None and (cards.get("dataset_version"), cards.get("identity_hash")) != (
                version, identity):
            cards = None
        if cards is not None:
            # A card's owner can be an office none of whose pages lists the task (the page says
            # "Dining Services handles it"): it is named here too, with no page and no task.
            owning = {node for found in cards["cards"] for node in found.get("owners") or []}
            for entity in snapshot.entities:
                if entity["id"] in owning and entity["id"] not in nodes:
                    nodes[entity["id"]] = {**_node(entity), "pages": 0, "tasks": 0}
    with_tasks = sorted((page for page in pages.values() if page["tasks"]),
                        key=lambda page: (page["url"] or "", page["record_id"]))
    totals = {"pages_read": len(pages_read), "pages_with_tasks": len(with_tasks),
              "tasks": sum(len(page["tasks"]) for page in with_tasks), "steps": steps}
    return {"dataset_version": version, "identity_hash": identity,
            "cards": None if cards is None else {
                "built_on": cards.get("built_on"), "rules": cards.get("rules") or {},
                "items": cards["cards"]},
            "totals": totals,
            "nodes": sorted(nodes.values(), key=lambda n: (n["name"].casefold(), n["entity_id"])),
            "pages": with_tasks}


def join_input(facts: EntityFacts, today: date,
               ) -> tuple[str, str, list[Task], dict[str, list[str]]]:
    """What the cards are joined from (card_join.build): the active release, its tasks in the
    order of their pages' record ids, and each node's names (its name and its other names) for
    every office and every node that links a page. Whether a task's term is over and whether its
    page is old are as of `today`, by the rules a how-to read uses."""
    found: dict[str, list[dict[str, Any]]] = {}
    linking: dict[str, list[str]] = {}
    names: dict[str, list[str]] = {}
    with facts.snapshot() as snapshot:
        for entity in snapshot.entities:
            links = _links_pages(entity)
            if links or entity.get("kind") == "office":
                names[entity["id"]] = [entity["name"], *(entity.get("aliases") or [])]
            if not links:
                continue
            for row in _pages(snapshot, entity):
                record_id = str(row["id"])
                if record_id not in found:
                    updated = page_updated(row)
                    steps = {item["key"]: len(item["steps"]) for item in _tasks(row)}
                    found[record_id] = [
                        {"record_id": record_id, "key": key, "name": unit["heading"],
                         "url": str(row.get("url") or ""), "steps": steps[key],
                         "applies_to": unit.get("applies_to"),
                         "handled_by": unit.get("handled_by"), "section": unit.get("section"),
                         "updated": updated, "over": unit_over(row, unit, today),
                         "old": old_page(updated, today)}
                        for key, unit, _, _ in _procedure_units(row) if key.startswith("e")]
                nodes = linking.setdefault(record_id, [])
                if entity["id"] not in nodes:
                    nodes.append(entity["id"])
        tasks = [Task(**item, nodes=tuple(linking[record_id]))
                 for record_id in sorted(found) for item in found[record_id]]
        return snapshot.dataset_version, snapshot.identity_hash, tasks, names


def task(facts: EntityFacts, node_id: str, record_id: str, key: str, *,
         dataset_version: str | None = None, identity_hash: str | None = None) -> dict[str, Any]:
    """One task in full: its steps, each beside the page's own words, its page with the other
    tasks listed there, and every node that links the page. `node_id` is any node the listing
    names for the page, so only that node's pages are read."""
    with facts.snapshot() as snapshot:
        _pin(snapshot, dataset_version, identity_hash)
        entity = next((e for e in snapshot.entities if e["id"] == node_id), None)
        if entity is None or not _links_pages(entity):
            raise InvalidFactRequest("This node links no office page in the release.")
        row = next((r for r in _pages(snapshot, entity) if str(r["id"]) == record_id), None)
        if row is None:
            raise InvalidFactRequest("This page is not linked from that node.")
        tasks = _tasks(row)
        chosen = next((item for item in tasks if item["key"] == key), None)
        if chosen is None:
            raise InvalidFactRequest("This page lists no such task.")
        linking = [_node(e) for e in snapshot.entities if _links_pages(e)
                   and linked_rows(e, [row], PAGES)]
        return {"dataset_version": snapshot.dataset_version,
                "identity_hash": snapshot.identity_hash, "task": chosen,
                "page": {**_page(row),
                         "other_tasks": [{"key": item["key"], "task": item["task"]}
                                         for item in tasks if item["key"] != key]},
                "nodes": sorted(linking, key=lambda n: (n["name"].casefold(), n["entity_id"]))}
