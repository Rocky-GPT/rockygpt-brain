"""Jev may select retrieval, but never bypass evidence, accounting or deadlines."""

import asyncio
import json
from datetime import datetime, time, timedelta
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx
import psycopg
import pytest

from rockygpt_brain.campus.formats import SAFETY_NET
from rockygpt_brain.config import RELEASE, Deployment
from rockygpt_brain.contracts import ChatMessage
from rockygpt_brain.core.engine import run_turn
from rockygpt_brain.core.provider import JevProvider, ModelResponse, PaidGateway, Usage
from rockygpt_brain.core.render import InvalidAnswer
from rockygpt_brain.core.routing import (
    FIELDS,
    GRAPH_TOOLS,
    ROUTED_SECTIONS,
    graph_first,
    interpret,
    named,
    route_request,
    routing_payload,
    shortlist,
    validate_answers,
)
from rockygpt_brain.governance.accounting import PaidCallError
from rockygpt_brain.retrieval.profiles import Identity
from test_engine import answer, review, search, tools
from test_general import SAFETY, urgent
from test_phase2 import result_for
from test_provider import arguments

# Noon on the first day both verified price windows (GPT and Jev) cover.
NOW = datetime.combine(
    max(RELEASE.price.valid_from, RELEASE.routing.price.valid_from), time(12),
    ZoneInfo("America/New_York"),
)
ENTITY = Identity.model_validate(
    {
        "id": "00000000-0000-0000-0000-000000000001",
        "kind": "office",
        "name": "Registrar",
        "aliases": ["Registration Office"],
        "links": [
            {
                "collection": "contacts",
                "source_key": "directory",
                "source_record_keys": ["registrar"],
            }
        ],
    }
)


def messages(text: str = "What is the Registrar phone?") -> list[ChatMessage]:
    return [ChatMessage(role="user", content=text)]


def answers_for(payload: dict[str, Any], **selections: Any) -> dict[str, Any]:
    choices = {
        "route": "contact",
        "entity": str(ENTITY.id),
        "date": "unspecified",
        "meal": "unspecified",
        "topic": "contact",
        "danger": "none",
        **selections,
    }
    answers: dict[str, Any] = {}
    for key, question in payload["questions"].items():
        if question["type"] == "choice":
            selected = choices.get(key, "unresolved")
            answers[key] = {
                "type": "choice",
                "choice": selected,
                "confidence": 1.0,
                "probabilities": {
                    option: float(option == selected) for option in question["criteria"]
                },
            }
        else:
            answers[key] = {"type": "noul", "noul": choices.get(key, 0.0)}
    return answers


def data_mock(*entities: Identity) -> Mock:
    data = Mock()
    data.deadline = None
    # Like an offline repository: no release to fingerprint, so nothing is cached.
    data.release_fingerprint.return_value = None
    data._artifact.return_value = {
        "schema_version": 1,
        "entities": [entity.model_dump(mode="json") for entity in entities or [ENTITY]],
    }
    return data


def router_mock(**choices: Any) -> Mock:
    router = Mock()
    router.route.side_effect = lambda payload, **kwargs: answers_for(payload, **choices)
    return router


def contact_record() -> dict[str, Any]:
    record: dict[str, Any] = json.loads(
        (Path(__file__).parents[1] / "docs/phase2/published-contact.json").read_text()
    )["contact_search"]["records"][0]
    record.update(
        entity_id="campus-directory:office:registrar",
        freshness="fresh",
        collected_at=NOW.isoformat(),
        valid_from=None,
        valid_until=None,
    )
    record["coverage"] = {"fields": {key: "published" for key in record["fields"]}}
    return record


def test_direct_contact_uses_only_jev_and_preserves_citations() -> None:
    data, router, gpt = data_mock(), router_mock(), Mock()
    record = contact_record()
    data.lookup_contact.return_value = result_for([record])
    progress = Mock()
    result = run_turn(
        messages(),
        client=gpt,
        data=data,
        model=RELEASE.model,
        now=NOW,
        routing_client=router,
        routing_mode="active",
        progress=progress,
    )
    assert result["status"] == "answered"
    assert "201-684-7695" in result["answer"]
    assert result["model"] == RELEASE.routing.model
    assert result["metrics"]["modelCalls"] == result["metrics"]["routingCalls"] == 1
    assert result["metrics"]["draftCalls"] == result["metrics"]["reviewCalls"] == 0
    assert result["metrics"]["routing"]["directRetrieval"] is True
    assert result["citations"][0]["url"] == record["url"]
    gpt.create.assert_not_called()
    router.route.assert_called_once()
    assert "retrieving" in [call.args[0]["stage"] for call in progress.call_args_list]


def test_direct_profile_preserves_review_and_allows_more_retrieval() -> None:
    data = data_mock()
    record = contact_record()
    data.lookup_profile.return_value = result_for([record])
    data.search.return_value = result_for([record])
    gpt = Mock()
    gpt.create.side_effect = [
        tools(search()),
        answer("Office D-224", "campus_fact", [record["id"]]),
        review(),
    ]
    result = run_turn(
        messages("Tell me about the Registrar"),
        client=gpt,
        data=data,
        model=RELEASE.model,
        now=NOW,
        routing_client=router_mock(route="profile"),
        routing_mode="active",
    )
    assert result["metrics"]["routing"]["directRetrieval"]
    assert result["metrics"]["reviewCalls"] == 1
    assert result["metrics"]["modelCalls"] == 4
    assert result["model"] == "test-model"
    first = gpt.create.call_args_list[0].kwargs
    assert first["tool_choice"] == "auto" and len(first["tools"]) == 6
    assert first["input"][0] == messages("Tell me about the Registrar")[0].model_dump()
    assert first["input"][2].name == "lookup_profile"
    assert first["input"][3]["call_id"] == first["input"][2].call_id
    assert record["url"] in first["input"][3]["output"]


