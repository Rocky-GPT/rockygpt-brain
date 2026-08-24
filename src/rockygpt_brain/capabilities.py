"""Small capability adapters; objective shuttle selection stays in DATA."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from rockygpt_brain.data_client import DataPort, ShuttleQuery
from rockygpt_brain.evidence import Evidence, EvidenceKind, EvidenceRegistry
from rockygpt_brain.errors import GroundingError
from rockygpt_brain.planning import AnswerDraft, ClaimKind, ShuttleIntent
from rockygpt_brain.time_context import TimeContext


def service_day_for(value: date) -> str:
    weekday = value.weekday()
    return "saturday" if weekday == 5 else "sunday" if weekday == 6 else "weekday"


@dataclass(frozen=True, slots=True)
class CapabilityResult:
    name: str
    outcome: str
    records: list[dict[str, Any]]
    completeness: dict[str, Any]
    evidence_ids: list[str]
    applied_filters: dict[str, Any]
    ordering: list[dict[str, str]]
    dataset_id: str
    dataset_version: str
    warnings: list[str]

    @property
    def grounded(self) -> bool:
        return bool(self.evidence_ids)

    def prompt_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "outcome": self.outcome,
            "records": self.records,
            "completeness": self.completeness,
            "evidenceIds": self.evidence_ids,
            "appliedFilters": self.applied_filters,
            "ordering": self.ordering,
            "dataset": {"id": self.dataset_id, "version": self.dataset_version},
            "warnings": self.warnings,
        }


class ShuttleCapability:
    name = "shuttle"

    def __init__(self, data: DataPort) -> None:
        self._data = data

    async def execute(
        self,
        intent: ShuttleIntent,
        time: TimeContext,
        registry: EvidenceRegistry,
    ) -> CapabilityResult:
        service_date = intent.service_date or time.service_date
        derived_day = service_day_for(service_date)
        if intent.service_day is not None and intent.service_day != derived_day:
            # The calendar date is authoritative; this prevents contradictory DATA arguments.
            raise ValueError("serviceDate and serviceDay disagree")
        query = ShuttleQuery(
            route=intent.route,
            origin=intent.origin,
            destination=intent.destination,
            serviceDate=service_date,
            serviceDay=derived_day,
            asOf=time.instant,
            selection=intent.selection,
            timeScope=intent.time_scope,
            limit=intent.limit,
        )
        response = await self._data.query_shuttle(query)
        for item in response.evidence:
            registry.register(
                Evidence(
                    evidenceId=item.evidence_id,
                    kind=EvidenceKind.DATA,
                    sourceId=item.source_id,
                    title=item.title,
                    url=item.url,
                    collectedAt=item.collected_at,
                    datasetId=response.dataset.id,
                    datasetVersion=response.dataset.version,
                )
            )
        return CapabilityResult(
            name=self.name,
            outcome=response.outcome,
            records=[record.model_dump(mode="json", by_alias=True) for record in response.records],
            completeness=response.completeness.model_dump(mode="json", by_alias=True),
            evidence_ids=[item.evidence_id for item in response.evidence],
            applied_filters=response.applied_filters.model_dump(mode="json", by_alias=True),
            ordering=[item.model_dump(mode="json") for item in response.ordering],
            dataset_id=response.dataset.id,
            dataset_version=response.dataset.version,
            warnings=response.warnings,
        )


@dataclass(frozen=True, slots=True)
class ShuttleCommunication:
    answer: str
    evidence_ids: tuple[str, ...]


def shuttle_communication(result: CapabilityResult) -> ShuttleCommunication:
    """Project DATA facts to one exact sentence AI #2 may render but may not alter."""
    if result.outcome == "success":
        lines: list[str] = []
        evidence_ids: list[str] = []
        for record in result.records:
            departure = record["departure"]
            destination = record["matchedDestination"]
            lines.append(
                f"{record['route']} leaves {departure['location']} at {departure['time']} "
                f"and reaches {destination['location']} at {destination['time']}."
            )
            evidence_ids.extend(str(item) for item in record["evidenceIds"])
        answer = "\n".join(lines)
    else:
        reason = result.completeness.get("reason")
        if result.outcome == "no_match" or reason == "entity_no_match":
            answer = "I couldn’t find a shuttle matching the requested route or stop."
        elif reason == "not_current":
            answer = "No matching shuttle is currently active."
        elif reason == "dataset_empty":
            answer = "The published shuttle schedule has no trips for that service period."
        else:
            answer = "No matching shuttle remains in the requested service period."
        evidence_ids = list(result.evidence_ids)
    if result.completeness.get("state") != "complete" or result.completeness.get("truncated"):
        answer += "\n\nThis is a partial result; additional matching trips may exist."
    return ShuttleCommunication(answer=answer, evidence_ids=tuple(dict.fromkeys(evidence_ids)))


def validate_shuttle_communication(
    draft: AnswerDraft,
    result: CapabilityResult,
    registry: EvidenceRegistry,
) -> None:
    required = shuttle_communication(result)
    reasons: list[str] = []
    if draft.answer != required.answer:
        reasons.append("shuttle answer must exactly render the CODE projection")
    campus_claims = [claim for claim in draft.claims if claim.kind == ClaimKind.CAMPUS]
    if len(campus_claims) != 1 or campus_claims[0].text != draft.answer:
        reasons.append("shuttle answer must be covered by one exact campus claim")
    elif set(campus_claims[0].evidence_ids) != set(required.evidence_ids):
        reasons.append("shuttle claim evidence must exactly match the CODE projection")
    public_citations = [
        evidence_id
        for evidence_id in draft.citation_evidence_ids
        if (item := registry.get(evidence_id)) is not None and item.url is not None
    ]
    if not public_citations:
        reasons.append("shuttle answers require at least one public citation")
    if not set(draft.citation_evidence_ids).issubset(set(required.evidence_ids)):
        reasons.append("shuttle citation is unrelated to the projected facts")
    if reasons:
        raise GroundingError(reasons)
