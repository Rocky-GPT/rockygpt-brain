"""Generic structured-data execution for the CODE lane."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from rockygpt_brain.core.model import Intent, QueryOperation
from rockygpt_brain.services.data_client import DataPort


class CodeExecutor:
    """Fetch authoritative records, then apply the requested data operations."""

    def __init__(self, data: DataPort) -> None:
        self._data = data

    async def execute(self, intent: Intent, now: datetime) -> dict[str, Any]:
        payload = await self._data.code(intent, now)
        records = payload.get("records")
        if not isinstance(records, list):
            return payload

        selected = [record for record in records if isinstance(record, dict)]
        operation = intent.operation
        if operation:
            selected = self._apply(selected, operation)

        result: dict[str, Any] = {
            "outcome": payload.get("outcome", "success" if selected else "empty"),
            "records": selected,
        }
        for key in ("dataset", "evidence", "warnings"):
            if key in payload:
                result[key] = payload[key]

        completeness = payload.get("completeness")
        if isinstance(completeness, dict):
            result["completeness"] = {**completeness, "returned": len(selected)}

        if operation:
            result["operation"] = operation.model_dump(
                mode="json",
                by_alias=True,
                exclude_none=True,
            )
        result["evidence"] = self._selected_evidence(result.get("evidence"), selected)
        return result

    @classmethod
    def _apply(
        cls,
        records: list[dict[str, Any]],
        operation: QueryOperation,
    ) -> list[dict[str, Any]]:
        selected = records
        if operation.order_by:
            present: list[dict[str, Any]] = []
            missing: list[dict[str, Any]] = []
            for record in selected:
                target = present if cls._field(record, operation.order_by) is not None else missing
                target.append(record)
            present.sort(
                key=lambda record: cls._sort_value(cls._field(record, operation.order_by or "")),
                reverse=operation.direction == "descending",
            )
            selected = present + missing
        if operation.limit is not None:
            selected = selected[: operation.limit]
        return selected

    @staticmethod
    def _field(record: dict[str, Any], path: str) -> Any:
        value: Any = record
        for part in path.split("."):
            if not isinstance(value, dict):
                return None
            value = value.get(part)
        return value

    @staticmethod
    def _sort_value(value: Any) -> tuple[int, float | str]:
        if isinstance(value, (int, float)):
            return (0, float(value))
        if isinstance(value, str):
            text = value.strip()
            for pattern in ("%I:%M %p", "%H:%M"):
                try:
                    parsed = datetime.strptime(text, pattern)
                    return (0, float(parsed.hour * 3600 + parsed.minute * 60))
                except ValueError:
                    pass
            try:
                parsed_date = datetime.fromisoformat(text.replace("Z", "+00:00"))
                return (0, parsed_date.timestamp())
            except ValueError:
                return (1, text.casefold())
        return (1, str(value).casefold())

    @staticmethod
    def _selected_evidence(
        evidence: Any,
        records: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not isinstance(evidence, list):
            return []
        evidence_ids = {
            evidence_id
            for record in records
            for evidence_id in record.get("evidenceIds", [])
            if isinstance(evidence_id, str)
        }
        items = [item for item in evidence if isinstance(item, dict)]
        if not evidence_ids:
            return items
        return [item for item in items if item.get("evidenceId") in evidence_ids]
