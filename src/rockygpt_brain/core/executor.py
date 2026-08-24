"""The Worker. Contract sections 5 through 7.

Compiles one task, runs it, and seals a single discriminated outcome. Everything
the Writer would otherwise have to decide — which record, what an empty result
means, whether the evidence supports anything — is decided here or not at all.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from rockygpt_brain.core.capabilities import CAPABILITIES, can_report_success
from rockygpt_brain.core.compilation import CompiledPlan, compile_task
from rockygpt_brain.core.interpretation import (
    MEASUREMENT_RELATIONS,
    Cardinality,
    Domain,
    Task,
    WorldTask,
)
from rockygpt_brain.core.outcomes import (
    AbsenceCause,
    Absent,
    CardinalityViolation,
    Error,
    General,
    Outcome,
    Success,
    Unavailable,
    seal,
)
from rockygpt_brain.core.selection import is_complete, select_extremal
from rockygpt_brain.services.data_client import DataPort


class Executor:
    def __init__(self, data: DataPort) -> None:
        self._data = data

    async def run(self, task: Task, now: datetime, tz: ZoneInfo) -> Outcome:
        domain = Domain(task.domain)

        if isinstance(task, WorldTask):
            return General(question=task.question, current_time=now.isoformat())

        if domain in (Domain.CONVERSATION, Domain.UNKNOWN):
            # Conversation truth is contract section 8 and is not built yet.
            # Reporting that plainly is better than handing the Writer a
            # transcript to search, which is the same defect one layer up.
            return Absent(cause=AbsenceCause.NO_CAPABILITY)

        capability = CAPABILITIES.get(domain)
        if capability is None:
            return Absent(cause=AbsenceCause.NO_CAPABILITY)

        if not can_report_success(capability):
            # Contract 6.4: without a calibrated floor this domain cannot tell a
            # supporting document from a returned one, so it does not fetch and
            # does not carry evidence the Writer would be forbidden to use.
            return Absent(cause=capability.default_absence)

        plan = compile_task(task, now, tz)
        if not isinstance(plan, CompiledPlan):
            return plan

        try:
            payload = await self._data.execute(plan)
        except Exception:
            return Unavailable(dependency="data")

        return self._read(plan, payload)

    def _read(self, plan: CompiledPlan, payload: dict[str, Any]) -> Outcome:
        reported = payload.get("outcome")
        if reported == "unavailable":
            return Unavailable(dependency="data")
        if reported == "error":
            return Error(code="data_error")

        records = [r for r in payload.get("records", []) if isinstance(r, dict)]
        completeness = payload.get("completeness")
        resolved = dict(plan.resolved)

        if plan.relation in MEASUREMENT_RELATIONS:
            return self._measure(plan, records, completeness, payload, resolved)

        if not records:
            return Absent(cause=self._absence(plan, completeness), resolved=resolved)

        if plan.post_select is not None:
            if not is_complete(completeness):
                # An extremum over a truncated set is a guess. Contract 6.3.
                return Absent(cause=AbsenceCause.INCOMPLETE_SOURCE, resolved=resolved)
            records = select_extremal(
                records, plan.post_select.ordering, plan.post_select.direction
            )
            if not records:
                return Absent(cause=AbsenceCause.NO_QUALIFYING_RECORDS, resolved=resolved)
            resolved["orderedBy"] = plan.post_select.ordering.field
            resolved["direction"] = plan.post_select.direction

        if plan.cardinality is Cardinality.ONE and len(records) > 1:
            # Transport promised one and returned several. Truncating here would
            # hide a contract breach behind a plausible answer.
            return Error(code="cardinality_violation")

        try:
            return seal(
                Success(
                    relation=plan.relation,
                    cardinality=plan.cardinality,
                    records=records,
                    evidence=self._evidence(payload.get("evidence"), records),
                    resolved=resolved,
                )
            )
        except CardinalityViolation:
            return Error(code="cardinality_violation")

    def _measure(
        self,
        plan: CompiledPlan,
        records: list[dict[str, Any]],
        completeness: Any,
        payload: dict[str, Any],
        resolved: dict[str, Any],
    ) -> Outcome:
        """A measurement of zero is a fact; an unresolved name is not.

        Contract 7.1 makes a measured zero a success. That only holds where the
        capability can actually measure: if a mention was passed to a role whose
        resolution failure is unreported, an empty result may mean the name was
        never recognised, and reporting zero would assert something the data did
        not say.
        """

        if not records and self._mention_unresolvable(plan):
            return Absent(cause=AbsenceCause.ENTITY_UNKNOWN, resolved=resolved)
        matched = completeness.get("matched") if isinstance(completeness, dict) else None
        value = matched if isinstance(matched, int) else len(records)
        return seal(
            Success(
                relation=plan.relation,
                cardinality=Cardinality.ONE,
                records=[],
                value=value,
                evidence=self._evidence(payload.get("evidence"), []),
                resolved=resolved,
            )
        )

    @staticmethod
    def _mention_unresolvable(plan: CompiledPlan) -> bool:
        supplied = set(plan.params) | set(plan.body)
        return any(
            role.resolution == "unreported" and role.parameter in supplied
            for role in plan.capability.entity_roles.values()
        )

    def _absence(self, plan: CompiledPlan, completeness: Any) -> AbsenceCause:
        """Map the source's own reason, then fall back conservatively."""

        reason = completeness.get("reason") if isinstance(completeness, dict) else None
        if isinstance(reason, str):
            mapped = plan.capability.absence_map.get(reason)
            if mapped is not None:
                return mapped
        if self._mention_unresolvable(plan):
            # The data never said the subject does not exist, so neither do we.
            return AbsenceCause.ENTITY_UNKNOWN
        return plan.capability.default_absence

    @staticmethod
    def _evidence(evidence: Any, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Evidence for what was selected, not for what was fetched."""

        if not isinstance(evidence, list):
            return []
        items = [item for item in evidence if isinstance(item, dict)]
        wanted = {
            evidence_id
            for record in records
            for evidence_id in record.get("evidenceIds", [])
            if isinstance(evidence_id, str)
        }
        if not wanted:
            return items
        return [item for item in items if item.get("evidenceId") in wanted]
