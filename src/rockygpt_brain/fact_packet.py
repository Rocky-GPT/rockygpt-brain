"""The Fact Packet: the one JSON contract the Brain's findings leave in.

The Brain understands the question, resolves entities, walks the graph, applies dates and rules,
and validates evidence. It ends there. What it hands on is this packet, and nothing that writes
words (a template or a model) may discover, infer, calculate or retrieve a fact: it only expresses
what the packet holds.

Version 1.0:

    version        "1.0"
    request        intent, entities [{id, name, kind, query}], fields, asOf, dataset
    status         complete | partial | insufficient | ambiguous | not_found | emergency
                   | no_facts_needed
    facts          [{id, subject {id, name, kind}, predicate, value, status, current, source_ids}]
    derived_facts  [] (reserved: values the Brain computes, such as "open at 8 pm")
    missing        [{subject, predicate, reason}]   a requested fact we hold no information about
                   (reason "unknown": nobody checked, or the page could not be read)
    not_published  [{subject, predicate, checked_at, current, checks, source_ids}]
                   a requested fact the office's own pages were read for and do not publish: an
                   answer, not a gap (the pages and date are recorded)
    ambiguities    [{query, candidates [{id, name, match}], truncated}]
    unresolved     [{query, reason}]                a name that matched no office
    notices        [{type, ...}]                    replies with no facts (greeting, safety...)
    sources        [{id, title, collection, urls, captured_at, freshness, validity, valid_from,
                     valid_until, current, limitations}]

A fact's `value` is exactly what the shared reader returned for that property; `status` is the
reader's (known, conflicting or multiple) and `current` says whether any of its sources is fresh
and inside its published validity. Conflicts are listed side by side; nothing is chosen.

The packet is all a writer sees, so it must stand alone. A notice that stands for words the Brain
used to write itself (the emergency guidance, "I can't see your student record", the greeting)
carries those words as `approved_text`: the writer states them, it never composes them. A
`safety` notice always has it, and `emergency_reminder` marks the standing reminder that goes
with a reply cut short; only a real `safety` notice makes the status `emergency`.
"""

import json
from typing import Any

from rockygpt_brain.answers import _current as source_is_current
from rockygpt_brain.answers import citation_url

PACKET_VERSION = "1.0"
STATUSES = frozenset({"complete", "partial", "insufficient", "ambiguous", "not_found",
                      "emergency", "no_facts_needed"})
CONTACT_FIELDS = frozenset({"name", "department", "email", "phones", "offices", "prefers_email",
                            "preferred_contact", "contact_note", "website"})
# Replies that cannot carry facts: the question is outside what the Brain can look up.
CANNOT_ANSWER = frozenset({"unsupported", "account_limit", "clarification"})
ABSENCE_KEYS = frozenset({"subject", "predicate", "checked_at", "checks", "current", "source_ids"})
FACT_KEYS = frozenset({"id", "subject", "predicate", "value", "status", "current", "source_ids"})


class PacketInvalid(ValueError):
    """The packet does not meet the contract, so nothing may be written from it."""


def _source(raw: dict[str, Any]) -> dict[str, Any]:
    source = {
        "id": raw["id"], "title": str(raw["source_key"]), "collection": raw["collection"],
        "urls": list(dict.fromkeys(
            url for candidate in raw["citation_urls"] if (url := citation_url(candidate)))),
        "captured_at": raw.get("collected_at"), "freshness": raw["freshness"],
        "validity": raw["validity"], "valid_from": raw.get("valid_from"),
        "valid_until": raw.get("valid_until"), "current": source_is_current(raw),
        "limitations": list(raw.get("caveats", [])),
    }
    for key in ("original_record_id", "observation_field", "original_collected_at"):
        if key in raw:
            source[key] = raw[key]
    return source


