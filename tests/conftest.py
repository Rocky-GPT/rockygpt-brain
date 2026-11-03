"""Shared setup."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from rockygpt_brain.api.app import create_app


@pytest.fixture(autouse=True)
def no_read_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """Engines in tests answer as they did before the read step (triage.py), whose own tests turn
    it on with read_first=True: a scripted model's steps are each test's own."""
    monkeypatch.setenv("BRAIN_READ_FIRST", "0")


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app(service_token="", environment="development")) as client:
        yield client