@pytest.mark.parametrize("mode", ["off", "shadow"])
def test_off_and_shadow_do_not_change_tool_selection(mode: Any) -> None:
    data, router, gpt = data_mock(), router_mock(), Mock()
    gpt.create.side_effect = [answer(), review()]
    result = run_turn(
        messages(),
        client=gpt,
        data=data,
        model=RELEASE.model,
        now=NOW,
        routing_client=router,
        routing_mode=mode,
    )
    data.lookup_contact.assert_not_called()
    # Jev chose lookup_contact alone; both modes ignore it and apply only the
    # graph-first rule, since the request names the Registrar.
    first = gpt.create.call_args_list[0].kwargs
    assert first["tool_choice"] == "required"
    assert [tool["name"] for tool in first["tools"]] == list(GRAPH_TOOLS)
    assert router.route.call_count == (mode == "shadow")
    assert result["metrics"]["modelCalls"] == 2 + (mode == "shadow")


def test_confident_route_without_arguments_constrains_only_first_call() -> None:
    data, gpt = data_mock(), Mock()
    record = contact_record()
    data.search.return_value = result_for([record])
    gpt.create.side_effect = [
        tools(search()),
        answer("Office D-224", "campus_fact", [record["id"]]),
        review(),
    ]
    run_turn(
        messages(),
        client=gpt,
        data=data,
        model=RELEASE.model,
        now=NOW,
        routing_client=router_mock(route="search"),
        routing_mode="active",
    )
    first, second = [call.kwargs for call in gpt.create.call_args_list[:2]]
    assert first["tool_choice"] == {"type": "function", "name": "search_campus"}
    assert [tool["name"] for tool in first["tools"]] == ["search_campus"]
    assert second["tool_choice"] == "auto" and len(second["tools"]) == 6


@pytest.mark.parametrize(
    "mutation",
    [
        {"freshness": "stale"},
        {"trust_tier": "community"},
        {"content_truncated": True},
        {"coverage": {}},
        {"valid_until": "2000-01-01"},
    ],
)
def test_direct_contact_does_not_relax_exact_validation(mutation: dict[str, Any]) -> None:
    data, gpt = data_mock(), Mock()
    record = contact_record()
    record.update(mutation)
    data.lookup_contact.return_value = result_for([record])
    result = run_turn(
        messages(),
        client=gpt,
        data=data,
        model=RELEASE.model,
        now=NOW,
        routing_client=router_mock(),
        routing_mode="active",
    )
    assert result["status"] == "unavailable"
    assert "201-684-7695" not in result["answer"]
    gpt.create.assert_not_called()


def test_rejected_prose_after_direct_lookup_is_not_returned() -> None:
    data, gpt = data_mock(), Mock()
    record = contact_record()
    data.lookup_profile.return_value = result_for([record])
    gpt.create.side_effect = [
        answer("invented claim", "campus_fact", [record["id"]]),
        review("unsupported_claim"),
    ]
    result = run_turn(
        messages("Describe the Registrar"),
        client=gpt,
        data=data,
        model=RELEASE.model,
        now=NOW,
        routing_client=router_mock(route="profile"),
        routing_mode="active",
    )
    assert result["status"] == "unavailable" and "invented claim" not in result["answer"]


@pytest.mark.parametrize("score,confident", [(0.8999, False), (0.90, True), (1.0, True)])
def test_choice_requires_both_probability_and_confidence(score: float, confident: bool) -> None:
    payload, day = routing_payload(messages(), [ENTITY], NOW)
    for field in ("probability", "confidence"):
        answers = answers_for(payload)
        if field == "confidence":
            answers["route"]["confidence"] = score
        else:
            answers["route"]["probabilities"]["contact"] = score
            answers["route"]["probabilities"]["unresolved"] = 1 - score
        validate_answers(answers, payload["questions"])
        assert (interpret(answers, [ENTITY], day, messages()).arguments is not None) == confident


def test_a_contact_lookup_fetches_every_field() -> None:
    # Jev's yes/no answers about single fields sat between 0.5 and 0.8 whether or not the
    # field was asked, so the payload no longer asks them.
    payload, day = routing_payload(messages(), [ENTITY], NOW)
    assert {question["type"] for question in payload["questions"].values()} == {"choice"}
    result = interpret(answers_for(payload), [ENTITY], day, messages())
    assert result.arguments is not None and result.arguments["fields"] == list(FIELDS)
    assert result.tool == "lookup_contact" and result.reason is None


