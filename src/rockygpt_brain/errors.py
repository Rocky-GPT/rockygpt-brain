"""Errors the public API can return, named by cause.

`retryable` is the one field a client acts on, and it is not a judgement to
make at a raise site — it is a property of why the turn failed. Told to retry
a missing API key or an exhausted billing account, a client retries forever
against something no amount of retrying will fix; told not to retry a model
that hiccuped, it gives up on a turn that would have worked.

So retryability is not passed in. Pick the cause and it follows, and there is
no argument to get wrong. Every subclass below fixes its status, its code and
its retryability; the only thing a raise site supplies is what to tell the
person, and — through `raise ... from` — what actually went wrong, which stays
out of the response and in the log where an operator can see it.

The set is deliberately small: it is sized by the distinctions a *client* can
act on, not the ones an operator cares about. Those live in the `__cause__`
chain, which is why every raise in this codebase has one.
"""

from __future__ import annotations

from typing import ClassVar

from rockygpt_brain.api.contracts import ErrorCode


class ServiceError(Exception):
    """A failure with a public message. Never raised directly — pick a cause."""

    status_code: ClassVar[int]
    code: ClassVar[ErrorCode]
    retryable: ClassVar[bool]

    def __init__(self, public_message: str, retry_after_seconds: int | None = None) -> None:
        super().__init__(public_message)
        self.public_message = public_message
        self.retry_after_seconds = retry_after_seconds

    def __str__(self) -> str:
        return self.public_message


class BadRequest(ServiceError):
    """The request was malformed. Sending it again will not help."""

    status_code = 400
    code: ClassVar[ErrorCode] = "INVALID_REQUEST"
    retryable = False


class Unauthorized(ServiceError):
    """No valid credential. Not a thing the client fixes by waiting."""

    status_code = 401
    code: ClassVar[ErrorCode] = "UNAUTHORIZED"
    retryable = False


class Internal(ServiceError):
    """Something unforeseen. Not advertised as retryable — it may repeat."""

    status_code = 500
    code: ClassVar[ErrorCode] = "INTERNAL_ERROR"
    retryable = False


class Unavailable(ServiceError):
    """Something that usually works did not, this once. Worth another try.

    A model that returned nothing, a plan the registry would not accept — the
    next attempt has a real chance, because none of it is deterministic.
    """

    status_code = 503
    code: ClassVar[ErrorCode] = "SERVICE_UNAVAILABLE"
    retryable = True


class DatasetUnavailable(Unavailable):
    """Campus data did not answer. The turn is lost, the lookup is not gone."""

    code: ClassVar[ErrorCode] = "DATASET_UNAVAILABLE"


class Unsupported(ServiceError):
    """Rocky cannot do this, and asking again changes nothing.

    A capability with no code behind it, a key that is not set, a billing
    account with nothing left. The wait is on a person, not on the service, so
    a client that retries is only making noise. This is the distinction the
    boolean kept losing: it looks exactly like `Unavailable` from the outside
    and is its opposite in what to do about it.
    """

    status_code = 503
    code: ClassVar[ErrorCode] = "SERVICE_UNAVAILABLE"
    retryable = False