class _Builder:
    def __init__(self) -> None:
        self.facts: list[dict[str, Any]] = []
        self.missing: list[dict[str, Any]] = []
        self.not_published: list[dict[str, Any]] = []
        self.ambiguities: list[dict[str, Any]] = []
        self.unresolved: list[dict[str, Any]] = []
        self.notices: list[dict[str, Any]] = []
        self.sources: dict[str, dict[str, Any]] = {}
        self.entities: dict[str, dict[str, Any]] = {}
        self.fields: list[str] = []
        self.dataset: dict[str, Any] | None = None

    def office(self, part: dict[str, Any], *, purpose: str | None = None) -> None:
        reader = part["facts"]
        entity = {"id": part["office"]["id"], "name": part["office"]["name"],
                  "kind": reader["entity"]["kind"]}
        if purpose is None:
            self.entities.setdefault(entity["id"], {**entity, "query": part["query"]})
            self.fields.extend(f for f in part["fields"] if f not in self.fields)
        self.dataset = self.dataset or {"version": reader.get("dataset_version"),
                                        "identityHash": reader.get("identity_hash")}
        local: dict[str, dict[str, Any]] = {}
        for raw in reader["sources"]:
            source = _source(raw)
            if self.sources.setdefault(source["id"], source) != source:
                raise PacketInvalid("Two different sources share one id.")
            local[source["id"]] = source
        for prop in reader["properties"]:
            if prop["status"] == "not_published":
                absence = prop["absence"]
                ids = list(absence["source_ids"])
                if not ids or any(sid not in local for sid in ids) or not absence["checks"]:
                    raise PacketInvalid("A confirmed absence names a source the reader lacks.")
                entry = {"subject": entity, "predicate": prop["key"],
                         "checked_at": absence["checked_at"], "checks": list(absence["checks"]),
                         "current": any(local[sid]["current"] for sid in ids), "source_ids": ids}
                if purpose:
                    entry["purpose"] = purpose
                self.not_published.append(entry)
                continue
            if prop["status"] == "unknown" or not prop["values"]:
                self.missing.append({"subject": entity, "predicate": prop["key"],
                                     "reason": "unknown"})
                continue
            for value in prop["values"]:
                ids = list(value["source_ids"])
                if not ids or any(sid not in local for sid in ids):
                    raise PacketInvalid("A fact names a source the reader did not return.")
                fact = {"id": f"f{len(self.facts) + 1}", "subject": entity,
                        "predicate": prop["key"], "value": value["value"],
                        "status": prop["status"],
                        "current": any(local[sid]["current"] for sid in ids), "source_ids": ids}
                if purpose:
                    fact["purpose"] = purpose
                self.facts.append(fact)
        if not reader.get("complete", True):
            self.notices.append({"type": "evidence_incomplete", "subject": entity,
                                 "caveats": list(reader.get("caveats", []))})

    def add(self, part: dict[str, Any]) -> None:
        kind = part["kind"]
        if kind == "office_facts":
            self.office(part)
        elif kind == "ambiguous":
            self.ambiguities.append({"query": part["query"], "truncated": part["truncated"],
                                     "candidates": part["candidates"]})
        elif kind == "not_found":
            self.unresolved.append({"query": part["query"], "reason": "no_matching_office"})
        elif kind == "safety":
            for contact in part["campusContacts"]:
                self.office(contact, purpose="emergency_contact")
            self.notices.append({"type": "safety", "situation": part["situation"],
                                 "contacts": sorted({c["office"]["id"]
                                                     for c in part["campusContacts"]}),
                                 "approved_text": part["approved_text"]})
        else:
            self.notices.append({"type": kind, **{k: v for k, v in part.items() if k != "kind"}})


def _intent(builder: _Builder) -> str:
    kinds = [notice["type"] for notice in builder.notices]
    if "safety" in kinds:
        return "safety"
    if builder.fields:
        wanted = set(builder.fields)
        if wanted == {"hours"}:
            return "hours"
        if "hours" in wanted:
            return "contact_and_hours"
        return "contact" if wanted <= CONTACT_FIELDS else "office_facts"
    if builder.ambiguities or builder.unresolved:
        return "office_lookup"
    real = [kind for kind in kinds if kind not in {"evidence_incomplete", "incomplete"}]
    return real[0] if real else "unknown"


