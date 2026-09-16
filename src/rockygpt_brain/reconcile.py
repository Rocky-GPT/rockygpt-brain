"""Settle uncertain operations from an operator-verified provider usage receipt. No API calls."""

import argparse
import json
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from rockygpt_brain.accounting import CAMPUS_ZONE, PaidCallError, PostgresLedger
from rockygpt_brain.config import ConfigurationError, Environment, Price
from rockygpt_brain.provider import Usage


class Receipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation_id: str
    provider_response_id: str = Field(min_length=1)
    returned_model: str = Field(min_length=1)
    evidence_reference: str = Field(min_length=1)
    input_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    reasoning_tokens: int = Field(ge=0)


def reconcile(ledger: PostgresLedger, receipt: Receipt, now: datetime) -> None:
    identity = str(UUID(receipt.operation_id))
    with ledger.transaction() as conn:
        operation = conn.execute(
            "SELECT * FROM brain_ops.operations WHERE environment = %s AND operation_id = %s",
            (ledger.environment, identity),
        ).fetchone()
    if operation is None:
        raise PaidCallError("operation_not_found")
    # Use the original reservation's rates, even after a release or price expiry.
    price = Price.model_validate(operation["metadata"]["price"])
    usage = Usage(
        receipt.input_tokens,
        receipt.cached_input_tokens,
        receipt.output_tokens,
        receipt.reasoning_tokens,
    )
    ledger.settle(
        identity,
        usage.cost(price),
        asdict(usage),
        receipt.provider_response_id,
        receipt.returned_model,
        operation["elapsed_ms"] or 0,
        now,
        reconciliation_reference=receipt.evidence_reference,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", type=Path)
    args = parser.parse_args()
    environment = os.environ.get("BRAIN_ENVIRONMENT")
    if environment not in {"development", "production"}:
        raise ConfigurationError("Set BRAIN_ENVIRONMENT")
    receipt = Receipt.model_validate_json(args.receipt.read_text())
    ledger = PostgresLedger(os.environ["BRAIN_LEDGER_DATABASE_URL"], cast(Environment, environment))
    reconcile(ledger, receipt, datetime.now(CAMPUS_ZONE))
    print(json.dumps({"operationId": receipt.operation_id, "state": "settled"}))


if __name__ == "__main__":
    main()
