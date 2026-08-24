from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from rockygpt_brain.config import Settings
from rockygpt_brain.data_client import (
    Completeness,
    DataDataset,
    DataEvidence,
    DataOrdering,
    ShuttleAppliedFilters,
    ShuttleQuery,
    ShuttleRecord,
    ShuttleResponse,
    ShuttleStop,
)
from rockygpt_brain.model import CommunicateInput, UnderstandInput
from rockygpt_brain.planning import (
    AnswerDraft,
    CapabilityOperation,
    ClaimKind,
    DraftClaim,
    RouteMode,
    RoutePlan,
    ShuttleIntent,
    ShuttleSelection,
    ShuttleTimeScope,
)


class FakeData:
    def __init__(self, responses: list[ShuttleResponse] | None = None) -> None:
        self.responses = responses or [make_shuttle_response()]
        self.queries: list[ShuttleQuery] = []
        self.ready = True

    async def query_shuttle(self, query: ShuttleQuery) -> ShuttleResponse:
        self.queries.append(query)
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]

    async def readiness(self) -> bool:
        return self.ready


class ScriptedModel:
    configured = True
    model_id = "fake-structured-model"

    def __init__(self) -> None:
        self.understand_calls: list[UnderstandInput] = []
        self.communicate_calls: list[CommunicateInput] = []
        self.plan_queue: list[RoutePlan | Exception] = []
        self.draft_queue: list[AnswerDraft | Exception] = []

    async def understand(
        self, request: UnderstandInput, *, repair_error: str | None = None
    ) -> RoutePlan:
        self.understand_calls.append(request)
        if self.plan_queue:
            value = self.plan_queue.pop(0)
            if isinstance(value, Exception):
                raise value
            return value
        lowered = request.message.casefold()
        if "what time did you tell me" in lowered or "what source supported" in lowered:
            return RoutePlan(mode=RouteMode.CONVERSATION)
        if "what about tomorrow" in lowered:
            tomorrow = request.time.request_date + timedelta(days=1)
            return shuttle_plan(
                destination="GSP",
                selection=ShuttleSelection.NEXT,
                scope=ShuttleTimeScope.REMAINING,
                service_date=tomorrow,
            )
        if "shuttle" in lowered:
            selection = ShuttleSelection.FIRST if "first" in lowered else ShuttleSelection.NEXT
            scope = (
                ShuttleTimeScope.FULL_DAY
                if selection == ShuttleSelection.FIRST
                else ShuttleTimeScope.REMAINING
            )
            route = "Route A" if "route a" in lowered else None
            destination = "GSP" if "gsp" in lowered else None
            return shuttle_plan(
                route=route,
                destination=destination,
                selection=selection,
                scope=scope,
            )
        return RoutePlan(mode=RouteMode.GENERAL)

    async def communicate(
        self, request: CommunicateInput, *, correction_error: str | None = None
    ) -> AnswerDraft:
        self.communicate_calls.append(request)
        if self.draft_queue:
            value = self.draft_queue.pop(0)
            if isinstance(value, Exception):
                raise value
            return value
        if request.plan.mode in {RouteMode.CAPABILITY, RouteMode.COMPOSITE}:
            result = request.typed_results[0]["result"]
            evidence_ids = list(result["evidenceIds"])
            if result["outcome"] in {"empty", "no_match"}:
                answer = "No matching shuttle remains in the requested service period."
                claim = answer
            else:
                record = result["records"][0]
                answer = (
                    f"{record['route']} leaves {record['departure']['location']} at "
                    f"{record['departure']['time']} and reaches "
                    f"{record['matchedDestination']['location']} at "
                    f"{record['matchedDestination']['time']}."
                )
                claim = answer
            return AnswerDraft(
                answer=answer,
                route="standard",
                claims=[DraftClaim(text=claim, kind=ClaimKind.CAMPUS, evidenceIds=evidence_ids)],
                citationEvidenceIds=evidence_ids[:1],
                uiActions=[],
                suggestedQuestions=[],
            )
        if request.plan.mode == RouteMode.CONVERSATION:
            claim_evidence = next(
                item["evidenceId"]
                for item in request.evidence
                if item["kind"] == "conversation"
            )
            prior = next(
                item["payload"]["text"]
                for item in request.evidence
                if item["evidenceId"] == claim_evidence
            )
            if "what source supported" in request.message.casefold():
                historical = next(
                    item for item in request.evidence if item["kind"] == "historical_data"
                )
                answer = f"The source I used then was {historical['title']}."
                return AnswerDraft(
                    answer=answer,
                    route="standard",
                    claims=[
                        DraftClaim(
                            text=answer,
                            kind=ClaimKind.CONVERSATION,
                            evidenceIds=[claim_evidence, historical["evidenceId"]],
                        )
                    ],
                    citationEvidenceIds=[historical["evidenceId"]],
                    uiActions=[],
                    suggestedQuestions=[],
                )
            answer = f"Earlier, I told you: {prior}"
            return AnswerDraft(
                answer=answer,
                route="standard",
                claims=[
                    DraftClaim(
                        text=answer,
                        kind=ClaimKind.CONVERSATION,
                        evidenceIds=[claim_evidence],
                    )
                ],
                citationEvidenceIds=[],
                uiActions=[],
                suggestedQuestions=[],
            )
        return AnswerDraft(
            answer="That’s subjective, but student government can still be worth engaging with.",
            route="standard",
            claims=[],
            citationEvidenceIds=[],
            uiActions=[],
            suggestedQuestions=[],
        )


