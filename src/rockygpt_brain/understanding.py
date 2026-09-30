"""Step 4, Jev understanding: one Jev call reads the Turn and its Context.

Jev (Typesafe's reading model) answers six questions. Each is about the student's own words,
stands alone and asks one thing, so no answer leans on another. Jev never writes anything; code
only turns its six answers into an `Understanding`. This does not spend money safely yet: no
spending ledger stands in front of it, so nothing in the Brain calls it.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from rockygpt_brain.context import Context
from rockygpt_brain.turn import Turn

URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-1.13.0"

NEEDS = {
    "campus_info": "Facts about Ramapo College: places, offices, people, hours, shuttles, "
                   "dining, events or rules, even if the earlier messages were needed to "
                   "understand the request",
    "conversation": "The answer itself is in the earlier messages: what was said or asked "
                    "before in this chat",
    "outside": "General knowledge that has nothing to do with Ramapo College",
    "own_account": "The student's own private records, or an action on their own account",
    "unclear": "Even after using all available context, it is still not possible to tell which "
               "source would answer it",
}
TOPICS = {
    "transport": "Shuttles, buses, parking or getting around",
    "dining": "Food, dining halls, menus or meal plans",
    "offices": "A campus office, department or service and how to reach it",
    "academics": "Classes, grades, registration, degrees or professors",
    "events": "Events, clubs or things happening on campus",
    "housing": "Dorms, residence halls or living on campus",
    "none": "None of these, or no campus subject at all",
}
QUESTIONS: dict[str, dict[str, Any]] = {
    "needs": {
        "type": "choice",
        "instructions": "Once any earlier references are resolved, which source would answer "
                        "the request?",
        "criteria": NEEDS,
    },
    "topic": {
        "type": "choice",
        "instructions": "Which campus subject is the student asking about right now, counting what "
                        "the earlier messages make clear?",
        "criteria": TOPICS,
    },
    "needs_history": {
        "type": "noul",
        "instructions": "The latest message depends on earlier conversation either to identify "
                        "what it means or to get information from that conversation.",
        "criteria": {
            "true": "It refers back to earlier conversation, either vaguely (such as it, that, "
                    "they, there, the one), by leaving out what it is about, or explicitly "
                    "(such as asking what was said earlier)",
            "false": "It can be understood and answered without earlier conversation",
        },
    },
    "history_resolves": {
        "type": "noul",
        "instructions": "The earlier messages say what the latest message refers back to.",
        "criteria": {
            "true": "What it points at or leaves out (it, that, the one, a topic or a question) "
                    "is named in the earlier messages",
            "false": "Nothing earlier says what it points at or leaves out, or there are no "
                     "earlier messages",
        },
    },
    "danger": {
        "type": "noul",
        "instructions": "The latest message suggests someone is in immediate danger, or wants to "
                        "hurt themselves or someone else.",
        "criteria": {
            "true": "Someone is in danger right now, or wants to hurt themselves or another person",
            "false": "Nothing suggests anyone is in immediate danger",
        },
    },
    "multi_part": {
        "type": "noul",
        "instructions": "The latest message asks for two or more separate things.",
        "criteria": {
            "true": "It contains more than one separate request or question",
            "false": "It asks for one thing",
        },
    },
}


@dataclass(frozen=True, slots=True)
class Understanding:
    needs: str
    topic: str
    needs_history: bool
    history_resolves: bool
    danger: bool
    multi_part: bool


class JevError(Exception):
    """Jev could not be asked, or did not answer in the expected shape."""


def send(body: dict[str, Any]) -> dict[str, Any]:
    """POST one request to Jev and return its JSON. A failed call raises httpx's error."""
    key = os.getenv("BRAIN_TYPESAFE_API_KEY")
    if not key:
        raise JevError("BRAIN_TYPESAFE_API_KEY is not set")
    response = httpx.post(URL, json=body, headers={"Authorization": f"Bearer {key}"}, timeout=4.0)
    response.raise_for_status()
    return response.json()  # type: ignore[no-any-return]


def request_body(turn: Turn, context: Context) -> dict[str, Any]:
    return {
        "model": MODEL,
        "state": {
            "latest_message": context.latest_message,
            "recent_messages": [
                {"role": m.role, "content": m.content} for m in context.recent_messages
            ],
            "current_topic": context.current_topic,
            "referenced_entities": list(context.referenced_entities),
            "campus_now": turn.campus_now.isoformat(),
        },
        "questions": QUESTIONS,
    }


def understand(
    turn: Turn, context: Context, post: Callable[[dict[str, Any]], dict[str, Any]] = send
) -> Understanding:
    """Ask Jev the six questions and read its top pick on each (yes at 0.5 or more)."""
    try:
        answers = post(request_body(turn, context))["answers"]
        needs, topic = answers["needs"]["choice"], answers["topic"]["choice"]
        yes = {name: answers[name]["noul"] >= 0.5
               for name in ("needs_history", "history_resolves", "danger", "multi_part")}
        if needs not in NEEDS or topic not in TOPICS:
            raise JevError(f"Jev picked an option that does not exist: {needs}, {topic}")
    except (KeyError, TypeError) as error:
        raise JevError("Jev's answer was not in the expected shape") from error
    return Understanding(needs, topic, **yes)
