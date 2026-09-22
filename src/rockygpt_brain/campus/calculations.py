"""Small arithmetic tool with explicit operands and provenance; never eval code."""

import re
from datetime import date, datetime
from decimal import Decimal, localcontext
from typing import Any, Literal

from pydantic import Field, model_validator

from rockygpt_brain.campus.schedules import CAMPUS_ZONE, opening_intervals
from rockygpt_brain.contracts import ChatMessage, StrictModel


class Operand(StrictModel):
    source: Literal["user", "evidence"]
    value: str = Field(pattern=r"^-?\d{1,12}(\.\d{1,8})?$", max_length=22)
    evidence_id: str | None = Field(default=None, max_length=180)
    field: Literal["calories", "credits"] | None = None
    unit: Literal["minutes", "hours", "kcal", "credits"] | None = None
    user_message_index: int | None = Field(
        default=None,
        ge=0,
        le=79,
        description="User value: zero-based full conversation index; null means latest message.",
    )

    @model_validator(mode="after")
    def provenance(self) -> "Operand":
        if (self.source == "evidence") != (self.evidence_id is not None and self.field is not None):
            raise ValueError("Evidence operands need both evidence_id and numeric field")
        if self.source == "user" and (self.evidence_id is not None or self.field is not None):
            raise ValueError("User operands have no evidence reference")
        if self.source == "evidence" and self.unit is not None:
            raise ValueError("Evidence units are derived from the field")
        if self.source == "evidence" and self.user_message_index is not None:
            raise ValueError("Evidence operands cannot reference a user message")
        return self


class TimeOperand(StrictModel):
    source: Literal["user", "evidence"]
    value: str = Field(
        min_length=16, max_length=40, description="ISO datetime with explicit offset"
    )
    evidence_id: str | None = Field(default=None, max_length=180)
    user_message_index: int | None = Field(
        default=None,
        ge=0,
        le=79,
        description="User time: zero-based full conversation index; null means latest message.",
    )
    field: (
        Literal["starts_at", "opening", "closing", "scheduled_departure", "scheduled_arrival"]
        | None
    ) = None
    point: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "For scheduled_departure/arrival, exact origin/stop label from schedule_calculations."
        ),
    )

    @model_validator(mode="after")
    def provenance(self) -> "TimeOperand":
        if self.source == "user":
            if self.evidence_id is not None or self.field is not None or self.point is not None:
                raise ValueError("User times have no evidence reference")
        elif self.evidence_id is None or self.field is None:
            raise ValueError("Evidence times require an evidence ID and field")
        if self.source == "evidence" and self.user_message_index is not None:
            raise ValueError("Evidence times cannot reference a user message")
        if (self.field in {"scheduled_departure", "scheduled_arrival"}) != (self.point is not None):
            raise ValueError("Only scheduled times require a published stop label")
        value = datetime.fromisoformat(self.value)
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Time operands require explicit UTC offsets")
        return self


class CalculationQuery(StrictModel):
    operation: Literal[
        "sum",
        "difference",
        "mean",
        "minimum",
        "maximum",
        "sort",
        "count",
        "duration",
        "compare_times",
    ]
    operands: list[Operand] = Field(default_factory=list, max_length=100)
    times: list[TimeOperand] = Field(default_factory=list, max_length=2)
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def shape(self) -> "CalculationQuery":
        if self.operation in {"duration", "compare_times"}:
            if len(self.times) != 2 or self.operands or self.evidence_ids:
                raise ValueError("Time operations require exactly two ordered time operands")
        elif self.operation == "count":
            if not self.evidence_ids or self.operands or self.times:
                raise ValueError("Count requires a nonempty list of retrieved record IDs")
            if len(set(self.evidence_ids)) != len(self.evidence_ids):
                raise ValueError("Duplicate record IDs cannot inflate a count")
        elif not self.operands or self.times or self.evidence_ids:
            raise ValueError("Arithmetic and sorting require numeric operands only")
        return self


