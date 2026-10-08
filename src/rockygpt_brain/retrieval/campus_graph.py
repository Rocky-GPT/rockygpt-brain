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


def lookup_choice(
    candidates: list[dict[str, Any]], truncated: bool,
) -> tuple[str, list[dict[str, Any]]]:
    """Resolve only a unique untruncated match; otherwise preserve the alternatives."""
    exact = [candidate for candidate in candidates if candidate["match"] == "exact"]
    chosen = exact if len(exact) == 1 and not truncated else candidates
    if len(chosen) == 1 and not truncated:
        return "answers", chosen
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
            return {**result, **self.open(f"office:{chosen[0]['entity_id']}", fields, as_of)}

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
