"""Evidence review gate. A separate model context verifies factual citations."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from importlib.resources import files
from typing import Any

from httpx import Timeout
from pydantic import ValidationError

from rockygpt_brain.config import RELEASE
from rockygpt_brain.contracts import Answer, AnswerPart, ChatMessage, EvidenceReview
from rockygpt_brain.core.provider import ModelClient
from rockygpt_brain.core.render import InvalidAnswer
from rockygpt_brain.governance.evidence import compact_records, map_references, reference_aliases

REVIEW_INSTRUCTIONS = files("rockygpt_brain").joinpath("review.md").read_text(encoding="utf-8")


def review_answer(
    answer: Answer,
    *,
    messages: list[ChatMessage],
    evidence: dict[str, dict[str, Any]],
    client: ModelClient,
    model: str,
    now: datetime,
    timeout: float,
    verified_prefix: list[AnswerPart] | None = None,
    retrievals: list[dict[str, Any]] | None = None,
) -> EvidenceReview:
    """A separate context checks every part; draft/tool history cannot approve itself."""
    subjects = {
        record_id: {
            "name": record["title"],
            "published_category": record.get("fields", {}).get("category"),
            "kind": (
                "event"
                if record["collection"] == "events"
                or record.get("source_key") == "archway-events"
                # Captured records from the first checkpoint used source titles.
                or record.get("source_title") == "Archway Events"
                else record["collection"]
            ),
        }
        for record_id, record in evidence.items()
    }
    # A summary can reuse citations already visible in this answer. Explicit
    # citations keep their own scope; unrelated records cannot replace them.
    citation_scope: dict[int, list[str]] = {}
    earlier_citation_scope: dict[int, list[str]] = {}
    preceding_citations: dict[str, None] = dict.fromkeys(
        record_id for part in (verified_prefix or []) for record_id in part.evidence_ids
    )
    # "Next", "last" and "none left" are claims about the whole timetable a code
    # calculation covered, so a part citing one of its trips is checked against
    # every trip of that complete lookup, not the cited trip alone.
    timetables = [
        lookup["evidence_ids"]
        for lookup in retrievals or []
        if (lookup.get("schedule_calculations") or {}).get("status") == "ok"
    ]
    for index, answer_part in enumerate(answer.parts):
        earlier_citation_scope[index] = list(preceding_citations)
        citation_scope[index] = list(dict.fromkeys(answer_part.evidence_ids))
        if not citation_scope[index] and answer_part.kind != "campus_fact":
            citation_scope[index] = list(preceding_citations)
        for trips in timetables:
            if set(answer_part.evidence_ids) & set(trips):
                citation_scope[index] = list(dict.fromkeys([*citation_scope[index], *trips]))
        preceding_citations.update(dict.fromkeys(answer_part.evidence_ids))
    event_citations = {
        index: [
            record_id
            for record_id in dict.fromkeys([*record_ids, *earlier_citation_scope[index]])
            if subjects.get(record_id, {}).get("kind") == "event"
        ]
        for index, record_ids in citation_scope.items()
    }
    aliases = reference_aliases(list(evidence))
    response = client.create(
        category="review",
        model=model,
        instructions=REVIEW_INSTRUCTIONS,
        input=json.dumps(
            {
                "conversation": [message.model_dump() for message in messages],
                "campus_time": now.isoformat(),
                "campus_weekday": now.strftime("%A"),
                "campus_calendar_week": [
                    str(now.date() - timedelta(days=now.weekday())),
                    str(now.date() + timedelta(days=6 - now.weekday())),
                ],
                "candidate": {
                    **answer.model_dump(),
                    "parts": [
                        {
                            **part.model_dump(),
                            "evidence_ids": map_references(part.evidence_ids, aliases),
                        }
                        for part in answer.parts
                    ],
                },
                "verified_prefix": [
                    {
                        **part.model_dump(),
                        "evidence_ids": map_references(part.evidence_ids, aliases),
                    }
                    for part in (verified_prefix or [])
                ],
                "evidence": map_references(compact_records(list(evidence.values())), aliases),
                "evidence_subjects": map_references(subjects, aliases),
                "citation_scope": map_references(citation_scope, aliases),
                "earlier_citation_scope": map_references(earlier_citation_scope, aliases),
                "evidence_scope": {
                    "record_ids": map_references(list(evidence), aliases),
                    "covers": "all records returned in this turn",
                    "does_not_establish": "exhaustive campus or database coverage",
                },
                "retrieval_coverage": map_references(
                    [
                        {
                            key: lookup.get(key)
                            for key in (
                                "tool",
                                "arguments",
                                "status",
                                "result_count",
                                "total_matches",
                                "truncated",
                                "reason",
                                "evidence_ids",
                                "resolution",
                                "components",
                                "entity_facts",
                                "schedule_calculations",
                            )
                            if key not in {
                                "resolution", "components", "entity_facts",
                                "schedule_calculations",
                            }
                            or key in lookup
                        }
                        for lookup in (retrievals or [])
                        if lookup.get("tool") in {
                            "search_campus", "read_campus", "lookup_contact", "lookup_profile",
                            "lookup_entity",
                        }
                    ],
                    aliases,
                ),
                "event_citations": map_references(event_citations, aliases),
            },
            default=str,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        tools=[],
        tool_choice="none",
        text={
            "format": {
                "type": "json_schema",
                "name": "evidence_review",
                "schema": EvidenceReview.model_json_schema(),
                "strict": True,
            }
        },
        reasoning={"effort": RELEASE.review_reasoning},
        max_output_tokens=RELEASE.review_output_tokens,
        store=False,
        timeout=Timeout(timeout, connect=min(2.0, timeout)),
    )
    if response.status != "completed":
        raise InvalidAnswer("Incomplete evidence review", "incomplete_review")
    try:
        review = EvidenceReview.model_validate_json(response.output_text)
    except ValidationError as error:
        raise InvalidAnswer("Invalid evidence review", "invalid_review") from error
    if sorted(part.part_index for part in review.parts) != list(range(len(answer.parts))):
        raise InvalidAnswer(
            "Review did not cover every answer part exactly once", "review_coverage"
        )
    for part in review.parts:
        if part.unverified_premises:
            part.verdict = "unsupported_claim"
            part.reason = ("Missing factual support: " + "; ".join(part.unverified_premises))[:400]
        # Citation membership and source kind come from code, not an ID list
        # echoed by the reviewer. Event facts are about that activity only.
        if event_citations[part.part_index] and part.uses_event_for_entity:
            part.verdict = "wrong_scope"
            part.reason = (
                "Event evidence cannot establish general attributes of a referenced "
                "facility or organization. Use direct evidence for that entity, or "
                "state that the requested attribute could not be verified."
            )
        if part.infers_food_safety:
            part.verdict = "unsupported_claim"
            part.reason = (
                "Published menu and allergen labels do not establish allergy safety or "
                "relative risk, including when a label is blank. Report the labels and "
                "ask dining staff about ingredients and cross-contact without ranking safety."
            )
        for constraint in part.plan_deadlines:
            if constraint.basis == "standing_service_rule":
                continue
            deadline = constraint.latest_usable_at
            if deadline is None or deadline.tzinfo is None:
                raise InvalidAnswer("Dated plan lacks a timezone-aware deadline", "invalid_review")
            if deadline <= now:
                part.verdict = "wrong_context"
                part.reason = (
                    f"This proposed action's latest usable time is {deadline.isoformat()}, "
                    f"which has passed at campus time {now.isoformat()}. Describe it as past "
                    "or offer an option that can still be followed, using published evidence."
                )
    return review
