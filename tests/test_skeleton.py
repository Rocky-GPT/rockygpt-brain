"""Proves the package imports and the minimal HTTP shell runs."""

import rockygpt_brain
from rockygpt_brain.api.app import ChatRequest, chat, health, readiness


def test_package_imports() -> None:
    assert rockygpt_brain.__version__ == "0.0.0"


def test_health() -> None:
    assert health() == {"status": "ok"}


def test_readiness() -> None:
    assert readiness() == {"status": "ready"}


def test_chat() -> None:
    assert chat(ChatRequest(message="Hello")) == {"answer": "RockyGPT chat is connected."}