def test_a_contact_request_is_looked_up_by_jev_alone() -> None:
    data, gpt = data_mock(), Mock()
    record = contact_record()
    data.lookup_contact.return_value = result_for([record])
    result = run_turn(
        messages("How can I contact Registrar?"),
        client=gpt,
        data=data,
        model=RELEASE.model,
        now=NOW,
        routing_client=router_mock(),
        routing_mode="active",
    )
    assert data.lookup_contact.call_args.args[0].fields == list(FIELDS)
    assert "201-684-7695" in result["answer"]
    assert result["metrics"]["routing"]["directRetrieval"] is True
    gpt.create.assert_not_called()


@pytest.mark.parametrize("changes", [{"entity": "unresolved"}, {"route": "unresolved"}])
def test_ambiguity_and_mixed_requests_defer(changes: dict[str, Any]) -> None:
    payload, day = routing_payload(messages(), [ENTITY], NOW)
    result = interpret(answers_for(payload, **changes), [ENTITY], day, messages())
    # GPT keeps every tool, since the request may need more than one lookup.
    assert result.arguments is None and result.tool is None


def test_duplicate_aliases_never_select_an_arbitrary_identity() -> None:
    other = ENTITY.model_copy(
        update={"id": UUID(int=2), "name": "Other Office", "aliases": ["Registrar"]}
    )
    payload, day = routing_payload(messages(), [ENTITY, other], NOW)
    result = interpret(answers_for(payload), [ENTITY, other], day, messages())
    assert result.arguments is None and result.tool is None


def leaning(answers: dict[str, Any], leading: str, probability: float) -> dict[str, Any]:
    """Jev picks `leading` at `probability` and spreads the rest over the other options."""
    options = answers["entity"]["probabilities"]
    rest = (1 - probability) / (len(options) - 1)
    answers["entity"].update(
        choice=leading,
        confidence=probability,
        probabilities={option: probability if option == leading else rest for option in options},
    )
    return answers


OTHER = Identity.model_validate(
    {**ENTITY.model_dump(mode="json"), "id": str(UUID(int=2)), "name": "Bursar", "aliases": []}
)


@pytest.mark.parametrize(
    "text,leading,probability,direct",
    [
        # Jev was 79% sure "What is the Registrar phone?" meant the Registrar.
        ("What is the Registrar phone?", str(ENTITY.id), 0.79, True),
        ("What is the Registrar phone?", str(ENTITY.id), 0.5, True),
        ("What is the Registrar phone?", str(ENTITY.id), 0.4999, False),
        ("What is the Registrar phone?", "unresolved", 0.79, False),
        # Jev leaning toward an entity the request doesn't name never replaces the named one.
        ("What is the Registrar phone?", str(OTHER.id), 0.79, False),
        # A follow-up names nothing, so Jev's own pick must clear the threshold.
        ("What is their phone?", str(ENTITY.id), 0.79, False),
    ],
)
def test_a_named_entity_needs_only_jevs_leaning_pick(
    text: str, leading: str, probability: float, direct: bool
) -> None:
    request = messages(text)
    payload, day = routing_payload(request, [ENTITY, OTHER], NOW)
    answers = leaning(answers_for(payload), leading, probability)
    validate_answers(answers, payload["questions"])
    result = interpret(answers, [ENTITY, OTHER], day, request)
    assert (result.arguments is not None) == direct


def identity(number: int, kind: str, name: str, *aliases: str) -> Identity:
    # Each identity links its own record, so several can share one valid registry.
    return Identity.model_validate(
        {
            "id": str(UUID(int=number)),
            "kind": kind,
            "name": name,
            "aliases": list(aliases),
            "links": [
                {
                    "collection": "contacts",
                    "source_key": "directory",
                    "source_record_keys": [f"entity-{number}"],
                }
            ],
        }
    )


CS_BS = identity(2, "program", "Computer Science BS", "Computer Science")
CS_MS = identity(3, "program", "Computer Science MS", "Computer Science")
CS_CLUB = identity(4, "club", "Computer Science Club")
BIRCH = identity(5, "venue", "Birch Tree Inn", "Birch")
MANSION = identity(6, "building", "Birch Mansion")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Who is the convener of the Computer Science BS program?", [CS_BS]),
        ("Tell me about the Computer Science Club.", [CS_CLUB]),
        ("Where is Birch Mansion?", [MANSION]),
        ("What's the phone number for Birch?", [BIRCH]),
        ("Who convenes Computer Science?", [CS_BS, CS_MS]),
        ("Compare the Computer Science BS and the Computer Science MS.", [CS_BS, CS_MS]),
        # A separate mention of the shorter name still counts.
        ("Tell me about Computer Science and the Computer Science Club.", [CS_BS, CS_MS, CS_CLUB]),
    ],
)
def test_a_longer_name_hides_only_the_names_inside_it(text: str, expected: list[Identity]) -> None:
    assert named(text, [CS_BS, CS_MS, CS_CLUB, BIRCH, MANSION]) == expected


def test_one_named_entity_among_overlapping_names_can_route_directly() -> None:
    request = messages("Who is the convener of the Computer Science BS program?")
    candidates = [CS_BS, CS_MS, CS_CLUB]
    payload, day = routing_payload(request, candidates, NOW)
    answers = answers_for(payload, route="profile", entity=str(CS_BS.id), topic="about")
    result = interpret(answers, candidates, day, request)
    assert result.arguments is not None and result.arguments["entity_id"] == str(CS_BS.id)
    # Jev must select the entity the request names; any other leaves the lookup to GPT.
    answers = answers_for(payload, route="profile", entity=str(CS_MS.id), topic="about")
    result = interpret(answers, candidates, day, request)
    assert result.arguments is None and result.tool is None


