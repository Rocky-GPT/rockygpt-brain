"""A follow-up that names its own place is looked up as that place (09-29).

After a Birch answer, "What's on the menu at the dining place in the Learning Commons
today?" needed the earlier messages to Jev, so GPT looked up Birch, or searched every
menu, and the next question inherited Birch. Asked alone, the same words reached the one
dining place in the building in 1.2 s.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import pytest

from rockygpt_brain.contracts import ChatMessage
from rockygpt_brain.core.routing import route_request
from rockygpt_brain.retrieval.profiles import Identity, ProfileQuery
from test_profiles import IDENTITY, repository
from test_routing import NOW, data_mock, identity, router_mock

TODAY = NOW.date().isoformat()
BUILDING = {**IDENTITY, "id": "00000000-0000-4000-8000-00000000b001", "kind": "building",
            "name": "Example Learning Commons", "aliases": ["Learning Commons"],
            "links": [{"collection": "buildings", "source_key": "map",
                       "source_record_keys": ["1133431"]}]}
CAFE = {**IDENTITY, "kind": "venue", "name": "Example Cafe", "aliases": [],
        "relationships": [{"type": "located_at", "target_entity_id": BUILDING["id"],
                           "evidence": [{"collection": "buildings", "source_key": "map",
                                         "source_record_key": "1133431",
                                         "field": "reviewed_locations"}]}]}
BIRCH = {**IDENTITY, "id": "00000000-0000-4000-8000-00000000b003", "kind": "venue",
         "name": "Birch Tree Inn", "aliases": ["Birch"],
         "links": [{"collection": "dining_hours", "source_key": "hours",
                    "source_record_keys": ["birch"]}]}
ENTITIES = [Identity.model_validate(entity) for entity in (BUILDING, CAFE, BIRCH)]
Q8 = "What’s on the menu at the dining place in the Learning Commons today?"


def after_birch(latest: str) -> list[ChatMessage]:
    return [
        ChatMessage(role="user", content="What can I actually eat on campus right now?"),
        ChatMessage(role="assistant", content="Birch Tree Inn's Late Night hours are "
                    "9:00-11:00 p.m. The menu lists cheese pizza and a black bean burger."),
        ChatMessage(role="user", content=latest),
    ]


def route(request: list[ChatMessage], **choices: Any) -> Any:
    choices = {"route": "profile", "entity": BUILDING["id"], "date": "named",
               "detail_menu": 0.99, "detail_contact": 0.0, **choices}
    return route_request(request, data=data_mock(*ENTITIES), client=router_mock(**choices),
                         now=NOW, timeout=2)


@pytest.mark.parametrize("entity", [BUILDING["id"], BIRCH["id"], "none"])
def test_a_follow_up_naming_a_place_looks_up_that_place(entity: str) -> None:
    # Jev put 0.89-0.94 on the profile route but said the request needed the earlier
    # messages. Whichever place Jev picks, the one the request names is the one looked up.
    decision = route(after_birch(Q8), entity=entity, needs_earlier=0.6)
    assert decision.tool == "lookup_profile" and decision.reason is None
    assert decision.arguments is not None
    assert decision.arguments["entity_id"] == BUILDING["id"]
    assert decision.arguments["include"] == ["menu", "hours"]
    assert decision.arguments["date"] == TODAY
    # Jev may have read what it asks from the earlier turns, so GPT writes the answer.
    assert decision.template is None and decision.answer_fields is None
    # The building's one dining place is what its menu lookup reads.
    data = repository()
    data._artifacts["campus-identities"]["entities"] = [BUILDING, CAFE, BIRCH]
    data._load = Mock(return_value=[])  # type: ignore[method-assign]
    menu = data.lookup_profile(ProfileQuery.model_validate(decision.arguments))
    assert menu["resolution"]["entity"]["name"] == "Example Cafe"
    assert menu["resolution"]["located_in"]["name"] == "Example Learning Commons"


def test_the_same_question_alone_is_still_looked_up_and_written_by_code() -> None:
    decision = route([ChatMessage(role="user", content=Q8)])
    assert decision.arguments is not None and decision.reason is None
    assert decision.arguments["entity_id"] == BUILDING["id"]
    assert decision.template == "menu"


@pytest.mark.parametrize("latest,entity", [
    # Naming nothing, a follow-up is GPT's unless Jev is sure who "it" is, as before.
    ("Is that place still open in 45 minutes?", "none"),
    ("What room is it in?", "none"),
    # A place named beside a word that points back: "it" may be the earlier place.
    ("Is it in the Learning Commons?", BIRCH["id"]),
    ("Is it in the Learning Commons?", BUILDING["id"]),
])
def test_a_follow_up_that_points_back_stays_with_gpt(latest: str, entity: str) -> None:
    decision = route(after_birch(latest), entity=entity, needs_earlier=0.9, date="none",
                     detail_menu=0.0, detail_hours=0.99, detail_location=0.99)
    assert decision.arguments is None and decision.tool is None
    assert decision.reason == "follow_up"


def test_is_there_asks_whether_something_exists() -> None:
    decision = route(after_birch("Is there a menu at the Learning Commons today?"),
                     entity="none", needs_earlier=0.6)
    assert decision.arguments is not None
    assert decision.arguments["entity_id"] == BUILDING["id"]


def test_a_follow_up_naming_no_day_may_mean_an_earlier_one() -> None:
    # "What about the Atrium?" after Birch's hours tomorrow means tomorrow.
    decision = route(after_birch("What's on the menu at the Learning Commons?"),
                     date="none", needs_earlier=0.6)
    assert decision.arguments is None and decision.reason == "follow_up"


def test_a_place_named_after_another_replaces_it() -> None:
    # The latest request names Birch after a Learning Commons answer: Birch is looked up
    # even though Jev still leans to the earlier place.
    request = [
        ChatMessage(role="user", content=Q8),
        ChatMessage(role="assistant", content="Example Cafe publishes no menu today."),
        ChatMessage(role="user", content="And what's on the menu at Birch today?"),
    ]
    decision = route(request, entity=BUILDING["id"], needs_earlier=0.5)
    assert decision.arguments is not None
    assert decision.arguments["entity_id"] == BIRCH["id"]


@pytest.mark.parametrize("latest", ["Where do I send the info?",
                                    "Is there an email for more info?"])
def test_a_subject_code_in_a_follow_up_is_an_everyday_word(latest: str) -> None:
    # INFO, READ and DATA are course codes too. After a Registrar answer, Jev's pick of the
    # Registrar stands and the turn stays with GPT, as it did before the rule above.
    registrar = identity(1, "office", "Registrar")
    info = identity(2, "subject", "Info Systems (INFO)", "INFO")
    request = [
        ChatMessage(role="user", content="What's the Registrar's phone number?"),
        ChatMessage(role="assistant", content="The Registrar's phone is 201-684-7695."),
        ChatMessage(role="user", content=latest),
    ]
    decision = route_request(
        request, data=data_mock(registrar, info),
        client=router_mock(route="contact", entity=str(registrar.id), needs_earlier=0.9,
                           date="none", detail_contact=0.99, detail_location=0.99),
        now=NOW, timeout=2)
    assert decision.arguments is None and decision.tool is None
    assert decision.reason == "follow_up"
