"""The tool catalogue: what each tool is called, what it does, and the JSON
schema for its arguments.

This is a data table, not logic. It is also the single source of truth for
argument validation — `validation.validate_arguments` re-checks model output
against the very same `properties` advertised here, so a tool cannot drift
into accepting something it never advertised.
"""

from __future__ import annotations

from typing import Any

from rockygpt_brain.brain.tools.handlers import (
    search_academic_dates,
    search_campus_hours,
    search_clubs,
    search_contacts,
    search_dining_hours,
    search_events,
    search_map,
    search_menu,
    search_programs,
    search_shuttles,
)
from rockygpt_brain.brain.tools.payload import ToolDefinition

_DAY_ENUM = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_SERVICE_DAY_ENUM = ["weekday", "saturday", "sunday"]

TOOL_DEFINITIONS: list[ToolDefinition] = [
    ToolDefinition(
        name="search_campus_hours",
        description="Search official campus facility hours (offices, libraries, gyms, etc).",
        parameters={
            "type": "object",
            "properties": {
                "q": {
                    "type": "string",
                    "maxLength": 200,
                    "description": "Facility name/keywords.",
                },
                "day": {"type": "string", "enum": _DAY_ENUM},
            },
            "additionalProperties": False,
        },
        handler=search_campus_hours,
    ),
    ToolDefinition(
        name="search_dining_hours",
        description="Search dining hall / cafe hours.",
        parameters={
            "type": "object",
            "properties": {
                "q": {"type": "string", "maxLength": 200},
                "day": {"type": "string", "enum": _DAY_ENUM},
            },
            "additionalProperties": False,
        },
        handler=search_dining_hours,
    ),
    ToolDefinition(
        name="search_menu",
        description="Search structured dining menu items.",
        parameters={
            "type": "object",
            "properties": {
                "q": {"type": "string", "maxLength": 200},
                "meal": {"type": "string", "maxLength": 64},
            },
            "additionalProperties": False,
        },
        handler=search_menu,
    ),
    ToolDefinition(
        name="search_contacts",
        # Measured: "I'm walking alone at night" and "Can someone walk me to my
        # car?" produced **zero tool calls** — read as conversation rather than
        # as a request for a campus phone number, so the Public Safety record
        # was never looked up even though it answers both. A student in that
        # situation is asking who to call; say so, because the previous wording
        # described the table's contents and not the questions it answers.
        description=(
            "Search campus offices, departments, staff and faculty for a phone "
            "number, email, or office location. Use this whenever someone asks "
            "who to call, who to contact, or who can help with something on "
            "campus — including when they describe a situation rather than "
            "naming an office ('I'm walking alone at night', 'I'm locked out', "
            "'someone should walk me to my car'), since Public Safety and other "
            "offices provide those services. Argument `q` is a free-text search "
            "over office and person names; a plain description of the need "
            "('safety escort', 'walking alone') works."
        ),
        parameters={
            "type": "object",
            "properties": {"q": {"type": "string", "maxLength": 200}},
            "additionalProperties": False,
        },
        handler=search_contacts,
    ),
    ToolDefinition(
        name="search_clubs",
        description="Search student clubs and organizations.",
        parameters={
            "type": "object",
            "properties": {"q": {"type": "string", "maxLength": 200}},
            "additionalProperties": False,
        },
        handler=search_clubs,
    ),
    ToolDefinition(
        name="search_events",
        # Measured: 21% of search_events calls were rejected, and every
        # rejection was an undeclared argument — `day` twelve times, `date`
        # four. The cause is this description saying nothing about time while
        # sibling tools (search_dining_hours, search_menu, search_shuttles) all
        # take a narrowing argument, so a question about "today" invites one
        # here too. Results are already scoped to the current campus time by an
        # `at` value injected server-side, so the argument was never needed —
        # only unmentioned. Saying so is the fix; adding a `day` parameter would
        # mean building a filter the data service does not have.
        description=(
            "Search upcoming campus events. Results are already limited to "
            "events on and after the current campus date, so do not pass a "
            "date, day, or time argument — there is none. The only argument is "
            "`q`, an optional free-text filter on event title or organizer; "
            "omit it to see what is coming up."
        ),
        parameters={
            "type": "object",
            "properties": {"q": {"type": "string", "maxLength": 200}},
            "additionalProperties": False,
        },
        handler=search_events,
    ),
    ToolDefinition(
        name="search_programs",
        description="Search academic programs (majors, minors, certificates).",
        parameters={
            "type": "object",
            "properties": {"q": {"type": "string", "maxLength": 200}},
            "additionalProperties": False,
        },
        handler=search_programs,
    ),
    ToolDefinition(
        name="search_academic_dates",
        description="Search academic calendar dates (breaks, deadlines, terms).",
        parameters={
            "type": "object",
            "properties": {"q": {"type": "string", "maxLength": 200}},
            "additionalProperties": False,
        },
        handler=search_academic_dates,
    ),
    ToolDefinition(
        name="search_shuttles",
        description="Search shuttle/train-loop/Shortline trips.",
        parameters={
            "type": "object",
            "properties": {
                "route": {"type": "string", "maxLength": 120},
                "serviceDay": {"type": "string", "enum": _SERVICE_DAY_ENUM},
            },
            "additionalProperties": False,
        },
        handler=search_shuttles,
    ),
    ToolDefinition(
        name="search_map",
        description=(
            "Find campus buildings, offices, parking, and room locations. "
            "Use for any 'where is X' or 'how do I get to X' question. Each "
            "result's `key` is the locationKey for a VIEW_MAP uiAction."
        ),
        parameters={
            "type": "object",
            "properties": {"q": {"type": "string", "maxLength": 200}},
            "additionalProperties": False,
        },
        handler=search_map,
    ),
]

TOOL_HANDLERS: dict[str, ToolDefinition] = {tool.name: tool for tool in TOOL_DEFINITIONS}


def openai_tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in TOOL_DEFINITIONS
    ]
