"""Proves the package imports and the minimal HTTP shell runs."""

from fastapi.testclient import TestClient

import rockygpt_brain
from rockygpt_brain.api.app import app

client = TestClient(app)


def test_package_imports() -> None:
    assert rockygpt_brain.__version__ == "0.0.0"


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness() -> None:
    response = client.get("/readiness")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_readiness_chat_logs() -> None:
    response = client.get("/readiness/chat-logs")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