def verified_record(record: dict[str, Any], day: date) -> None:
    if (
        record.get("freshness") not in {"fresh", "static"}
        or record.get("trust_tier") not in {"official_primary", "official_secondary"}
        or record.get("content_truncated")
    ):
        raise ValueError("Calculation requires complete, current published evidence")
    for key in ("valid_from", "valid_until"):
        bound = date.fromisoformat(record[key]) if record.get(key) else None
        if bound and (
            (key == "valid_from" and day < bound) or (key == "valid_until" and day > bound)
        ):
            raise ValueError("Operand is outside its validity window")


def user_content(messages: list[ChatMessage], index: int | None) -> str:
    index = len(messages) - 1 if index is None else index
    if index < 0 or index >= len(messages) or messages[index].role != "user":
        raise ValueError("Calculation reference must identify a user message")
    return messages[index].content


def verified_time(
    operand: TimeOperand,
    evidence: dict[str, dict[str, Any]],
    messages: list[ChatMessage],
    scheduled_times: dict[tuple[str, str, str], set[str]],
) -> datetime:
    value = datetime.fromisoformat(operand.value)
    if operand.source == "user":
        # An explicit offset is required: do not guess dates or disambiguate a
        # campus daylight-saving fold from an unqualified clock in a question.
        if not re.search(
            rf"(?<![\w:]){re.escape(operand.value)}(?![\w:+\-]|\.\d)",
            user_content(messages, operand.user_message_index),
        ):
            raise ValueError("Time is absent from the user's question")
        return value
    record = evidence.get(operand.evidence_id or "", {})
    fields = record.get("fields", {})
    source_day = fields.get("service_date")
    verified_record(
        record,
        date.fromisoformat(source_day) if source_day else value.astimezone(CAMPUS_ZONE).date(),
    )
    coverage = record.get("coverage", {}).get("fields", {})
    if operand.field == "starts_at":
        if coverage.get("starts_at") != "published":
            raise ValueError("Time field is not published")
        candidates = [datetime.fromisoformat(fields["starts_at"])]
    elif operand.field in {"opening", "closing"}:
        if (
            record.get("collection") not in {"campus_hours", "dining_hours"}
            or coverage.get("schedule") != "published"
            or not source_day
        ):
            raise ValueError("No applicable published opening intervals")
        intervals = opening_intervals(
            fields.get("hours", fields["schedule"]), date.fromisoformat(source_day)
        )
        candidates = [
            min(start for start, _ in intervals)
            if operand.field == "opening"
            else max(end for _, end in intervals)
        ]
    else:
        # These values are produced only by a complete validated timetable
        # calculation in this turn, never supplied or certified by the model.
        candidates = [
            datetime.fromisoformat(item)
            for item in scheduled_times.get(
                (operand.evidence_id or "", operand.field or "", operand.point or ""), set()
            )
        ]
    if not any(
        item.tzinfo is not None and item.timestamp() == value.timestamp() for item in candidates
    ):
        raise ValueError("Time does not match its published field or verified calculation")
    return value


