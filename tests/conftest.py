"""Shared setup."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from rockygpt_brain.api.app import app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as client:
        yield client