MATH = identity(7, "subject", "Mathematics (MATH)", "MATH")


def profile_call(entity: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="function_call",
        name="lookup_profile",
        call_id="profile",
        arguments=json.dumps({"entity": entity, "include": ["contact"]}),
    )


def test_a_request_naming_one_identity_reads_the_graph_first() -> None:
    data, gpt = data_mock(), Mock()
    record = contact_record()
    data.lookup_profile.return_value = result_for([record])
    gpt.create.side_effect = [
        tools(profile_call("Registrar")),
        answer("Office D-224", "campus_fact", [record["id"]]),
        review(),
    ]
    result = run_turn(
        messages("Tell me about the Registrar"), client=gpt, data=data, model=RELEASE.model, now=NOW
    )
    first, second = [call.kwargs for call in gpt.create.call_args_list[:2]]
    assert first["tool_choice"] == "required"
    assert [tool["name"] for tool in first["tools"]] == ["lookup_profile", "lookup_contact"]
    assert second["tool_choice"] == "auto" and len(second["tools"]) == 6
    assert result["metrics"]["graphFirst"] is True
    data.lookup_profile.assert_called_once()


@pytest.mark.parametrize(
    "conversation",
    [
        messages("What are the shuttle departures tomorrow?"),
        messages("Compare the Registrar and the Computer Science BS."),
        messages("Which MATH courses are in the catalog?"),
        [
            *messages(),
            ChatMessage(role="assistant", content="The Registrar's phone is published."),
            ChatMessage(role="user", content="What is their email?"),
        ],
    ],
    ids=["no identity", "two identities", "a subject only", "unnamed follow-up"],
)
def test_other_requests_keep_every_tool_in_the_first_call(
    conversation: list[ChatMessage],
) -> None:
    data, gpt = data_mock(ENTITY, CS_BS, MATH), Mock()
    gpt.create.side_effect = [answer(), review()]
    result = run_turn(conversation, client=gpt, data=data, model=RELEASE.model, now=NOW)
    first = gpt.create.call_args_list[0].kwargs
    assert first["tool_choice"] == "auto" and len(first["tools"]) == 6
    assert "graphFirst" not in result["metrics"]


def test_a_subject_beside_one_identity_still_starts_with_the_graph() -> None:
    request = messages("How many MATH electives does the Computer Science BS require?")
    assert graph_first(request, data_mock(ENTITY, CS_BS, MATH))


def test_active_routing_replaces_the_graph_first_rule() -> None:
    data, gpt = data_mock(), Mock()
    gpt.create.side_effect = [answer(), review()]
    result = run_turn(
        messages(),
        client=gpt,
        data=data,
        model=RELEASE.model,
        now=NOW,
        routing_client=router_mock(route="unresolved"),
        routing_mode="active",
    )
    first = gpt.create.call_args_list[0].kwargs
    assert first["tool_choice"] == "auto" and len(first["tools"]) == 6
    assert "graphFirst" not in result["metrics"]


def test_an_unreadable_registry_keeps_the_ordinary_first_call() -> None:
    data = data_mock()
    data.release_fingerprint.side_effect = psycopg.OperationalError("campus database unavailable")
    assert not graph_first(messages(), data)
    for payload in ({"schema_version": 1, "entities": "invalid"}, None):
        data = data_mock()
        data._artifact.return_value = payload
        assert not graph_first(messages(), data)


def test_the_name_index_is_built_once_per_release() -> None:
    data = data_mock()
    data.release_fingerprint.return_value = ("test-release", str(uuid4()))
    assert graph_first(messages(), data) and graph_first(messages(), data)
    data._artifact.assert_called_once_with("campus-identities")


def test_shortlist_prioritizes_latest_exact_match_and_caps_candidates() -> None:
    entities = [
        ENTITY.model_copy(update={"id": UUID(int=i + 2), "name": f"Registrar {i}", "aliases": []})
        for i in range(40)
    ]
    ranked = shortlist([*entities, ENTITY], messages())
    assert ranked[0] == ENTITY and len(ranked) == 24
    followup = [
        *messages(),
        ChatMessage(role="assistant", content="Registrar has a directory."),
        ChatMessage(role="user", content="What is their email?"),
    ]
    assert ENTITY in shortlist([ENTITY], followup)
    payload, _ = routing_payload(followup, [ENTITY], NOW)
    assert payload["state"]["latest_request"] == "What is their email?"
    assert payload["state"]["prior_messages"] == [message.model_dump() for message in followup[:-1]]


def spread(answers: dict[str, Any], key: str, **probabilities: float) -> dict[str, Any]:
    """Jev splits `key` as given, with any remainder spread over the other options."""
    options = answers[key]["probabilities"]
    rest = (1 - sum(probabilities.values())) / (len(options) - len(probabilities))
    distribution = {option: probabilities.get(option, rest) for option in options}
    leading = max(distribution, key=lambda option: distribution[option])
    answers[key].update(
        choice=leading, confidence=distribution[leading], probabilities=distribution
    )
    return answers


