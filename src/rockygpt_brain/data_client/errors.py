"""Exceptions raised by DataServiceClient, mapped from spec/data-service.md's
error envelope: {"error": {"code", "message", "retryable"}}.
"""

from __future__ import annotations


class DataClientError(Exception):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        retryable: bool,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status_code = status_code


class DataServiceUnavailable(DataClientError):
    """503 UNAVAILABLE, or a transport-level failure (timeout/connection)."""

    def __init__(self, message: str = "Campus data is temporarily unavailable.") -> None:
        super().__init__(
            code="UNAVAILABLE", message=message, retryable=True, status_code=503
        )


class DataContractError(DataClientError):
    """The data service returned an ostensibly-successful response that does
    not match its documented contract (spec/data-api.openapi.yaml) — not
    valid JSON, missing/extra/wrong-typed fields, or over a resource-size
    bound. Distinct from the error envelope DataClientError otherwise
    represents: this is *our* parsing of a 200 response failing, and it
    must fail closed rather than leak a KeyError/TypeError/ValueError from
    deep inside model parsing (see data_client/models.py)."""

    def __init__(
        self, message: str = "Campus data response did not match its contract."
    ) -> None:
        super().__init__(code="SERVICE_UNAVAILABLE", message=message, retryable=True)
