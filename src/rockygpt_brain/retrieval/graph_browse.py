"""Browse the whole published campus graph for the dev UI: every node type and every link.

The chat lookup walks a small hand-made map (Ramapo -> Offices, Dining, Shuttle, Topics; see
campus_graph.py), because those are the nodes it can answer from. A person looking at the data
needs the opposite: everything the release holds. This reader is built only from the release's
own identity registry, so a node type or link type the data adds shows up here with no code
change. It reads, never answers: nothing here feeds a chat reply.

Node ids:
    ramapo          every node type with its count, and every link type with its count
    kind:<kind>     every node of one type, by name
    entity:<id>     one node: its names, the facts in the source records it links to, and its
                    links out and in
"""

from collections import Counter, defaultdict
from typing import Any

from rockygpt_brain.retrieval.entity_facts import (
    RECORD_COLLECTIONS,
    DatasetChanged,
    EntityFacts,
    InvalidFactRequest,
    Snapshot,
    _json_safe,
)

ROOT = "ramapo"
# The chat lookup's own view of a node, which shows its published evidence (contacts, hours,
# menus, a topic's offices, a route's trips). Only these kinds have one.
EVIDENCE_VIEW = {"office": "office", "venue": "office", "topic": "topic", "route": "route"}
# A node's card shows at most this many rows of each collection it links, with the full count.
MAX_CARD_ROWS = 50
# Bookkeeping columns a person reading the card has no use for.
_HIDDEN_FIELDS = {"id", "content_hash", "normalization_metadata",
                  "contact_observation_artifact_hash", "freshness_sla_hours", "source_key",
                  "source_record_key"}


def _facts(snapshot: Snapshot, entity: dict[str, Any]) -> list[dict[str, Any]]:
    """The rows behind every source record the node links, read with the same readers chat uses:
    a person's directory contact and faculty profile, an office's hours, a course's catalog row."""
    facts: list[dict[str, Any]] = []
    links = entity.get("links") or []
    for collection in dict.fromkeys(link.get("collection", "") for link in links):
        if collection == "contacts":
            rows = snapshot.contact_reader(entity)
        elif collection == "campus_hours":
            rows = snapshot.schedule_reader(entity)
        elif collection in RECORD_COLLECTIONS:
            rows = snapshot.record_reader(entity, collection)
        else:
            continue
        shown = [{key: _json_safe(value) for key, value in row.items()
                  if key not in _HIDDEN_FIELDS and value not in (None, "", [], {})}
                 for row in rows[:MAX_CARD_ROWS]]
        facts.append({"collection": collection, "total": len(rows), "rows": shown})
    return facts


def _relationships(entity: dict[str, Any]) -> list[dict[str, Any]]:
    found = entity.get("relationships")
    return [r for r in found if isinstance(r, dict) and isinstance(r.get("type"), str)] \
        if isinstance(found, list) else []


def _record_owners(entities: list[dict[str, Any]]) -> dict[tuple[str, str], list[str]]:
    """(collection, source record key) -> the entities that link that record. A link that points
    at a catalog record (a subject's course) is drawn to the record's own node through this."""
    owners: dict[tuple[str, str], list[str]] = defaultdict(list)
    for entity in entities:
        for link in entity.get("links") or []:
            for key in link.get("source_record_keys") or []:
                owners[(link.get("collection", ""), key)].append(entity["id"])
    return owners


def _targets(relationship: dict[str, Any], owners: dict[tuple[str, str], list[str]]
             ) -> tuple[list[str], dict[str, Any] | None]:
    """The node ids one link points to, or the record it names when no node owns that record."""
    target = relationship.get("target_entity_id")
    if isinstance(target, str):
        return [target], None
    record = relationship.get("target_record")
    if isinstance(record, dict):
        found = owners.get((record.get("collection", ""), record.get("source_record_key", "")))
        return (list(found), None) if found else ([], record)
    return [], None


def _node(entity: dict[str, Any]) -> dict[str, Any]:
    return {"id": f"entity:{entity['id']}", "label": entity["name"], "kind": entity["kind"],
            "entity_id": entity["id"]}


def _sources(relationship: dict[str, Any]) -> list[str]:
    return sorted({e["source_url"] for e in relationship.get("evidence") or []
                   if isinstance(e, dict) and isinstance(e.get("source_url"), str)})


