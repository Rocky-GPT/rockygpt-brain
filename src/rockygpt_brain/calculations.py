"""Small arithmetic tool with explicit operands and provenance; never eval code."""

import re
from datetime import date
from decimal import Decimal, localcontext
from typing import Any, Literal

from pydantic import Field, model_validator

from rockygpt_brain.contracts import ChatMessage, StrictModel


class Operand(StrictModel):
    source: Literal["user", "evidence"]
    value: str = Field(pattern=r"^-?\d{1,12}(\.\d{1,8})?$", max_length=22)
    evidence_id: str | None = Field(default=None, max_length=180)
    field: Literal["calories", "credits"] | None = None

    @model_validator(mode="after")
    def provenance(self) -> "Operand":
        if (self.source == "evidence") != (self.evidence_id is not None and self.field is not None):
            raise ValueError("Evidence operands need both evidence_id and numeric field")
        if self.source == "user" and (self.evidence_id is not None or self.field is not None):
            raise ValueError("User operands have no evidence reference")
        return self


class CalculationQuery(StrictModel):
    operation: Literal["sum", "difference", "mean", "minimum", "maximum"]
    operands: list[Operand] = Field(min_length=1, max_length=50)


def calculate(
    query: CalculationQuery,
    evidence: dict[str, dict[str, Any]],
    messages: list[ChatMessage],
    today: date,
) -> dict[str, Any]:
    values = []
    units = set()
    for operand in query.operands:
        value = Decimal(operand.value)
        if operand.source == "user":
            # Only explicit numbers in the latest question; assistant history is not evidence.
            literals = re.findall(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])", messages[-1].content)
            if value not in {Decimal(literal) for literal in literals}:
                raise ValueError("Operand is absent from the user's question")
            units.add("unspecified")
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
        else:
            result = max(values)
        rendered = format(result, "f")
    return {
        "status": "ok",
        "operation": query.operation,
        "result": rendered,
        "unit": next(iter(units)),
        "operands": [operand.model_dump() for operand in query.operands],
        "limitations": [
            "Arithmetic only; operands do not establish a complete set or a campus policy."
        ],
    }