def test_dates_meals_and_complete_menu_are_bounded() -> None:
    request = messages("Registrar menu and hours tomorrow for dinner")
    payload, day = routing_payload(request, [ENTITY], NOW)
    assert day == (NOW.date() + timedelta(days=1)).isoformat()
    answers = answers_for(
        payload, route="profile", date="explicit", meal="dinner", topic="complete_menu"
    )
    validate_answers(answers, payload["questions"])
    result = interpret(answers, [ENTITY], day, request)
    assert result.arguments is not None
    assert result.arguments["date"] == day and result.arguments["meal"] == "dinner"
    assert result.arguments["menu_limit"] == 100
    assert result.arguments["include"] == ["menu", "hours"]
    # Jev only has to flag a date the resolver can't read.
    answers["date"]["choice"] = "unresolved"
    assert interpret(answers, [ENTITY], day, request).arguments is None


@pytest.mark.parametrize(
    "probabilities,include,menu_limit",
    [
        ({"hours": 0.95}, ["hours"], None),
        # Jev split "Library hours and phone number" between two topics.
        ({"hours": 0.6, "contact": 0.3}, ["hours", "contact"], None),
        ({"menu": 0.7, "complete_menu": 0.25}, ["menu", "hours"], 12),
        ({"complete_menu": 0.6, "menu": 0.35}, ["menu", "hours"], 100),
        ({"location": 0.92}, ["building", "contact"], None),
        ({"about": 0.93}, ["faculty", "courses", "program", "conveners", "club"], None),
        # Too spread out, or partly unresolved: fetch everything the router may fetch.
        ({"hours": 0.4, "contact": 0.3, "events": 0.15}, list(ROUTED_SECTIONS), 12),
        ({"hours": 0.7, "unresolved": 0.25}, list(ROUTED_SECTIONS), 12),
    ],
)
def test_a_profile_lookup_fetches_jevs_leading_topics(
    probabilities: dict[str, float], include: list[str], menu_limit: int | None
) -> None:
    request = messages("What does the Registrar have today?")
    payload, day = routing_payload(request, [ENTITY], NOW)
    answers = spread(answers_for(payload, route="profile"), "topic", **probabilities)
    validate_answers(answers, payload["questions"])
    arguments = interpret(answers, [ENTITY], day, request).arguments
    assert arguments is not None and arguments["include"] == include
    assert (arguments["menu_limit"] if "menu" in include else None) == menu_limit


def test_a_weekday_that_has_passed_this_week_is_left_to_gpt() -> None:
    # Asked on a Sunday, "Saturday" resolved to the day before and fetched past hours.
    data, router = data_mock(), router_mock(route="profile", topic="hours")
    yesterday, tomorrow = NOW - timedelta(days=1), NOW + timedelta(days=1)
    assert yesterday.weekday() < NOW.weekday() < tomorrow.weekday()  # All in one week.
    request = messages(f"When is the Registrar open on {yesterday:%A}?")
    decision = route_request(request, data=data, client=router, now=NOW, timeout=2)
    assert decision.arguments is None and decision.tool is None
    assert decision.route == "profile" and decision.reason == "past_date"
    request = messages(f"When is the Registrar open on {tomorrow:%A}?")
    decision = route_request(request, data=data, client=router, now=NOW, timeout=2)
    assert decision.arguments is not None
    assert decision.arguments["date"] == tomorrow.date().isoformat()


def test_a_topic_without_a_date_ignores_an_unreadable_date() -> None:
    # "Who convenes the program the week after Thanksgiving?" needs no date to look up.
    request = messages("Who convenes the Registrar the week after Thanksgiving?")
    payload, day = routing_payload(request, [ENTITY], NOW)
    answers = answers_for(payload, route="profile", topic="about", date="unresolved")
    assert interpret(answers, [ENTITY], day, request).arguments is not None
    answers = answers_for(payload, route="profile", topic="hours", date="unresolved")
    assert interpret(answers, [ENTITY], day, request).arguments is None


def test_a_building_location_reads_the_building() -> None:
    # "Where is Birch Mansion?" found nothing when the router could not fetch buildings.
    request = messages("Where is Birch Mansion?")
    payload, day = routing_payload(request, [MANSION], NOW)
    answers = answers_for(payload, route="profile", entity=str(MANSION.id), topic="location")
    arguments = interpret(answers, [MANSION], day, request).arguments
    assert arguments is not None and "building" in arguments["include"]


def test_a_follow_up_never_looks_things_up_itself() -> None:
    # "Actually, what is the Financial Aid phone?" names its entity, but still follows a
    # conversation, so GPT runs the lookup.
    followup = [
        *messages(),
        ChatMessage(role="assistant", content="The Registrar's phone is 201-684-7695."),
        ChatMessage(role="user", content="Actually, what is the Registrar phone?"),
    ]
    payload, day = routing_payload(followup, [ENTITY], NOW)
    result = interpret(answers_for(payload), [ENTITY], day, followup)
    assert result.arguments is None
    assert result.tool is None and result.reason == "follow_up"


@pytest.mark.parametrize("pick,danger", [("self_harm", "self_harm"), ("danger", "danger"),
                                         ("none", None)])
def test_jevs_danger_pick_is_its_top_choice(pick: str, danger: str | None) -> None:
    decision = route_request(messages("I don't want to be alive anymore."), data=data_mock(),
                             client=router_mock(route="unresolved", entity="unresolved",
                                                danger=pick), now=NOW, timeout=2)
    assert decision.danger == danger
    assert decision.metrics("active")["danger"] == danger


