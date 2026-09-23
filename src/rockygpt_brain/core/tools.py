"""Strict tool definitions and Response function calling schemas."""

from __future__ import annotations

import json
from typing import Any

from rockygpt_brain.campus.calculations import CalculationQuery
from rockygpt_brain.campus.formats import ContactCall, SearchCall
from rockygpt_brain.retrieval.models import COLLECTIONS, ReadQuery
from rockygpt_brain.retrieval.profiles import ProfileQuery


def function_tool(name: str, description: str, schema: dict[str, Any]) -> dict[str, Any]:
    # Optional arguments are required-but-nullable in strict Responses schemas.
    schema = json.loads(json.dumps(schema))

    def strict(node: Any) -> None:
        if isinstance(node, dict):
            node.pop("default", None)
            if node.get("type") == "object":
                node["required"] = list(node["properties"])
                node["additionalProperties"] = False
            for child in node.values():
                strict(child)
        elif isinstance(node, list):
            for child in node:
                strict(child)

    strict(schema)
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": schema,
        "strict": True,
    }


def tool_definitions() -> list[dict[str, Any]]:
    return [
        function_tool(
            "calculate",
            "Compute arithmetic or ascending sort over explicit user numbers or exact retrieved "
            "calories/credits; count supplied record IDs; compare two verified times or calculate "
            "their elapsed minutes (second minus first). Times require explicit user ISO offsets "
            "or published event, opening/closing, or schedule_calculations references. Preserve "
            "arrival/departure meaning and exact stop labels. Supply only operands, times, or "
            "evidence_ids for the chosen operation; other lists must be empty. No inferred units, "
            "travel durations, policy conclusions or corpus-wide counts. Results require context.",
            CalculationQuery.model_json_schema(),
        ),
        function_tool(
            "lookup_profile",
            "Resolve a named campus entity through curated identity links and retrieve its "
            "selected contact, faculty, undated profile courses, program, conveners, dated "
            "campus/dining hours, menu, club, event, related, requirements, building and school "
            "sections. Prefer it for named "
            "combined requests, "
            "program conveners and their follow-ups. Supply exactly one name/verified alias "
            "or a previously returned entity_id. For follow-ups, resolve the subject of the "
            "CURRENT request from recent dialogue; prior names are lookup selectors, not "
            "factual evidence. If the request asks for a mentioned person's contact field, "
            "look up that person's name with include=['contact'], even if the prior answer "
            "was about a program. Recheck the program relationship only when requested or "
            "the person's identity is ambiguous. If prior tool IDs are absent, resolve "
            "the current subject's name or alias directly. "
            "A convener relationship returns a person's "
            "identity ID; look up that ID's contact section for their email. "
            "Choose only requested components. Use club for a student club's or campus "
            "organization's Archway directory profile and contact for its published email/links; "
            "its published category says whether it is a student organization or a department, "
            "residence hall, team or school. Use event for a specific dated event instance; "
            "a title can name several occurrences, so supply its requested date or clarify. "
            "An event profile with null date retains that occurrence's published date; "
            "hours/menu use the current campus date when null. "
            "An organized_by relationship returns an explicitly identified club or organization "
            "ID; retrieve its contact or club section for a follow-up. A club's or "
            "organization's event section traverses only approved organizer links, soonest "
            "upcoming first (date narrows it), retains each occurrence's identity, and reports "
            "any unexamined candidates; it is not the complete campus event calendar. "
            "Use related to follow other published relationships in either direction, e.g. "
            "relationship='convener', direction='incoming' for the programs a person convenes, "
            "or relationship='listed_faculty' for the people a program's catalog Program Faculty "
            "field lists; a listing is neither convenership nor current teaching. "
            "relationship='office_at' (people) or 'located_at' (offices) places a published room "
            "in a campus building by its room prefix; follow it incoming from a building for the "
            "people or offices with rooms there. Use building for a building's campus map record "
            "and room prefixes. A building is a location, not a school, host or owner, and rooms "
            "cover only published room numbers. relationship='part_of' places programs (through "
            "their catalog school, which may be a reviewed former name) and faculty (through "
            "their profile) in Ramapo's current schools; use school for a school's official page, "
            "abbreviation and former names. "
            "Each related entity has an entity_id to look up for its own details. "
            "Use requirements for a program's published catalog requirement groups in section "
            "order, each with its nested all/any/choose-N structure, counts, credits and notes. "
            "A course listed as one option of a choose-N or either/or group is not required on "
            "its own; name the choice it belongs to. Conditions without a derived choose stay "
            "as published. Read a truncated requirement record in full with read_campus. "
            "Organizer/location names alone "
            "do not link identities. An event is neither recurring operating hours nor an "
            "academic program. Missing event times/location remain unknown. "
            "Use date and meal for dated menus and meal hours. Profile course lists are "
            "undated, never current-semester teaching assignments. "
            "Use menu_limit=12 for a normal menu summary and give a few exactly cited "
            "examples; raise it only for an explicitly requested complete menu. The menu "
            "section reports matching, returned and omitted counts; never call a truncated "
            "selection the full menu. Answer the requested meal's hours without enumerating "
            "other meal periods unless requested. "
            "Ambiguity needs clarification; no match means no curated identity, not nonexistence. "
            "A name or alias several entities share (e.g. a program family such as 'Computer "
            "Science', or 'Public Safety') returns them all as candidates; ask or answer for each. "
            "An entity with status 'retired' is published as retired; never present it as current. "
            "Each record retains its own source and freshness. Conflicts and unavailable "
            "components do not invalidate independent fields. Operating hours never establish "
            "staff or telephone availability. Generated profile answers require evidence review.",
            ProfileQuery.model_json_schema(),
        ),
        function_tool(
            "lookup_contact",
            "First choice for how to contact a named office/person, contact details, or "
            "specific phone, email, office, department or other directory fields. "
            "Look up the exact published name or alias. "
            "Include every requested field; for 'contact details' or 'how to contact', "
            "request phone, email, office and department. Hours/fax/website can be uncovered; "
            "never infer them. Empty records do not prove an office does not exist. "
            "The server may render a complete, validated contact answer directly. "
            "For unnamed entities, discover their published names with search_campus first.",
            ContactCall.model_json_schema(),
        ),
        function_tool(
            "search_campus",
            "Search published official campus evidence. Collections: "
            + ", ".join(COLLECTIONS)
            + ". Use short distinctive terms; an empty query browses a collection. "
            "Dates are campus-local ISO dates. Always supply date_from for menu, "
            "campus_hours, dining_hours, shuttle and events, using the requested date "
            "or the supplied current campus date. Do not put schedule dates only in keywords. "
            "Read returned records for missing details. "
            "Search each requested subject; reformulate if no relevant results. "
            "A no-match result may include discovery_titles from a small published collection. "
            "Choose relevant names by meaning and retrieve their records before citing them.",
            SearchCall.model_json_schema(),
        ),
        function_tool(
            "read_campus",
            "Read details of evidence ids already returned by search_campus or lookup_profile.",
            ReadQuery.model_json_schema(),
        ),
    ]
