"""Only synthetic transports and facts for the offline HTTP regression suite.

The real gateway validates scripted Responses output and charges a fake ledger.
No real OpenAI transport, database connection, key, or .env is used here.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from rockygpt_brain.api.app import create_app
from rockygpt_brain.engine import SYSTEM_PROMPT, ChatEngine
from rockygpt_brain.provider import Gateway
from rockygpt_brain.retrieval import MemoryEntityFacts
from rockygpt_brain.settings import Prices, ProviderSettings
from rockygpt_brain.spending import Reservation, SpendingError

PUBLIC_CONFIG: dict[str, object] = {
    "environment": "development", "model": "fixture-model",
    "input_nusd": 1, "output_nusd": 1, "cached_input_nusd": 1,
    "price_valid_until": "2099-01-01T00:00:00+00:00",
    "max_input_bytes": 100_000, "max_output_tokens": 1_200,
    "max_turn_nusd": 25_000_000,
    "clock": "2026-10-01T12:00:00+00:00",
}
NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)


def prompt_text() -> str:
    return SYSTEM_PROMPT


class FixtureLedger:
    def __init__(self, error: str | None = None) -> None:
        self.error = error
        self.reservations = 0

    async def ready(self) -> bool:
        return self.error != "ledger_unavailable"

    async def reserve(self, request_id: str, amount_nusd: int, metadata: dict[str, Any],
                      now: datetime) -> Reservation:
        if self.error:
            raise SpendingError(self.error)
        self.reservations += 1
        return Reservation(f"fixture-{self.reservations}", amount_nusd)

    async def settle(self, reservation: Reservation, cost_nusd: int, usage: dict[str, int],
                     response_id: str, returned_model: str, now: datetime) -> None:
        pass

    async def uncertain(self, reservation: Reservation, code: str) -> None:
        pass

    async def release(self, reservation: Reservation, code: str, now: datetime) -> None:
        pass

    async def pause(self) -> None:
        pass


class ScriptedTransport:
    def __init__(self, steps: list[dict[str, Any]]) -> None:
        self.steps = iter(steps)
        self.count = 0

    async def send(self, request: dict[str, Any], timeout: float) -> dict[str, Any]:  # noqa: ASYNC109
        self.count += 1
        step = next(self.steps)
        if step.get("error") == "timeout":
            raise TimeoutError("synthetic provider outage")
        return {
            "id": f"fixture-response-{self.count}", "model": step.get("model", "fixture-model"),
            "status": "completed",
            "usage": {"input_tokens": 10, "output_tokens": 10,
                      "input_tokens_details": {"cached_tokens": 0}},
            "output": [{"type": "function_call", "name": step["tool"],
                        "call_id": f"fixture-call-{self.count}",
                        "arguments": json.dumps(step["arguments"])}],
        }

    async def close(self) -> None:
        pass


def make_client(conversation: dict[str, Any], fixtures: dict[str, Any]) -> TestClient:
    settings = ProviderSettings(
        environment="development", api_key="offline-fixture", project="offline-fixture",
        ledger_url="unused",
        prices=Prices("fixture-model", 1, 1, 1, datetime(2099, 1, 1, tzinfo=UTC)),
    )
    gateway = Gateway(settings, ledger=FixtureLedger(conversation.get("ledger_error")),
                      transport=ScriptedTransport(conversation["provider_steps"]), now=lambda: NOW)
    contacts = [dict(row, collected_at=datetime.fromisoformat(row["collected_at"]))
                for row in fixtures["contacts"]]
    facts = MemoryEntityFacts(dataset_version=fixtures["dataset_version"],
                              identity_hash=fixtures["identity_hash"],
                              entities=fixtures["entities"], contacts=contacts, now=lambda: NOW)
    return TestClient(create_app(ChatEngine(gateway, facts), service_token="",
                                 environment="development", clock=lambda: NOW))