def test_a_late_routing_answer_still_flags_danger(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [0.0]
    monkeypatch.setattr("rockygpt_brain.core.routing.monotonic", lambda: clock[0])
    router = Mock()

    def late(payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        clock[0] = 10.0  # Jev answers after the routing deadline.
        return answers_for(payload, danger="danger")

    router.route.side_effect = late
    decision = route_request(messages(), data=data_mock(), client=router, now=NOW, timeout=2)
    assert decision.reason == "routing_timeout" and decision.tool is None
    assert decision.danger == "danger"


def safety_turn(client: Mock, *, danger: str = "self_harm", mode: Any = "active",
                text: str = "I don't want to be alive anymore.") -> dict[str, Any]:
    data = data_mock()
    data.search.return_value = {"status": "ok", "dataset_version": "v1", "records": SAFETY}
    return run_turn(messages(text), client=client, data=data, model=RELEASE.model, now=NOW,
                    routing_client=router_mock(route="unresolved", entity="unresolved",
                                               danger=danger),
                    routing_mode=mode)


PUBLIC_SAFETY = "Ramapo College Public Safety: emergency 201-684-6666; non-emergency 201-684-7432."
SAFETY_IDS = ["critical_facts:safety.emergency_phone", "critical_facts:safety.non_emergency_phone"]


@pytest.mark.parametrize("danger", ["self_harm", "danger"])
def test_jevs_danger_pick_puts_the_safety_block_first(danger: str) -> None:
    gpt = Mock()
    gpt.create.side_effect = [answer("Talking with someone you trust can help."), review()]
    result = safety_turn(gpt, danger=danger)
    guidance, numbers, reply = result["answer"].split("\n\n")
    assert guidance == SAFETY_NET[danger]
    assert numbers.startswith(PUBLIC_SAFETY)
    assert reply == "Talking with someone you trust can help."
    assert [citation["id"] for citation in result["citations"]] == SAFETY_IDS
    assert result["status"] == "answered"
    assert result["metrics"]["responseMode"] == "reviewed_prose"
    assert result["metrics"]["safetyNet"] == {"kind": danger, "shown": True}


def test_an_unverified_answer_still_shows_the_safety_block() -> None:
    gpt = Mock()
    gpt.create.side_effect = [answer("Rocky can see your records."), review("unsupported_claim")]
    result = safety_turn(gpt)
    assert result["answer"].startswith(SAFETY_NET["self_harm"] + "\n\n" + PUBLIC_SAFETY)
    assert result["answer"].endswith("I couldn't verify a reliable answer from the available "
                                     "information.")
    assert result["status"] == "partial"
    assert result["metrics"]["responseMode"] == "safe_fallback"


@pytest.mark.parametrize(
    "failure,reason",
    [
        (SimpleNamespace(status="incomplete", model="test-model", output=[], output_text=""),
         "incomplete_draft"),
        (PaidCallError("model_provider_error"), "model_provider_error"),
        (TimeoutError("Turn deadline exceeded"), "model_timeout"),
    ],
)
def test_a_failed_answer_leaves_only_the_safety_block(failure: Any, reason: str) -> None:
    gpt = Mock()
    gpt.create.side_effect = [failure]
    result = safety_turn(gpt, danger="danger")
    guidance, numbers = result["answer"].split("\n\n")
    assert guidance == SAFETY_NET["danger"] and numbers.startswith(PUBLIC_SAFETY)
    assert result["status"] == "partial"
    assert result["datasetVersion"] == "v1"
    assert result["metrics"]["responseMode"] == "safety_net"
    assert result["metrics"]["fallbackReason"] == reason
    gpt.create.side_effect = [failure]
    with pytest.raises((InvalidAnswer, PaidCallError, TimeoutError)):
        safety_turn(gpt, danger="none")


def test_gpts_own_urgent_safety_answer_is_not_repeated() -> None:
    gpt = Mock()
    gpt.create.return_value = urgent("Call 911 now and move toward a busy, staffed place.")
    result = safety_turn(gpt, danger="danger")
    assert result["answer"].count("Public Safety") == 1
    assert SAFETY_NET["danger"] not in result["answer"]
    assert result["metrics"]["responseMode"] == "urgent_safety"
    assert result["metrics"]["safetyNet"] == {"kind": "danger", "shown": False}


def test_shadow_routing_records_danger_without_showing_the_block() -> None:
    gpt = Mock()
    gpt.create.side_effect = [answer("Talking with someone you trust can help."), review()]
    result = safety_turn(gpt, mode="shadow")
    assert result["answer"] == "Talking with someone you trust can help."
    assert result["metrics"]["routing"]["danger"] == "self_harm"
    assert "safetyNet" not in result["metrics"]


@pytest.mark.parametrize("mutation", ["missing", "nan", "unknown", "sum", "bool", "wrong_type"])
def test_invalid_provider_answers_fall_back(mutation: str) -> None:
    def malformed(payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        answers = answers_for(payload)
        if mutation == "missing":
            del answers["route"]
        elif mutation == "nan":
            answers["simple"]["noul"] = float("nan")
        elif mutation == "unknown":
            answers["route"]["choice"] = "invented_tool"
        elif mutation == "sum":
            answers["route"]["probabilities"]["contact"] = 0.4
        elif mutation == "bool":
            answers["simple"]["noul"] = True
        else:
            answers["route"]["type"] = "noul"
        return answers

    router = Mock()
    router.route.side_effect = malformed
    result = route_request(messages(), data=data_mock(), client=router, now=NOW, timeout=2)
    assert result.reason == "routing_invalid_response" and result.arguments is None


@pytest.mark.parametrize(
    "code",
    [
        "routing_provider_error",
        "routing_usage_unknown",
        "routing_model_changed",
        "routing_price_unavailable",
    ],
)
def test_optional_provider_failures_fall_back(code: str) -> None:
    router = Mock()
    router.route.side_effect = PaidCallError(code)
    result = route_request(messages(), data=data_mock(), client=router, now=NOW, timeout=2)
    assert result.reason == code and result.arguments is None


@pytest.mark.parametrize(
    "code",
    ["accounting_unavailable", "budget_exhausted", "turn_cost_limit", "accounting_bound_exceeded"],
)
def test_accounting_failures_never_become_fallback(code: str) -> None:
    router = Mock()
    router.route.side_effect = PaidCallError(code)
    with pytest.raises(PaidCallError, match=code):
        route_request(messages(), data=data_mock(), client=router, now=NOW, timeout=2)


def test_context_overflow_does_not_send_truncated_history() -> None:
    router = router_mock()
    request = messages("é" * 15000)
    result = route_request(request, data=data_mock(), client=router, now=NOW, timeout=2)
    assert result.reason == "routing_context_limit" and result.calls == 0
    router.route.assert_not_called()


def test_elapsed_routing_time_is_inside_existing_turn_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rockygpt_brain.core import engine
    from rockygpt_brain.core.routing import RouteDecision

    clock = [100.0]
    monkeypatch.setattr(engine, "monotonic", lambda: clock[0])

    def routed(*args: Any, **kwargs: Any) -> RouteDecision:
        clock[0] += 2
        return RouteDecision(reason="routing_timeout", calls=1)

    monkeypatch.setattr(engine, "route_request", routed)
    gpt = Mock()
    gpt.create.side_effect = [answer(), review()]
    run_turn(
        messages(),
        client=gpt,
        data=data_mock(),
        model=RELEASE.model,
        now=NOW,
        routing_client=router_mock(),
        routing_mode="active",
    )
    timeout = gpt.create.call_args_list[0].kwargs["timeout"].read
    assert timeout == RELEASE.turn_seconds - RELEASE.review_reserve_seconds - 2


def gateway_setup() -> tuple[PaidGateway, Mock, Mock, Mock]:
    provider, ledger, jev = Mock(), Mock(), Mock()
    jev.name = "typesafe"
    payload, _ = routing_payload(messages(), [ENTITY], NOW)
    jev.create.return_value = ModelResponse(
        "",
        RELEASE.routing.model,
        "completed",
        json.dumps(answers_for(payload)),
        [],
        Usage(100, 0, 200, 0),
    )
    gateway = PaidGateway(provider, ledger, "routing-test", routing_provider=jev, clock=lambda: NOW)
    return gateway, provider, ledger, jev


def test_routing_charges_input_only_and_preserves_gpt_capacity() -> None:
    gateway, _, ledger, jev = gateway_setup()
    payload, _ = routing_payload(messages(), [ENTITY], NOW)
    gateway.route(payload, timeout=2)
    assert ledger.reserve.call_args.args[2] == "routing"
    metadata = ledger.reserve.call_args.args[4]
    assert metadata["provider"] == "typesafe"
    assert metadata["price"]["output_nusd"] == 0
    assert "Registrar" not in json.dumps(metadata)
    assert ledger.settle.call_args.args[1] == 4200
    assert ledger.settle.call_args.args[3].startswith("local-operation:")
    assert gateway.usage.report()["routingCalls"] == 1
    assert gateway.usage.report()["modelCalls"] == 1
    assert gateway.budget.draft_calls == 0
    assert gateway.budget.can_retrieve
    with pytest.raises(PaidCallError, match="model_call_limit"):
        gateway.route(payload, timeout=2)
    jev.create.assert_called_once()


def test_routing_ledger_names_the_provider_that_was_paid() -> None:
    gateway, _, ledger, jev = gateway_setup()
    jev.name = "openrouter"
    gateway.route(routing_payload(messages(), [ENTITY], NOW)[0], timeout=2)
    assert ledger.reserve.call_args.args[4]["provider"] == "openrouter"


def test_uncertain_jev_charge_is_retained_before_gpt_fallback() -> None:
    gateway, provider, ledger, jev = gateway_setup()
    jev.create.side_effect = TimeoutError()
    payload, _ = routing_payload(messages(), [ENTITY], NOW)
    with pytest.raises(PaidCallError, match="routing_provider_error"):
        gateway.route(payload, timeout=2)
    ledger.uncertain.assert_called_once()
    assert gateway.usage.report()["unsettledNusd"] > 0
    provider.create.return_value = ModelResponse(
        "gpt-id", RELEASE.model, "completed", "", [], Usage(10, 0, 10, 0)
    )
    gateway.create(category="draft", **arguments())
    assert gateway.usage.report()["modelCalls"] == 2
    assert not gateway.usage.report()["usageComplete"]


def test_jev_settlement_failure_stops_further_work() -> None:
    gateway, provider, ledger, _ = gateway_setup()
    ledger.settle.side_effect = PaidCallError("accounting_unavailable")
    with pytest.raises(PaidCallError, match="accounting_unavailable"):
        gateway.route(routing_payload(messages(), [ENTITY], NOW)[0], timeout=2)
    provider.create.assert_not_called()


def test_jev_missing_usage_keeps_reservation() -> None:
    gateway, _, ledger, jev = gateway_setup()
    jev.create.return_value.usage = None
    with pytest.raises(PaidCallError, match="routing_usage_unknown"):
        gateway.route(routing_payload(messages(), [ENTITY], NOW)[0], timeout=2)
    ledger.settle.assert_not_called()
    ledger.uncertain.assert_called_once()


def test_jev_total_deadline_cancels_slow_response_body(monkeypatch: pytest.MonkeyPatch) -> None:
    closed = []

    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self) -> Any:
            try:
                yield b"{"
                await asyncio.sleep(1)
                yield b"}"
            finally:
                closed.append(True)

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=SlowBody()))
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport, **kwargs)
    )
    started = monotonic()
    with pytest.raises(TimeoutError):
        JevProvider("secret").create(
            timeout=0.03, model=RELEASE.routing.model, state="hello", questions={}
        )
    assert monotonic() - started < 0.5 and closed


