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
            "contact and/or dated hours together. Prefer this for combined contact and hours "
            "requests. Supply exactly one name/verified alias or a previously returned entity_id. "
            "Choose only requested components. A null date uses the current campus date. "
            "Ambiguity needs clarification; no match means no curated identity, not nonexistence. "
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
            "Read details of evidence ids already returned by search_campus.",
            ReadQuery.model_json_schema(),
        ),
    ]
