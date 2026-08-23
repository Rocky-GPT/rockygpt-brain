"""JSON structured logging with a fixed field allow-list and redaction.

Per THREAT_MODEL.md §3.3, raw message text, identifiers, and secrets must
never reach logs. Every call site in this codebase is expected to log a
static message plus a small `extra={...}` dict of operational fields
(route, latency, counts) — never raw chat content or unhashed identifiers.
This formatter treats that as a convention to defend, not a guarantee:

- The rendered message and any exception summary are passed through
  `security.redaction.redact` *and* through a logging-specific URL scrubber
  (below) before being bounded to a fixed length. `redaction.redact` is
  tuned for *stored chat text*, where a citation URL is legitimate content
  that must survive — it does not strip URLs. Logs have no such exception:
  an exception raised by an HTTP dependency can easily embed a full request
  URL with a token-bearing query string, so every URL is unconditionally
  replaced here, independent of `redact`.
- Full tracebacks (file paths, source lines, local variables) are never
  emitted — only a redacted, URL-stripped, bounded `ExceptionType: message`
  summary.
- Every `extra` value, not just strings, is sanitized before it can reach
  the output: only `bool`/`int`/`float` pass through as-is; anything else
  (including a plain `str`) is stringified and put through the same
  redact + URL-strip + bound pipeline. `json.dumps` is never given a
  `default=str` escape hatch, so nothing can bypass sanitization by being
  an unexpected type.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from types import TracebackType
from typing import Any

from rockygpt_brain.security.redaction import redact

# Matches logging.LogRecord.exc_info's declared type: either a real
# (type, exception, traceback) triple, or the all-None triple
# sys.exc_info() returns when there is no active exception.
_ExcInfo = (
    tuple[type[BaseException], BaseException, TracebackType | None] | tuple[None, None, None]
)

ALLOWED_EXTRA_FIELDS = frozenset(
    {
        "request_id",
        "route",
        "status_code",
        "latency_ms",
        "cleared",
        "deleted",
        "tool_count",
        "dataset_id",
        "dataset_version",
        "error_code",
    }
)

MAX_MESSAGE_LENGTH = 500
MAX_EXCEPTION_LENGTH = 300
MAX_EXTRA_VALUE_LENGTH = 200

_URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)


def _strip_urls(text: str) -> str:
    return _URL_PATTERN.sub("[redacted-url]", text)


def _sanitize_text(text: str, *, max_length: int) -> str:
    scrubbed = _strip_urls(text)
    scrubbed = redact(scrubbed) or ""
    return scrubbed[:max_length]


def _sanitize_extra_value(value: object) -> bool | int | float | str:
    if isinstance(value, bool) or isinstance(value, int | float):
        return value
    return _sanitize_text(str(value), max_length=MAX_EXTRA_VALUE_LENGTH)


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": _sanitize_text(record.getMessage(), max_length=MAX_MESSAGE_LENGTH),
        }
        for field in ALLOWED_EXTRA_FIELDS:
            if hasattr(record, field):
                payload[field] = _sanitize_extra_value(getattr(record, field))
        if record.exc_info:
            payload["exception"] = self._exception_summary(record.exc_info)
        return json.dumps(payload)

    @staticmethod
    def _exception_summary(exc_info: _ExcInfo) -> str:
        exc_type, exc_value, _traceback = exc_info
        # `logging.LogRecord.exc_info` is typed to allow the all-None
        # variant (mirroring `sys.exc_info()`'s return with no active
        # exception) even though every call site here only reaches this
        # method after checking `if record.exc_info:` — a genuinely non-
        # empty tuple, but `(None, None, None)` is itself non-empty/truthy,
        # so that check alone doesn't rule this branch out statically.
        if exc_type is None or exc_value is None:
            return "Exception"
        summary = f"{exc_type.__name__}: {exc_value}"
        return _sanitize_text(summary, max_length=MAX_EXCEPTION_LENGTH)


def configure_logging(*, level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
