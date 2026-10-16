"""Turn-local root-first navigation over the shared entity evidence reader.

Root/category edges organize navigation; only the shared reader supplies facts.
Ramapo has three branches: Offices, Dining (venues) and Shuttle (routes). One lookup walks the
root, directory and office or venue in code; that node carries its published records. Children
must still be discovered before they can be opened, even on follow-up turns.
"""

import json
import re
from datetime import date, datetime
from typing import Any

from rockygpt_brain.retrieval.entity_facts import (
    CAMPUS_TIMEZONE,
    NAMED_KINDS,
    EntityFacts,
    InvalidFactRequest,
    discovery_tokens,
)
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


def named_by_conversation(
    candidates: list[dict[str, Any]], conversation: str,
) -> dict[str, Any] | None:
    """The one tied candidate that lists, as an alias of its own, a name the conversation used.

    Two offices that share a name ("Public Safety") are a tie the student has not broken. A
    conversation about a parking ticket has, when only one of them lists "Parking Citations".
    Only an alias no other candidate also lists counts. The offices' own names do not: their
    words overlap, and "not an emergency" contains the word "emergency".
    """
    words = discovery_tokens(conversation)
    listed = [{" ".join(alias.casefold().split()) for alias in c["aliases"]} for c in candidates]
    named = []
    for index, candidate in enumerate(candidates):
        others = set().union(*(names for at, names in enumerate(listed) if at != index))
        for alias in candidate["aliases"]:
            tokens = discovery_tokens(alias)
            if " ".join(alias.casefold().split()) not in others and tokens and tokens <= words:
                named.append(candidate)
                break
    return named[0] if len(named) == 1 else None


# The category each named kind is listed under. Offices and venues come from one listing.
BRANCH = {"office": "offices", "venue": "dining"}


# Words that say what a question is about rather than which field it wants or how it is asked.
_NOT_PURPOSE = frozenset(
    "what whats when where which whom whose does have need want with about their there this that "
    "from your into them they help office offices contact contacts reach email emails phone "
    "phones number numbers call hours open opens room rooms located location ramapo college "
    "campus student students please thanks know tell find here just like also some would could "
    "should handles handle talk speak someone anyone person people right today tomorrow tonight "
    "address website page whos".split())


# A note that says to make an appointment first: a "where do I go" answer needs it beside the room.
_APPOINTMENT = re.compile(r"\bappointments?\b", re.IGNORECASE)


def purpose_words(text: str) -> set[str]:
    """The content words of a text (four letters or more, plural s dropped), minus field and
    question words, for matching what a question is about against an office's contact note."""
    words = re.findall(r"[a-z]+", text.casefold())
    return {word[:-1] if len(word) > 4 and word.endswith("s") else word
            for word in words if len(word) >= 4 and word not in _NOT_PURPOSE}


