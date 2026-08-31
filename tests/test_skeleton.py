"""Proves the package imports and the minimal HTTP shell runs."""

from unittest.mock import Mock, patch

import rockygpt_brain
from rockygpt_brain.api.app import MODEL, ChatRequest, chat, health, readiness


def test_package_imports() -> None:
    assert rockygpt_brain.__version__ == "0.0.0"


def test_health() -> None:
    assert health() == {"status": "ok"}


def test_readiness() -> None:
    assert readiness() == {"status": "ready"}


def test_chat() -> None:
    response = Mock(output_text="Hello from the model.", model="gpt-test")
    with patch("rockygpt_brain.api.app.OpenAI") as client:
        client.return_value.responses.create.return_value = response
        assert chat(ChatRequest(message="Hello")) == {
            "answer": "Hello from the model.",
            "model": "gpt-test",
        }

    client.return_value.responses.create.assert_called_once_with(
        model=MODEL,
        input="Hello",
        store=False,
    )
