import json
import logging

from rockygpt_brain.observability.logging import JsonLogFormatter

_formatter = JsonLogFormatter()


def _render(msg: str, *, extra: dict[str, object] | None = None, exc_info=None) -> dict:
    logger = logging.getLogger("test.logging")
    logger.handlers.clear()
    record = logger.makeRecord(
        "test.logging", logging.INFO, "test.py", 1, msg, (), exc_info, extra=extra
    )
    return json.loads(_formatter.format(record))


def test_url_with_secret_query_param_is_stripped() -> None:
    out = _render("failed calling https://api.example.com/v1?token=SECRET123&x=1")
    assert "SECRET123" not in out["message"]
    assert "[redacted-url]" in out["message"]


def test_secret_inside_allowed_extra_field_is_redacted() -> None:
    out = _render("tool failed", extra={"error_code": "sk-abcdefghijklmnopqrstuvwx1234"})
    assert "sk-abcdefghijklmnopqrstuvwx1234" not in out["error_code"]
    assert "[redacted-secret]" in out["error_code"]


def test_unknown_extra_field_is_dropped() -> None:
    out = _render("x", extra={"user_message": "raw text should never appear"})
    assert "user_message" not in out


def test_allowed_extra_field_with_ordinary_value_passes_through() -> None:
    out = _render("x", extra={"route": "standard", "latency_ms": 42})
    assert out["route"] == "standard"
    assert out["latency_ms"] == 42


def test_non_primitive_extra_value_is_stringified_and_bounded() -> None:
    out = _render("x", extra={"dataset_id": ["a", "b"]})
    assert isinstance(out["dataset_id"], str)


def test_message_length_is_bounded() -> None:
    out = _render("x" * 5000)
    assert len(out["message"]) <= 500


def test_exception_summary_bounded_and_redacted_no_full_traceback() -> None:
    try:
        raise ValueError("token=" + "A" * 50)
    except ValueError:
        import sys

        out = _render("boom", exc_info=sys.exc_info())
    assert "exception" in out
    assert len(out["exception"]) <= 300
    assert "test_logging_formatter.py" not in out["exception"]
