"""Local browser harness: real HTTP/engine/SQL/ledger, explicitly injected provider.

Not a deployment mode. Requires localhost/brain_accounting_test and never imports
an OpenAI client. Select a fixture using /tmp/rockygpt-phase2-scenario.json.
"""

import importlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import uvicorn
from httpx import Request
from openai import APITimeoutError
from phase2_snapshot import local_database, snapshot
from psycopg.conninfo import make_conninfo

from rockygpt_brain.config import RELEASE, Deployment
from rockygpt_brain.core.provider import ModelResponse, OutputItem, PaidGateway, Usage
from rockygpt_brain.governance.accounting import PostgresLedger
from rockygpt_brain.retrieval.data import CampusData

api = importlib.import_module("rockygpt_brain.api.app")
url = local_database()
now = datetime.fromisoformat(snapshot()["captured_at"])
ledger = PostgresLedger(make_conninfo(url, user="brain_test_development"), "development")


def scenario() -> dict[str, Any]:
    # Public, non-secret fixture; it can only drive this explicit local test process.
    path = Path("/tmp/rockygpt-phase2-scenario.json")  # noqa: S108
    return json.loads(path.read_text())  # type: ignore[no-any-return]


class FixtureProvider:
    def create(self, **kwargs: Any) -> ModelResponse:
        case = scenario()
        if case["mode"] == "timeout":
            raise APITimeoutError(request=Request("POST", "https://api.openai.com"))
        return ModelResponse(
            "injected-" + str(uuid4()),
            RELEASE.model,
            "completed",
            "",
            [
                OutputItem(
                    {
                        "type": "function_call",
                        "name": "lookup_contact",
                        "call_id": "fixture-contact",
                        "arguments": json.dumps(
                            {
                                "entity": case.get("entity", "Registrar"),
                                "fields": case.get(
                                    "fields", ["phone", "email", "office", "department"]
                                ),
                            }
                        ),
                    }
                )
            ],
            Usage(250, 0, 40, 0),
        )


class FixtureData(CampusData):
    def __init__(self, database_url: str, requested_now: datetime) -> None:
        super().__init__(url, now)

    def lookup_contact(self, query: Any) -> dict[str, Any]:
        if scenario()["mode"] == "database":
            raise RuntimeError("Injected database failure")
        return super().lookup_contact(query)


@contextmanager
def gateway(deployment: Deployment, request_id: str) -> Iterator[PaidGateway]:
    yield PaidGateway(FixtureProvider(), ledger, request_id, clock=lambda: now)


api.load_deployment = lambda: Deployment(
    environment="development", api_key="injected", project="injected", ledger_url=ledger.url
)
api.open_gateway = gateway
api.CampusData = FixtureData

if __name__ == "__main__":
    print("PHASE 2 BROWSER HARNESS: injected provider usage; no paid calls", flush=True)
    uvicorn.run(api.app, host="127.0.0.1", port=8000)
