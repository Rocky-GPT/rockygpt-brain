"""Feedback keeps what a student said, without their personal details."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from rockygpt_brain.api.app import app
from rockygpt_brain.governance.redaction import redact

REQUEST_ID = "0b7c2d4e-5f60-4a1b-8c9d-0e1f2a3b4c5d"


def test_redaction_removes_personal_details_and_keeps_campus_numbers() -> None:
    text = (
        "I'm R01234567, email me at jdoe@ramapo.edu or 551-555-0100. "
        "SSN 123-45-6789, card 4111 1111 1111 1111. The Registrar is 201-684-7695."
    )
    assert redact(text) == (
        "I'm [student id], email me at [email] or [phone]. "
        "SSN [ssn], card [card]. The Registrar is 201-684-7695."
    )
    assert redact(None) is None and redact("") == ""


def submit(payload: dict[str, Any], stored: Any) -> tuple[dict[str, Any], MagicMock]:
    cursor = MagicMock()
    cursor.fetchone.return_value = stored
    connection = MagicMock()
    connection.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor
    with patch("psycopg.connect", return_value=connection):
        body = TestClient(app).post("/v1/feedback", json=payload).json()
    return body, cursor


@pytest.fixture(autouse=True)
def ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAIN_LEDGER_DATABASE_URL", "postgresql://ledger?sslrootcert=x")


def test_feedback_stores_the_rated_turn_with_the_students_words_redacted() -> None:
    body, cursor = submit({
        "requestId": REQUEST_ID, "rating": -1, "category": "other",
        "comments": "Wrong hours, text me at 973-555-0199",
        "question": "When does Birch close? I'm jdoe@ramapo.edu",
        "answer": "Call the Registrar at 201-684-7695.",
    }, stored={"id": "row"})
    assert body == {"success": True}
    insert = cursor.execute.call_args_list[-1]
    params = insert.args[1]
    assert params[1] == "When does Birch close? I'm [email]"
    assert params[2] == "Call the Registrar at 201-684-7695."
    assert params[5] == "Wrong hours, text me at [phone]"
    assert "COALESCE(EXCLUDED.comments" in insert.args[0]


def test_an_operator_review_does_not_replace_student_feedback() -> None:
    body, _ = submit(
        {"requestId": REQUEST_ID, "rating": 1, "category": "operator_review"}, stored=None
    )
    assert body["success"] is False
    assert body["error"] == "student_feedback_exists"


def test_malformed_feedback_is_refused_before_the_database() -> None:
    client = TestClient(app)
    malformed = {"requestId": "not-a-uuid", "rating": 1}
    assert client.post("/v1/feedback", json=malformed).status_code == 422
    too_long = {"requestId": REQUEST_ID, "rating": -1, "comments": "x" * 2001}
    assert client.post("/v1/feedback", json=too_long).status_code == 422