def test_enabled_routing_requires_credential_and_secrets_stay_out_of_repr() -> None:
    values = dict(
        environment="development", api_key="gpt-secret", project="project", ledger_url="db"
    )
    assert Deployment.model_validate(values).routing_mode == "off"
    with pytest.raises(ValueError):
        Deployment.model_validate({**values, "routing_mode": "active"})
    deployment = Deployment.model_validate(
        {**values, "routing_mode": "active", "typesafe_api_key": "jev-secret"}
    )
    assert "jev-secret" not in repr(deployment)
    openrouter = {**values, "routing_mode": "active", "routing_provider": "openrouter"}
    with pytest.raises(ValueError):
        Deployment.model_validate({**openrouter, "typesafe_api_key": "jev-secret"})
    deployment = Deployment.model_validate({**openrouter, "openrouter_api_key": "or-secret"})
    assert deployment.routing_api_key == "or-secret"
    assert "or-secret" not in repr(deployment)


@pytest.mark.parametrize("status", [401, 429, 529])
def test_jev_http_failures_are_single_attempts(
    status: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, json={"error": "upstream"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    with pytest.raises(httpx.HTTPStatusError):
        JevProvider("secret").create(
            timeout=1, model=RELEASE.routing.model, state="hello", questions={}
        )
    assert len(requests) == 1
    assert requests[0].url == "https://api.typesafe.ai/v1/systemone"
    assert requests[0].headers["authorization"] == "Bearer secret"


@pytest.mark.parametrize(
    "reported,model",
    [
        ("typesafe/jev-1.13-20260917", RELEASE.routing.model),
        ("typesafe/jev-1.14-20261001", "typesafe/jev-1.14-20261001"),
    ],
)
def test_openrouter_requests_its_model_name_and_reports_only_the_pinned_snapshot(
    reported: str, model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests = []
    answers = {"simple": {"type": "noul", "noul": 0.97}}

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "gen-sys-1",
                "model": reported,
                "provider": "TypeSafe",
                "answers": answers,
                "usage": {"input_tokens": 40, "output_tokens": 3, "cost": 0.00000168},
            },
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    state = {"latest_request": "Is the Registrar open?"}
    questions = {"simple": {"type": "noul", "instructions": "Is this a simple lookup?"}}
    response = JevProvider("or-secret", "openrouter").create(
        timeout=1, model=RELEASE.routing.model, state=state, questions=questions
    )
    assert requests[0].url == "https://openrouter.ai/api/v1/systemone"
    assert requests[0].headers["authorization"] == "Bearer or-secret"
    assert json.loads(requests[0].content) == {
        "model": "typesafe/jev-1.13", "state": state, "questions": questions
    }
    assert response.id == "gen-sys-1" and response.model == model
    assert response.usage == Usage(40, 0, 3, 0)
    assert json.loads(response.output_text) == answers


def test_jev_model_drift_is_billed_but_not_used() -> None:
    gateway, _, ledger, jev = gateway_setup()
    jev.create.return_value.model = "jev-unreviewed"
    with pytest.raises(PaidCallError, match="routing_model_changed"):
        gateway.route(routing_payload(messages(), [ENTITY], NOW)[0], timeout=2)
    ledger.settle.assert_called_once()
    assert gateway.usage.report()["usageComplete"]


def test_database_failure_and_expired_routing_deadline_do_not_call_jev() -> None:
    data, router = data_mock(), router_mock()
    data._ensure_loaded.side_effect = RuntimeError("private connection details")
    result = route_request(messages(), data=data, client=router, now=NOW, timeout=2)
    assert result.reason == "routing_data_unavailable"
    assert "private" not in json.dumps(result.metrics("active"))
    data._ensure_loaded.side_effect = None
    result = route_request(messages(), data=data, client=router, now=NOW, timeout=0)
    assert result.reason == "routing_timeout"
    router.route.assert_not_called()
