"""Shared setup."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from rockygpt_brain.api.app import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app(service_token="", environment="development")) as client:
        yield client
