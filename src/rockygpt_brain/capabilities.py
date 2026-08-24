"""Small capability adapters; objective shuttle selection stays in DATA."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from rockygpt_brain.data_client import DataPort, ShuttleQuery
from rockygpt_brain.evidence import Evidence, EvidenceKind, EvidenceRegistry
from rockygpt_brain.planning import ShuttleIntent
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