def calculate(
    query: CalculationQuery,
    evidence: dict[str, dict[str, Any]],
    messages: list[ChatMessage],
    today: date,
    scheduled_times: dict[tuple[str, str, str], set[str]] | None = None,
) -> dict[str, Any]:
    if query.operation == "count":
        for evidence_id in query.evidence_ids:
            verified_record(evidence.get(evidence_id, {}), today)
        return {
            "status": "ok",
            "operation": "count",
            "result": str(len(query.evidence_ids)),
            "unit": "records",
            "evidence_ids": query.evidence_ids,
            "limitations": [
                "Only these distinct retrieved record IDs were counted. This is not a count "
                "of all campus entities or proof that the selected set is complete."
            ],
        }
    if query.operation in {"duration", "compare_times"}:
        start, end = [
            verified_time(item, evidence, messages, scheduled_times or {}) for item in query.times
        ]
        seconds = Decimal(str(end.timestamp())) - Decimal(str(start.timestamp()))
        time_result = (
            format(seconds / Decimal(60), "f")
            if query.operation == "duration"
            else "before"
            if seconds > 0
            else "after"
            if seconds < 0
            else "same_instant"
        )
        return {
            "status": "ok",
            "operation": query.operation,
            "result": time_result,
            "unit": "elapsed_minutes" if query.operation == "duration" else "time_order",
            "operands": [item.model_dump(mode="json") for item in query.times],
            "limitations": [
                "Ordered inputs: duration is second minus first; comparison describes the first "
                "relative to the second. Published times do not confirm live service, holiday "
                "exceptions, or sufficient eating/walking time."
            ],
        }
    values = []
    units = set()
    for operand in query.operands:
        value = Decimal(operand.value)
        if operand.source == "user":
            # Explicit user provenance supports follow-ups without trusting assistant numbers.
            content = user_content(messages, operand.user_message_index)
            literals = re.findall(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])", content)
            if value not in {Decimal(literal) for literal in literals}:
                raise ValueError("Operand is absent from the user's question")
            if operand.unit is not None:
                labels = {
                    "minutes": r"minutes?|mins?",
                    "hours": r"hours?|hrs?",
                    "kcal": r"kcal|calories",
                    "credits": r"credits?",
                }
                measurements = re.findall(
                    rf"(?<![\w.])(-?\d+(?:\.\d+)?)\s*(?:{labels[operand.unit]})\b",
                    content,
                    re.IGNORECASE,
                )
                if value not in {Decimal(number) for number in measurements}:
                    raise ValueError("The user's value does not establish the requested unit")
            units.add(operand.unit or "unspecified")
        else:
            record = evidence.get(operand.evidence_id or "", {})
            if (
                record.get("freshness") not in {"fresh", "static"}
                or record.get("trust_tier") not in {"official_primary", "official_secondary"}
                or record.get("content_truncated")
                or record.get("coverage", {}).get("fields", {}).get(operand.field) != "published"
            ):
                raise ValueError("Operand requires complete, current published evidence")
            for key in ("valid_from", "valid_until"):
                applicable_date = date.fromisoformat(record[key]) if record.get(key) else None
                if applicable_date and (
                    (key == "valid_from" and applicable_date > today)
                    or (key == "valid_until" and applicable_date < today)
                ):
                    raise ValueError("Operand is outside its validity window")
            original = record.get("fields", {}).get(operand.field)
            if isinstance(original, bool) or not re.fullmatch(
                r"-?\d{1,12}(\.\d{1,8})?", str(original)
            ):
                raise ValueError("Evidence field is not an exact scalar number")
            if Decimal(str(original)) != value:
                raise ValueError("Operand does not match its evidence field")
            units.add("kcal" if operand.field == "calories" else "credits")
        values.append(value)
    if len(units) != 1:
        raise ValueError("Mixed or unverified units cannot be combined")
    if query.operation == "difference" and len(values) != 2:
        raise ValueError("Difference requires two ordered operands")
    with localcontext() as context:
        context.prec = 40
        if query.operation == "sum":
            result = sum(values, Decimal(0))
        elif query.operation == "difference":
            result = values[0] - values[1]
        elif query.operation == "mean":
            result = sum(values, Decimal(0)) / len(values)
        elif query.operation == "minimum":
            result = min(values)
        elif query.operation == "maximum":
            result = max(values)
        rendered: str | list[str] = (
            [format(value, "f") for value in sorted(values)]
            if query.operation == "sort"
            else format(result, "f")
        )
    return {
        "status": "ok",
        "operation": query.operation,
        "result": rendered,
        "unit": next(iter(units)),
        "operands": [operand.model_dump() for operand in query.operands],
        **(
            {"ordered_operand_indices": sorted(range(len(values)), key=lambda i: values[i])}
            if query.operation == "sort"
            else {}
        ),
        "limitations": [
            "Arithmetic only; operands do not establish a complete set or a campus policy. "
            "Unspecified units must stay unspecified; no conversions are inferred."
        ],
    }