def browse(facts: EntityFacts, node_id: str, *, dataset_version: str | None = None,
           identity_hash: str | None = None) -> dict[str, Any]:
    with facts.snapshot() as snapshot:
        if (dataset_version is not None and dataset_version != snapshot.dataset_version) or (
                identity_hash is not None and identity_hash != snapshot.identity_hash):
            raise DatasetChanged("The active dataset changed; open the current root.")
        entities = list(snapshot.entities)
        version, identity = snapshot.dataset_version, snapshot.identity_hash
        wanted = next((e for e in entities if node_id == f"entity:{e['id']}"), None)
        card_facts = _facts(snapshot, wanted) if wanted is not None else []
    by_id = {entity["id"]: entity for entity in entities}
    owners = _record_owners(entities)
    root = {"id": ROOT, "label": "Ramapo", "kind": "root"}
    result: dict[str, Any] = {"dataset_version": version, "identity_hash": identity}

    if node_id == ROOT:
        kinds = Counter(entity["kind"] for entity in entities)
        links: Counter[tuple[str, str, str]] = Counter()
        unresolved: Counter[str] = Counter()
        for entity in entities:
            for relationship in _relationships(entity):
                ids, record = _targets(relationship, owners)
                for target in ids:
                    links[(entity["kind"], relationship["type"],
                           by_id[target]["kind"] if target in by_id else "missing")] += 1
                if record is not None:
                    unresolved[relationship["type"]] += 1
        return {**result, "node": root, "path": [root],
                "kinds": [{"id": f"kind:{kind}", "label": kind, "kind": "type", "count": count}
                          for kind, count in sorted(kinds.items(), key=lambda k: (-k[1], k[0]))],
                "link_types": [{"from": a, "type": t, "to": b, "count": n}
                               for (a, t, b), n in sorted(links.items(),
                                                          key=lambda k: (-k[1], k[0]))],
                "unlinked_targets": dict(sorted(unresolved.items())),
                "entity_count": len(entities)}

    if node_id.startswith("kind:"):
        kind = node_id.removeprefix("kind:")
        members = sorted((e for e in entities if e["kind"] == kind),
                         key=lambda e: (e["name"].casefold(), e["id"]))
        if not members:
            raise InvalidFactRequest("This node type is not in the release.")
        listed: dict[str, Any] = {"id": node_id, "label": kind, "kind": "type"}
        return {**result, "node": listed, "path": [root, listed],
                "children": [_node(e) for e in members]}

    if node_id.startswith("entity:"):
        chosen = by_id.get(node_id.removeprefix("entity:"))
        if chosen is None:
            raise InvalidFactRequest("This node is not in the release.")
        outgoing: list[dict[str, Any]] = []
        for relationship in _relationships(chosen):
            ids, record = _targets(relationship, owners)
            for target in ids:
                outgoing.append({"type": relationship["type"],
                                 "node": _node(by_id[target]) if target in by_id else None,
                                 "missing_id": None if target in by_id else target,
                                 "sources": _sources(relationship)})
            if record is not None:
                outgoing.append({"type": relationship["type"], "node": None, "record": record,
                                 "sources": _sources(relationship)})
        incoming: list[dict[str, Any]] = []
        for other in entities:
            for relationship in _relationships(other):
                if chosen["id"] in _targets(relationship, owners)[0]:
                    incoming.append({"type": relationship["type"], "node": _node(other),
                                     "sources": _sources(relationship)})
        incoming.sort(key=lambda link: (link["type"], link["node"]["label"].casefold()))
        kind_node: dict[str, Any] = {"id": f"kind:{chosen['kind']}", "label": chosen["kind"],
                                     "kind": "type"}
        node = {**_node(chosen), "aliases": sorted(set(chosen.get("aliases") or []))}
        view = EVIDENCE_VIEW.get(chosen["kind"])
        return {**result, "node": node, "path": [root, kind_node, node],
                "status": (chosen.get("status") or {}).get("state"),
                "records": [{"collection": link.get("collection"),
                             "source_key": link.get("source_key"),
                             "count": len(link.get("source_record_keys") or [])}
                            for link in chosen.get("links") or []],
                "facts": card_facts,
                "outgoing": outgoing, "incoming": incoming,
                "evidence_node": f"{view}:{chosen['id']}" if view else None}

    raise InvalidFactRequest("This node is not in the release.")
