"""Application fact reads expose the same pinned canonical reader as chat."""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from rockygpt_brain.api.app import create_app
from rockygpt_brain.engine import ChatEngine
from rockygpt_brain.provider import Completion, TurnBudget
from rockygpt_brain.retrieval import EvidenceUnavailable, MemoryEntityFacts

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)
PIN = {"dataset_version": "fixture-release", "identity_hash": "fixture-identities"}


class NoModelCalls:
    async def complete(
        self,
        *,
        input: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        budget: TurnBudget,
    ) -> Completion:
        raise AssertionError("A fact HTTP read must not call a model.")

    async def ready(self) -> bool:
        return True


@pytest.fixture
def fact_client() -> Iterator[tuple[TestClient, MemoryEntityFacts]]:
    facts = MemoryEntityFacts(
        dataset_version=PIN["dataset_version"],
        identity_hash=PIN["identity_hash"],
        now=lambda: NOW,
        entities=[
            {
                "id": "registrar",
                "kind": "office",
                "name": "Registrar",
                "aliases": [],
                "links": [
                    {
                        "collection": "contacts",
                        "source_key": "directory",
                        "source_record_keys": ["registrar-contact"],
                    }
                ],
            }
        ],
        contacts=[
            {
                "id": "record-registrar",
                "source_key": "directory",
                "source_record_key": "registrar-contact",
                "name": "Registrar",
                "email": "published@example.edu",
                "phone": "201-555-0100",
                "office": "D224",
                "contact_note": "source-only-contact-note",
                "canonical_url": "https://example.edu/registrar",
                "collected_at": NOW,
                "freshness_sla_hours": 168,
            }
        ],
    )
    application = create_app(
        ChatEngine(NoModelCalls(), facts), environment="development", service_token=""
    )
    with TestClient(application) as client:
        yield client, facts


def test_fact_route_returns_shared_all_field_result_without_a_model(
    fact_client: tuple[TestClient, MemoryEntityFacts],
) -> None:
    client, facts = fact_client
    response = client.get("/v1/entities/registrar/facts", params=PIN)
    assert response.status_code == 200
    assert response.json() == facts.get_office_facts(
        "registrar",
        None,
        PIN["dataset_version"],
        identity_hash=PIN["identity_hash"],
    )
    assert {prop["key"] for prop in response.json()["properties"]} == {
        "name",
        "department",
        "email",
        "phones",
        "offices",
        "prefers_email",
        "preferred_contact",
        "contact_note",
        "website",
        "hours",
    }
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("changed_pin", ["dataset_version", "identity_hash"])
def test_fact_route_requires_current_release_and_identity_pins(
    fact_client: tuple[TestClient, MemoryEntityFacts],
    changed_pin: str,
) -> None:
    client, _ = fact_client
    params = {**PIN, changed_pin: "private-input-marker"}
    response = client.get("/v1/entities/registrar/facts", params=params)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "dataset_changed"
    assert "private-input-marker" not in response.text
    assert "published@example.edu" not in response.text
    assert "source-only-contact-note" not in response.text


def test_fact_route_unknown_identity_has_no_evidence_or_input_echo(
    fact_client: tuple[TestClient, MemoryEntityFacts],
) -> None:
    client, _ = fact_client
    response = client.get("/v1/entities/private-input-marker/facts", params=PIN)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_entity"
    assert "private-input-marker" not in response.text
    assert "published@example.edu" not in response.text
    assert "sources" not in response.json()


def test_fact_route_database_failure_is_sanitized(
    fact_client: tuple[TestClient, MemoryEntityFacts],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, facts = fact_client

    def unavailable(*args: Any, **kwargs: Any) -> Any:
        raise EvidenceUnavailable("database password=private-input-marker source-only-contact-note")

    monkeypatch.setattr(facts, "get_office_facts", unavailable)
    response = client.get("/v1/entities/registrar/facts", params=PIN)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "data_unavailable"
    assert response.json()["error"]["retryable"] is True
    assert "private-input-marker" not in response.text
    assert "source-only-contact-note" not in response.text
    assert "published@example.edu" not in response.text


def test_fact_route_rejects_missing_pins_before_reading_evidence(
    fact_client: tuple[TestClient, MemoryEntityFacts],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, facts = fact_client

    def should_not_read(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("A request without release pins must not read facts.")

    monkeypatch.setattr(facts, "get_office_facts", should_not_read)
    response = client.get(
        "/v1/entities/registrar/facts", params={"dataset_version": "private-input"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert "private-input" not in response.text