class CampusGraph:
    def __init__(self, facts: EntityFacts, *, dataset_version: str | None = None,
                 identity_hash: str | None = None) -> None:
        self.facts = facts
        self.version = dataset_version
        self.identity_hash = identity_hash
        self.nodes: dict[str, dict[str, Any]] = {
            "ramapo": {"id": "ramapo", "label": "Ramapo", "kind": "root"},
            "offices": {"id": "offices", "label": "Offices", "kind": "category"},
            "dining": {"id": "dining", "label": "Dining", "kind": "category"},
            "shuttle": {"id": "shuttle", "label": "Shuttle", "kind": "category"},
            "topics": {"id": "topics", "label": "Topics", "kind": "category"},
        }
        self.paths = {"ramapo": ["ramapo"], "offices": ["ramapo", "offices"],
                      "dining": ["ramapo", "dining"], "shuttle": ["ramapo", "shuttle"],
                      "topics": ["ramapo", "topics"]}
        self.current_node = "ramapo"

    def root(self) -> dict[str, Any]:
        return {**self.nodes["ramapo"],
                "children": [self.nodes[key]
                             for key in ("offices", "dining", "shuttle", "topics")],
                "scope": "Offices, dining venues, shuttle routes and topics (sites with no office "
                         "of their own, pointing to the offices that handle them) are available. "
                         "Category links "
                         "are navigation, not evidence. Other campus domains are not exposed by "
                         "this reader."}

    def path(self, node_id: str) -> list[dict[str, Any]]:
        return [dict(self.nodes[key]) for key in self.paths.get(node_id, [])]

    def lookup(self, query: str, fields: list[str], as_of: datetime, *,
               exact_name_only: bool = False, conversation: str = "", latest: str = "",
               on: date | None = None, meal: str | None = None,
               menu_query: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run one complete root-first traversal, without intermediate model decisions."""
        with measure("Open root · Ramapo"):
            self.open("ramapo", [], as_of)
        with measure("Open Offices · read published directory"):
            listing = self.open("offices", [], as_of)
        with measure("Match office name or service"):
            found = self.facts.search_offices(query, dataset_version=self.version,
                                             identity_hash=self.identity_hash,
                                             kinds=NAMED_KINDS)
        candidates = found["candidates"]
        truncated = found["truncated"] or listing["truncated"]
        if exact_name_only:
            # Emergency contact routing may never substitute a similar office name.
            candidates = [candidate for candidate in candidates if candidate["name"] == query]
        outcome, chosen = lookup_choice(candidates, truncated)
        result = {"candidates": candidates, "truncated": truncated,
                  "dataset_version": self.version, "identity_hash": self.identity_hash}
        if not exact_name_only and not any(c["match"] == "exact" for c in candidates):
            # A site with no office behind it (Scholarships, Summer Session) is a topic: its own
            # page names the offices that handle it. Only its exact name or alias is a topic.
            with measure("Match topic name"):
                topic = self._topic(query, as_of)
            if topic is not None:
                return self._topic_result(topic, result, fields, as_of, on=on, meal=meal,
                                          menu_query=menu_query, latest=latest)
        if outcome == "asks" and conversation and not truncated and not exact_name_only:
            named = named_by_conversation(candidates, conversation)
            if named is not None:
                outcome, chosen = "answers", [named]
                result["by_conversation"] = True
        if outcome != "answers":
            return result
        with measure("Open matched office · read published records · shared entity facts"):
            opened = {**result, **self.open(f"office:{chosen[0]['entity_id']}", fields, as_of,
                                            on=on, meal=meal, menu_query=menu_query)}
        if not exact_name_only and (
                note := self._note_for_unpublished(opened, fields, as_of, latest)):
            opened["also"] = note
        return opened

    def _topic(self, query: str, as_of: datetime) -> dict[str, Any] | None:
        found = self.facts.search_offices(query, dataset_version=self.version,
                                          identity_hash=self.identity_hash, kinds=("topic",))
        exact = [c for c in found["candidates"] if c["match"] == "exact"]
        if len(exact) != 1:
            return None
        return self.facts.get_topic(exact[0]["entity_id"], self.version,  # type: ignore[arg-type]
                                    identity_hash=self.identity_hash, as_of=as_of)

    def _topic_result(
        self, topic: dict[str, Any], result: dict[str, Any], fields: list[str], as_of: datetime,
        *, on: date | None, meal: str | None, menu_query: dict[str, Any] | None,
        latest: str = "",
    ) -> dict[str, Any]:
        """One named office is read like a lookup of it; several are offered to choose from; a
        retired topic has no office to read."""
        offices = topic["offices"]
        if len(offices) == 1:
            with measure("Open the topic's office · read published records"):
                opened = {**result, **self.open(f"office:{offices[0]['id']}", fields, as_of,
                                                on=on, meal=meal, menu_query=menu_query)}
            if note := self._note_for_unpublished(opened, fields, as_of, latest):
                opened["also"] = note
            return {**opened, "topic": topic}
        return {**result, "truncated": False, "topic": topic, "candidates": [
            {"entity_id": office["id"], "name": office["name"], "aliases": [],
             "match": "topic", "basis": "topic", "covers": []} for office in offices]}

    def _note_for_unpublished(
        self, opened: dict[str, Any], fields: list[str], as_of: datetime, latest: str = "",
    ) -> dict[str, Any] | None:
        """The office's contact note, when a requested email or phone is not published, and the
        address or number of the way it says it prefers to be reached, when that was not asked.

        The note is where an office lists its role and service contacts, so it is the one place
        an alternative to the missing value can be. It is read as supplementary: the model did not
        ask for it, so it is neither a requested field nor a reason for an answer to be partial.
        """
        wanted = []
        asked = [p for p in opened["facts"]["properties"] if p["key"] in ("email", "phones")]
        if "contact_note" not in fields and any(
                p["status"] in ("unknown", "not_published") for p in asked):
            wanted.append("contact_note")
        # A preferred way to reach the office is only useful with its address or number.
        preferred = next((p for p in opened["facts"]["properties"]
                          if p["key"] == "preferred_contact" and p["status"] == "known"), None)
        method = {"email": "email", "phone": "phones"}.get(
            str(preferred["values"][0]["value"]).casefold()) if preferred else None
        if method and method not in fields:
            wanted.append(method)
        # A question that names a purpose the contact note speaks to ("media inquiries", "parking
        # permit") reads the note too: it is where offices list role and service contacts.
        about = purpose_words(latest) - purpose_words(
            " ".join([str(opened.get("label") or ""), *opened.get("aliases", [])]))
        if about and "contact_note" not in fields and "contact_note" not in wanted:
            wanted.append("contact_note")
        # "Where do I go" (the room) reads the note too: an office may see people by appointment.
        visiting = "offices" in fields and "contact_note" not in fields
        if visiting and "contact_note" not in wanted:
            wanted.append("contact_note")
        # A question that asks where ("where can I get a flu shot") is answered with the room,
        # whatever fields the lookup asked for.
        if "offices" not in fields and re.search(r"\bwhere\b", latest, re.IGNORECASE):
            wanted.append("offices")
        if not wanted:
            return None
        with measure("Read beside the answer · contact note or the preferred contact"):
            extra = self.open(opened["id"], wanted, as_of)["facts"]
        unpublished = "contact_note" in wanted and any(
            p["status"] in ("unknown", "not_published") for p in asked)
        kept = [p for p in extra["properties"] if p["status"] == "known" and (
            p["key"] != "contact_note" or unpublished
            or about & purpose_words(json.dumps([v["value"] for v in p["values"]]))
            or (visiting and _APPOINTMENT.search(json.dumps([v["value"] for v in p["values"]]))))]
        return {**extra, "properties": kept} if kept else None

    def inspect(
        self, node_id: str, as_of: datetime, fields: list[str] | None = None,
        on: date | None = None,
    ) -> dict[str, Any]:
        """Reconstruct a developer deep link through the same published root path."""
        root = self.open("ramapo", [], as_of)
        listing = self.open("offices", [], as_of)  # Validate the publication even at the root.
        if node_id == "ramapo":
            opened = root
        elif node_id == "offices":
            opened = listing
        elif node_id == "dining":
            opened = self.open("dining", [], as_of)
        elif node_id == "topics" or node_id.startswith("topic:"):
            listed = self.open("topics", [], as_of)
            if node_id == "topics":
                opened = listed
            elif node_id not in self.nodes:
                raise InvalidFactRequest("This node is not in the available campus graph.")
            else:
                opened = {**self.nodes[node_id], "topic": self.facts.get_topic(
                    self.nodes[node_id]["entity_id"], self.version,  # type: ignore[arg-type]
                    identity_hash=self.identity_hash, as_of=as_of)}
        elif node_id == "shuttle" or node_id.startswith("route:"):
            routes = self.open("shuttle", [], as_of)
            if node_id == "shuttle":
                opened = routes
            elif node_id not in self.nodes:
                raise InvalidFactRequest("This node is not in the available campus graph.")
            else:
                opened = {**self.nodes[node_id], "facts": self.facts.get_route_facts(
                    self.nodes[node_id]["entity_id"], self.version,  # type: ignore[arg-type]
                    identity_hash=self.identity_hash, as_of=as_of)}
        else:
            if node_id not in self.nodes or self.nodes[node_id]["kind"] not in NAMED_KINDS:
                raise InvalidFactRequest("This node is not in the available campus graph.")
            # A venue's menu is read for the inspection's campus date; an office has no menu.
            venue = self.nodes[node_id]["kind"] == "venue"
            chosen = fields if fields is not None else [
                field for field in OFFICE_FIELDS if venue or field != "menu"]
            day = on or as_of.astimezone(CAMPUS_TIMEZONE).date()
            opened = self.open(node_id, chosen, as_of, on=day if venue else None)
            if venue:
                # Every date the venue published a menu for, so the inspector can open each one.
                menu = next(p for p in self.open(node_id, ["menu"], as_of)["facts"]["properties"]
                            if p["key"] == "menu")
                opened["menu_dates"] = menu.get("dates_published", [])
        return {
            "node": self.nodes[node_id], "path": self.path(node_id),
            "children": opened.get("children", []), "truncated": listing["truncated"],
            "facts": opened.get("facts"), "scope": root["scope"],
            **({"menu_dates": opened["menu_dates"]} if "menu_dates" in opened else {}),
            **({"topic": opened["topic"]} if "topic" in opened else {}),
            "dataset_version": self.version, "identity_hash": self.identity_hash,
            "as_of": as_of.isoformat(),
        }

    def _child(self, parent: str, node: dict[str, Any]) -> dict[str, Any]:
        key = node["id"]
        self.nodes[key] = node
        self.paths[key] = [*self.paths[parent], key]
        return node

    def open(self, node_id: str, fields: list[str], as_of: datetime, *,
             on: date | None = None, meal: str | None = None,
             menu_query: dict[str, Any] | None = None) -> dict[str, Any]:
        if node_id not in self.nodes:
            raise InvalidFactRequest("Open only a child returned by this turn's graph.")
        node = self.nodes[node_id]
        if node["kind"] not in NAMED_KINDS and fields:
            raise InvalidFactRequest("Select fields only on an office or venue node.")
        self.current_node = node_id
        if node_id == "ramapo":
            return self.root()
        if node_id in ("offices", "dining"):
            listing = self.facts.list_offices(dataset_version=self.version,
                                            identity_hash=self.identity_hash, with_ids=True,
                                            limit=500, kinds=NAMED_KINDS)
            self.version, self.identity_hash = listing["dataset_version"], listing["identity_hash"]
            # Both branches are registered, so a lookup can open an office or a venue after
            # opening either; the node ID keeps its office: prefix for saved traces.
            children = [self._child(BRANCH[office.get("kind", "office")], {
                "id": f"office:{office['entity_id']}", "label": office["name"],
                "kind": office.get("kind", "office"), "entity_id": office["entity_id"],
                "aliases": office["aliases"],
            }) for office in listing["offices"]]
            result = {**node, "children": [c for c in children if BRANCH[c["kind"]] == node_id],
                      "truncated": listing["truncated"]}
        elif node_id == "topics":
            listing = self.facts.list_topics(dataset_version=self.version,
                                             identity_hash=self.identity_hash)
            self.version, self.identity_hash = listing["dataset_version"], listing["identity_hash"]
            children = [self._child(node_id, {
                "id": f"topic:{topic['entity_id']}", "label": topic["name"], "kind": "topic",
                "entity_id": topic["entity_id"], "aliases": topic["aliases"],
            }) for topic in listing["topics"]]
            result = {**node, "children": children, "truncated": False}
        elif node_id == "shuttle":
            listing = self.facts.list_routes(dataset_version=self.version,
                                             identity_hash=self.identity_hash)
            self.version, self.identity_hash = listing["dataset_version"], listing["identity_hash"]
            children = [self._child(node_id, {
                "id": f"route:{route['entity_id']}", "label": route["name"], "kind": "route",
                "entity_id": route["entity_id"],
            }) for route in listing["routes"]]
            result = {**node, "children": children, "truncated": False}
        else:
            result = dict(node)
            if fields:
                if self.version is None:
                    raise InvalidFactRequest("Open Offices before reading an office's records.")
                result["facts"] = self.facts.get_office_facts(
                    node["entity_id"], fields, self.version,
                    identity_hash=self.identity_hash, as_of=as_of, on=on, meal=meal,
                    menu_query=menu_query)
        return {**result, "dataset_version": self.version, "identity_hash": self.identity_hash}