def shuttle_plan(
    *,
    route: str | None = None,
    destination: str | None = None,
    selection: ShuttleSelection = ShuttleSelection.NEXT,
    scope: ShuttleTimeScope = ShuttleTimeScope.REMAINING,
    service_date: date | None = None,
) -> RoutePlan:
    return RoutePlan(
        mode=RouteMode.CAPABILITY,
        operations=[
            CapabilityOperation(
                name="shuttle",
                arguments=ShuttleIntent(
                    route=route,
                    destination=destination,
                    serviceDate=service_date,
                    selection=selection,
                    timeScope=scope,
                ),
            )
        ],
    )


def make_shuttle_response(
    *,
    outcome: str = "success",
    records: bool = True,
    evidence: bool = True,
    route: str = "Route A",
    service_date: date = date(2026, 8, 24),
) -> ShuttleResponse:
    day = "weekday" if service_date.weekday() < 5 else "saturday" if service_date.weekday() == 5 else "sunday"
    evidence_items = (
        [
            DataEvidence(
                evidenceId="shuttle-source-1",
                sourceId="transportation",
                title="Official Shuttle Schedule",
                url="https://www.ramapo.edu/shuttle/",
                collectedAt="2026-08-24T12:00:00Z",
            )
        ]
        if evidence
        else []
    )
    record_items = (
        [
            ShuttleRecord(
                route=route,
                serviceDate=service_date,
                serviceDay=day,
                departure=ShuttleStop(location="Campus", time="9:00 AM"),
                stops=[ShuttleStop(location="GSP", time="9:20 AM")],
                arrival=ShuttleStop(location="Campus", time="9:40 AM"),
                matchedOrigin=ShuttleStop(location="Campus", time="9:00 AM"),
                matchedDestination=ShuttleStop(location="GSP", time="9:20 AM"),
                evidenceIds=["shuttle-source-1"],
            )
        ]
        if records
        else []
    )
    return ShuttleResponse(
        outcome=outcome,
        records=record_items,
        completeness=Completeness(
            state="complete",
            returned=len(record_items),
            matched=len(record_items),
            limit=8,
            truncated=False,
        ),
        appliedFilters=ShuttleAppliedFilters(
            serviceDate=service_date,
            serviceDay=day,
            asOf="2026-08-24T13:00:00Z",
            selection="next",
            timeScope="remaining",
            serviceDatesConsidered=[service_date],
        ),
        ordering=[DataOrdering(field="matchedDestination.time", direction="asc")],
        dataset=DataDataset(
            id="transportation",
            version="release-2026-08-24",
            activatedAt="2026-08-24T12:00:00Z",
        ),
        evidence=evidence_items,
    )


@pytest.fixture
def settings() -> Settings:
    return Settings(
        app_env="test",
        openai_api_key="test-openai-key",
        chat_log_hash_key="c" * 32,
        abuse_hash_key="a" * 32,
        admin_api_token="admin-token-value",
        staging_service_token=None,
        chat_rate_limit=100,
        feedback_rate_limit=100,
    )
