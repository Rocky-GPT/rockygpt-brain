"""Turn-local root-first navigation over the shared office evidence reader.

Root/category edges organize navigation; only the shared reader supplies facts.
Children must be discovered before they can be opened, even on follow-up turns.
"""

from datetime import datetime
from typing import Any

from rockygpt_brain.retrieval.entity_facts import EntityFacts, InvalidFactRequest


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

    def root(self) -> dict[str, Any]:
        return {**self.nodes["ramapo"], "children": [self.nodes["offices"]],
                "scope": "Only the office branch is available. Category links are navigation, "
                         "not evidence. Other campus domains are not exposed by this reader."}

    def path(self, node_id: str) -> list[dict[str, Any]]:
        return [dict(self.nodes[key]) for key in self.paths.get(node_id, [])]

    def _child(self, parent: str, node: dict[str, Any]) -> dict[str, Any]:
        key = node["id"]
        self.nodes[key] = node
        self.paths[key] = [*self.paths[parent], key]
        return node

    def open(self, node_id: str, fields: list[str], as_of: datetime) -> dict[str, Any]:
        if node_id not in self.nodes:
            raise InvalidFactRequest("Open only a child returned by this turn's graph.")
        node = self.nodes[node_id]
        if node["kind"] != "records" and fields:
            raise InvalidFactRequest("Select fields only on a published-records node.")
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
        elif node["kind"] == "office":
            child = self._child(node_id, {
                "id": f"records:{node['entity_id']}", "label": "Published records",
                "kind": "records", "entity_id": node["entity_id"], "office": node["label"],
            })
            result = {**node, "children": [child]}
        else:
            if not fields or self.version is None:
                raise InvalidFactRequest("Choose fields on a reached records node.")
            facts = self.facts.get_office_facts(node["entity_id"], fields, self.version,
                                              identity_hash=self.identity_hash, as_of=as_of)
            result = {**node, "facts": facts}
        return {**result, "dataset_version": self.version, "identity_hash": self.identity_hash}