def _status(builder: _Builder) -> str:
    kinds = {notice["type"] for notice in builder.notices}
    asked = [fact for fact in builder.facts if fact.get("purpose") is None]
    absent = [entry for entry in builder.not_published if entry.get("purpose") is None]
    answered = asked or absent  # A confirmed "not published" is an answer too.
    if "safety" in kinds:
        return "emergency"
    if not answered and (builder.ambiguities or "clarification" in kinds):
        return "ambiguous"  # The Brain needs a follow-up question answered before it can look up.
    if not answered and builder.unresolved:
        return "not_found"
    if answered:
        degraded = (builder.missing or builder.ambiguities or builder.unresolved
                    or kinds & ({"evidence_incomplete", "incomplete"} | CANNOT_ANSWER)
                    or any(not f["current"] or f["status"] != "known" for f in asked)
                    or any(not entry["current"] for entry in absent))
        return "partial" if degraded else "complete"
    if builder.missing or kinds & CANNOT_ANSWER:
        return "insufficient"
    return "no_facts_needed"


def build_packet(as_of: str, parts: list[dict[str, Any]]) -> dict[str, Any]:
    """The Fact Packet for a turn's findings; raises PacketInvalid when they break the contract."""
    builder = _Builder()
    for part in parts:
        builder.add(part)
    packet: dict[str, Any] = {
        "version": PACKET_VERSION,
        "request": {"intent": _intent(builder), "entities": list(builder.entities.values()),
                    "fields": builder.fields, "asOf": as_of, "dataset": builder.dataset},
        "status": _status(builder),
        "facts": builder.facts,
        "derived_facts": [],
        "missing": builder.missing,
        "not_published": builder.not_published,
        "ambiguities": builder.ambiguities,
        "unresolved": builder.unresolved,
        "notices": builder.notices,
        "sources": list(builder.sources.values()),
    }
    validate_packet(packet)
    return packet


_LISTS = ("facts", "derived_facts", "missing", "not_published", "ambiguities", "unresolved",
          "notices", "sources")


def validate_packet(packet: dict[str, Any]) -> None:
    """Refuse a packet a writer could not safely trust."""
    if packet.get("version") != PACKET_VERSION:
        raise PacketInvalid("Unsupported packet version.")
    if packet.get("status") not in STATUSES:
        raise PacketInvalid("Unknown packet status.")
    request = packet.get("request")
    if not isinstance(request, dict) or not isinstance(request.get("intent"), str) \
            or not request["intent"] or not isinstance(request.get("asOf"), str):
        raise PacketInvalid("The packet has no request intent and time.")
    for key in _LISTS:
        if not isinstance(packet.get(key), list):
            raise PacketInvalid(f"The packet's {key} must be a list.")
    source_ids = [source.get("id") for source in packet["sources"]]
    if len(source_ids) != len(set(source_ids)) or not all(isinstance(i, str) for i in source_ids):
        raise PacketInvalid("Source ids must be unique text.")
    fact_ids: set[str] = set()
    for fact in packet["facts"]:
        if not FACT_KEYS <= set(fact):
            raise PacketInvalid("A fact is missing a required field.")
        if fact["id"] in fact_ids:
            raise PacketInvalid("Fact ids must be unique.")
        fact_ids.add(fact["id"])
        if not fact["source_ids"] or not set(fact["source_ids"]) <= set(source_ids):
            raise PacketInvalid("Every fact needs sources the packet lists.")
        if fact["status"] not in {"known", "conflicting", "multiple"}:
            raise PacketInvalid("A fact has an unknown status.")
        if not isinstance(fact["current"], bool) or not isinstance(fact["predicate"], str):
            raise PacketInvalid("A fact has the wrong types.")
    for notice in packet["notices"]:
        text = notice.get("approved_text")
        if not isinstance(notice.get("type"), str) or (
                notice["type"] == "safety" and not (isinstance(text, str) and text)):
            raise PacketInvalid("A safety notice must carry its approved text.")
    for entry in packet["missing"]:
        if not {"subject", "predicate", "reason"} <= set(entry):
            raise PacketInvalid("A missing entry is incomplete.")
    for entry in packet["not_published"]:
        if not ABSENCE_KEYS <= set(entry):
            raise PacketInvalid("A not-published entry is incomplete.")
        if (not isinstance(entry["checks"], list) or not entry["checks"]
                or not entry["source_ids"] or not set(entry["source_ids"]) <= set(source_ids)
                or not isinstance(entry["current"], bool)):
            raise PacketInvalid("A not-published entry needs the pages checked and listed sources.")
    try:
        json.dumps(packet, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise PacketInvalid("The packet is not plain JSON.") from error
