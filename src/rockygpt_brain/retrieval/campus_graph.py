"""Turn-local root-first navigation over the shared office evidence reader.

Root/category edges organize navigation; only the shared reader supplies facts.
One lookup walks the root, directory and office in code; the office node carries its
published records. Children must still be discovered before they can be opened, even on
follow-up turns.
"""

from datetime import datetime
from typing import Any

from rockygpt_brain.retrieval.entity_facts import EntityFacts, InvalidFactRequest
from rockygpt_brain.retrieval.projection import OFFICE_FIELDS
from rockygpt_brain.timing import measure


def _inside(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The one office whose whole published name is in the question, when no other candidate's
    name is a larger part of what the student said. Two offices named in one question stay two."""
    if not candidates or any(
            not str(c.get("basis", "")).startswith("contains") for c in candidates):
        return None
    covers = [frozenset(c["covers"]) for c in candidates]
    # A name whose words are all inside a longer name's words was only part of that longer name.
    widest = [c for c, words in zip(candidates, covers, strict=True)
              if not any(words < other for other in covers)]
    if len({frozenset(c["covers"]) for c in widest}) != 1:
        return None
    named = [c for c in widest if c["basis"] == "contains_name"]
    pool = named if len(named) == 1 else widest
    return pool[0] if len(pool) == 1 else None


def lookup_choice(
    candidates: list[dict[str, Any]], truncated: bool,
) -> tuple[str, list[dict[str, Any]]]:
    """Resolve only a unique untruncated match; otherwise preserve the alternatives.

    One office is the answer when it is the only candidate, or the only one whose words are the
    student's; when several offices list the student's words, the one that has them as its own
    name (an alias of another office is a lesser claim); or, with no such match, the one whose
    whole name is in the question and is not a part of another candidate's longer name. A query
    "public safety non-emergency number" names "Public Safety (Non-Emergency)", whose words
    include all of "Public Safety (Emergency)". Anything else is a list to choose from."""
    if not truncated:
        exact = [candidate for candidate in candidates if candidate["match"] == "exact"]
        if len(candidates) == 1:
            return "answers", candidates
        if len(exact) == 1:
            return "answers", exact
        if len(exact) > 1:
            named = [c for c in exact if c.get("basis") == "name"]
            if len(named) == 1:
                return "answers", named
        elif (inside := _inside(candidates)) is not None:
            return "answers", [inside]
    return ("asks" if candidates else "not_found"), candidates


class CampusGraph:
    def __init__(self, facts: EntityFacts, *, dataset_version: str | None = None,
                 identity_hash: str | None = None) -> None:
        self.facts = facts
        self.version = dataset_version
        self.identity_hash = identity_hash
        self.nodes: dict[str, dict[str, Any]] = {
            "ramapo": {"id": "ramapo", "label": "Ramapo", "kind": "root"},
            "offices": {"id": "offices", "label": "Offices", "kind": "category"},
        }
        self.paths = {"ramapo": ["ramapo"], "offices": ["ramapo", "offices"]}
        self.current_node = "ramapo"

    def root(self) -> dict[str, Any]:
        return {**self.nodes["ramapo"], "children": [self.nodes["offices"]],
                "scope": "Only the office branch is available. Category links are navigation, "
                         "not evidence. Other campus domains are not exposed by this reader."}

    def path(self, node_id: str) -> list[dict[str, Any]]:
        return [dict(self.nodes[key]) for key in self.paths.get(node_id, [])]

    def lookup(self, query: str, fields: list[str], as_of: datetime, *,
               exact_name_only: bool = False) -> dict[str, Any]:
        """Run one complete root-first traversal, without intermediate model decisions."""
        with measure("Open root · Ramapo"):
            self.open("ramapo", [], as_of)
        with measure("Open Offices · read published directory"):
            listing = self.open("offices", [], as_of)
        with measure("Match office name or service"):
            found = self.facts.search_offices(query, dataset_version=self.version,
                                             identity_hash=self.identity_hash)
        candidates = found["candidates"]
        truncated = found["truncated"] or listing["truncated"]
        if exact_name_only:
            # Emergency contact routing may never substitute a similar office name.
            candidates = [candidate for candidate in candidates if candidate["name"] == query]
        outcome, chosen = lookup_choice(candidates, truncated)
        result = {"candidates": candidates, "truncated": truncated,
                  "dataset_version": self.version, "identity_hash": self.identity_hash}
        if outcome != "answers":
            return result
        with measure("Open matched office · read published records · shared entity facts"):
            opened = {**result, **self.open(f"office:{chosen[0]['entity_id']}", fields, as_of)}
        if not exact_name_only and (note := self._note_for_unpublished(opened, fields, as_of)):
            opened["also"] = note
        return opened

    def _note_for_unpublished(
        self, opened: dict[str, Any], fields: list[str], as_of: datetime,
    ) -> dict[str, Any] | None:
        """The office's contact note, when a requested email or phone is not published.

        The note is where an office lists its role and service contacts, so it is the one place
        an alternative to the missing value can be. It is read as supplementary: the model did not
        ask for it, so it is neither a requested field nor a reason for an answer to be partial.
        """
        if "contact_note" in fields:
            return None
        asked = [p for p in opened["facts"]["properties"] if p["key"] in ("email", "phones")]
        if not any(p["status"] in ("unknown", "not_published") for p in asked):
            return None
        with measure("Read contact note · requested contact not published"):
            extra = self.open(opened["id"], ["contact_note"], as_of)["facts"]
        note = next((p for p in extra["properties"] if p["key"] == "contact_note"), None)
        return extra if note is not None and note["status"] == "known" else None

    def inspect(
        self, node_id: str, as_of: datetime, fields: list[str] | None = None,
    ) -> dict[str, Any]:
        """Reconstruct a developer deep link through the same published root path."""
        root = self.open("ramapo", [], as_of)
        listing = self.open("offices", [], as_of)  # Validate the publication even at the root.
        if node_id == "ramapo":
            opened = root
        elif node_id == "offices":
            opened = listing
        else:
            if node_id not in self.nodes or self.nodes[node_id]["kind"] != "office":
                raise InvalidFactRequest("This node is not in the available office graph.")
            opened = self.open(
                node_id, fields if fields is not None else list(OFFICE_FIELDS), as_of)
        return {
            "node": self.nodes[node_id], "path": self.path(node_id),
            "children": opened.get("children", []), "truncated": listing["truncated"],
            "facts": opened.get("facts"), "scope": root["scope"],
            "dataset_version": self.version, "identity_hash": self.identity_hash,
            "as_of": as_of.isoformat(),
        }

    def _child(self, parent: str, node: dict[str, Any]) -> dict[str, Any]:
        key = node["id"]
        self.nodes[key] = node
        self.paths[key] = [*self.paths[parent], key]
        return node

    def open(self, node_id: str, fields: list[str], as_of: datetime) -> dict[str, Any]:
        if node_id not in self.nodes:
            raise InvalidFactRequest("Open only a child returned by this turn's graph.")
        node = self.nodes[node_id]
        if node["kind"] != "office" and fields:
            raise InvalidFactRequest("Select fields only on an office node.")
        self.current_node = node_id
        if node_id == "ramapo":
            return self.root()
        if node_id == "offices":
            listing = self.facts.list_offices(dataset_version=self.version,
                                            identity_hash=self.identity_hash, with_ids=True,
                                            limit=500)
            self.version, self.identity_hash = listing["dataset_version"], listing["identity_hash"]
            children = [self._child(node_id, {
                "id": f"office:{office['entity_id']}", "label": office["name"],
                "kind": "office", "entity_id": office["entity_id"],
                "aliases": office["aliases"],
            }) for office in listing["offices"]]
            result = {**node, "children": children, "truncated": listing["truncated"]}
        else:
            result = dict(node)
            if fields:
                if self.version is None:
                    raise InvalidFactRequest("Open Offices before reading an office's records.")
                result["facts"] = self.facts.get_office_facts(
                    node["entity_id"], fields, self.version,
                    identity_hash=self.identity_hash, as_of=as_of)
        return {**result, "dataset_version": self.version, "identity_hash": self.identity_hash}
