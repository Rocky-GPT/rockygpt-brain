"""Jev, Typesafe's fast reading model: yes/no and pick-one answers about text. It never writes.

Each call is held in the ledger at Jev's price before it is sent, and settled with the
tokens Typesafe reports (spending.py). A call that goes wrong raises JevError, and the
turn fails on purpose with a clear failure (turn.JEV_FAILURES), because without Jev's
answers there is no plan. A budget or ledger refusal raises SpendingError, which stops
all paid work.
"""

import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from threading import Thread
from time import monotonic
from typing import Any

import httpx

from rockygpt_brain.spending import CAMPUS_TIMEZONE, Ledger, paid_call

JEV_URL = "https://api.typesafe.ai/v1/systemone"
# Typesafe's limits: the state plus any one question within 32k tokens, a call within 64k.
QUESTION_TOKENS = 32_000
CALL_TOKENS = 64_000
# A call is sent only if it should fit those limits, at this many bytes to the token. Our own
# calls (questions and chat, as JSON) ran about 3.1 bytes to the token on 09-29 (9,500 bytes,
# 3,069 tokens), and plain English runs over 4. Fewer than 2 means text of mostly symbols or
# rare characters, and Typesafe refuses that one itself. The ledger still holds the worst
# case of one byte to the token (token_bound), so the estimate never lowers what is held.
BYTES_PER_TOKEN = 2
# Answered calls took a median 0.18 s and at most 0.93 s over 760 calls on 09-29. On 09-28 a
# degraded window sent 28% of calls past 2 s, and past 2 s the turn fails with model_timeout.
# The whole call, from connecting to the last byte of the reply, gets this long.
TIMEOUT_SECONDS = 2.0
MAX_REPLY_BYTES = 1_048_576

Question = dict[str, Any]


@dataclass(frozen=True)
class Price:
    """A price checked on the provider's own page, trusted only inside its window.
    .github/workflows/price-window.yml opens an issue two weeks before it ends."""

    model: str
    input_nusd: int
    valid_from: date
    valid_until: date
    source: str

    def current(self, now: datetime) -> bool:
        return self.valid_from <= now.astimezone(CAMPUS_TIMEZONE).date() < self.valid_until


def read_price(name: str) -> Price:
    entry = json.loads(Path(__file__).with_name("prices.json").read_text())[name]
    return Price(
        model=entry["model"],
        input_nusd=int(entry["input_nusd"]),
        valid_from=date.fromisoformat(entry["valid_from"]),
        valid_until=date.fromisoformat(entry["valid_until"]),
        source=entry["source"],
    )


JEV_PRICE = read_price("jev")


class JevError(Exception):
    """Jev gave no usable answer. `code` says why, for diagnostics and the turn log, and
    `sent` whether a hold was placed and the call attempted. `status` is Typesafe's HTTP
    status when it answered with one. `uncharged` is for a call refused before Typesafe read
    it (no connection, a rejected key or request, rate limiting, overload): nothing was spent,
    so its hold is settled at zero and not left counted against the month. `elapsed_ms` is
    set once the call has come back."""

    def __init__(self, code: str, *, sent: bool = True, status: int | None = None,
                 uncharged: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.sent = sent
        self.status = status
        self.uncharged = uncharged
        self.elapsed_ms: int | None = None


def noul(instructions: str, yes: str, no: str) -> Question:
    """A yes/no question. Jev answers with the probability of yes."""
    return {"type": "noul", "instructions": instructions, "criteria": {"true": yes, "false": no}}


def choice(instructions: str, options: Mapping[str, str]) -> Question:
    """Pick one option. Each option describes one case: a catch-all option that listed
    several cases took 10-20% of every answer (old Brain, 09-28)."""
    return {"type": "choice", "instructions": instructions, "criteria": dict(options)}


@dataclass(frozen=True)
class Yes:
    """A yes/no answer: how likely Jev thinks yes is."""

    probability: float


@dataclass(frozen=True)
class Pick:
    """A pick-one answer: the option Jev leans to, and how likely it is. `confidence` is
    Typesafe's own 0 to 1 figure for the whole spread. Its formula isn't published, but on
    900 shuttle readings it matched (options x top probability - 1) / (options - 1) to
    within 0.02, so it is the top probability rescaled by how many options there are, and
    ranks answers the same way within one question. Nothing here decides on it."""

    choice: str
    probability: float
    confidence: float
    probabilities: dict[str, float]


Answer = Yes | Pick


@dataclass(frozen=True)
class Reply:
    """What Typesafe sent back, before its answers are checked."""

    answers: Any
    input_tokens: int
    output_tokens: int
    model: str
    response_id: str


Send = Callable[[dict[str, Any], float], Reply]
# Told when a call went out and came back (monotonic seconds), and whether it failed.
Timed = Callable[[float, float, bool], None]


@dataclass(frozen=True)
class Asked:
    """A call's checked answers, with what it cost."""

    answers: dict[str, Answer]
    cost_nusd: int
    input_tokens: int
    # Typesafe's own time, without the ledger's.
    elapsed_ms: int


def json_bytes(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


def token_bound(value: Any) -> int:
    """At least the tokens `value` takes: no token is shorter than one byte. The 8,192 on
    top covers Typesafe's own framing. This is what the ledger holds."""
    return json_bytes(value) + 8192


def token_estimate(value: Any) -> int:
    """About the tokens `value` takes, for deciding whether Typesafe will take the call."""
    return math.ceil(json_bytes(value) / BYTES_PER_TOKEN)


class Jev:
    def __init__(self, send: Send, ledger: Ledger, price: Price = JEV_PRICE) -> None:
        self._send = send
        self._ledger = ledger
        self.price = price

    def ask(self, request_id: str, state: dict[str, Any], questions: dict[str, Question],
            now: datetime, timed: Timed | None = None) -> Asked:
        """Every question in one call. The answers come back checked, or JevError.
        `timed` hears when the call to Typesafe went out, came back, and whether it failed."""
        if not self.price.current(now):
            raise JevError("routing_price_unavailable", sent=False)
        body = {"model": self.price.model, "state": state, "questions": questions}
        if not questions or token_estimate(body) > CALL_TOKENS or any(
                token_estimate({"state": state, "question": question}) > QUESTION_TOKENS
                for question in questions.values()):
            raise JevError("routing_context_limit", sent=False)
        bound = token_bound(body)
        with paid_call(self._ledger, request_id, "routing", bound * self.price.input_nusd,
                       now, {"provider": "typesafe", "requested_model": self.price.model,
                             "input_token_bound": bound,
                             "price_nusd_per_input_token": self.price.input_nusd}) as receipt:
            sent, failed = monotonic(), True
            try:
                reply = self._send(body, TIMEOUT_SECONDS)
                failed = False
            except JevError as error:
                error.elapsed_ms = round((monotonic() - sent) * 1000)
                if error.uncharged:
                    # Refused before Typesafe read it: settled at zero, with why.
                    receipt.cost, receipt.error = 0, error.code
                raise
            finally:
                returned = monotonic()
                if timed is not None:
                    timed(sent, returned, failed)
            elapsed_ms = round((returned - sent) * 1000)
            receipt.cost = reply.input_tokens * self.price.input_nusd
            receipt.usage = {"input_tokens": reply.input_tokens,
                             "output_tokens": reply.output_tokens}
            receipt.response_id = reply.response_id
            receipt.model = reply.model
            # Settled either way: Typesafe charged for the call.
            try:
                if reply.model != self.price.model:
                    raise JevError("routing_model_changed")
                answers = checked(reply.answers, questions)
            except JevError as error:
                error.elapsed_ms = elapsed_ms
                raise
        return Asked(answers, receipt.cost, reply.input_tokens, elapsed_ms)


def probability(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or (
            not math.isfinite(value) or not 0 <= value <= 1):
        raise JevError("routing_invalid_response")
    return float(value)


def checked(answers: Any, questions: dict[str, Question]) -> dict[str, Answer]:
    """Every question answered, in the shape asked, with sound probabilities."""
    if not isinstance(answers, dict) or answers.keys() != questions.keys():
        raise JevError("routing_invalid_response")
    result: dict[str, Answer] = {}
    for key, question in questions.items():
        answer = answers[key]
        if not isinstance(answer, dict) or answer.get("type") != question["type"]:
            raise JevError("routing_invalid_response")
        if question["type"] == "noul":
            result[key] = Yes(probability(answer.get("noul")))
            continue
        options = question["criteria"]
        spread = answer.get("probabilities")
        picked = answer.get("choice")
        if not isinstance(spread, dict) or spread.keys() != options.keys() or (
                not isinstance(picked, str) or picked not in options):
            raise JevError("routing_invalid_response")
        values = {option: probability(value) for option, value in spread.items()}
        # Jev rounds each probability to the hundredth, so a sound answer can sum to 0.99
        # or 1.01. Held to 0.001, one answer in eight was thrown away (old Brain, 09-28).
        if abs(sum(values.values()) - 1) > 0.005 * len(values) + 1e-9 or (
                values[picked] < max(values.values())):
            raise JevError("routing_invalid_response")
        result[key] = Pick(picked, values[picked], probability(answer.get("confidence")),
                           values)
    return result


def refused(status: int) -> JevError:
    """What an HTTP error status from Typesafe means. Their API reference lists 401 (bad
    key), 422 (a body that failed validation), 429 (rate limit) and 529 (overloaded). A refusal
    before Typesafe read the call costs nothing; a 5xx or a 408 may have been read."""
    if status == 429:
        return JevError("routing_rate_limited", status=status, uncharged=True)
    if status == 529:
        return JevError("routing_overloaded", status=status, uncharged=True)
    if status in {401, 403}:
        return JevError("routing_auth_failed", status=status, uncharged=True)
    if 400 <= status < 500 and status != 408:
        return JevError("routing_request_rejected", status=status, uncharged=True)
    return JevError("routing_provider_error", status=status)


class TypesafeHttp:
    """Sends a call to Typesafe, and gives up `timeout` seconds after starting, however the
    time is spent: httpx's own limits are per step (connecting, then waiting for the first
    byte, then each chunk), so alone they let one call run for several times as long."""

    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        self._api_key = api_key
        # No proxy, .netrc or certificate settings from the environment: the key goes
        # straight to Typesafe. A connection that fails to open is tried once more, which is
        # safe: nothing has been sent yet.
        self._client = client or httpx.Client(
            trust_env=False,
            transport=httpx.HTTPTransport(retries=1, trust_env=False, http2=False))

    def __call__(self, body: dict[str, Any], timeout: float) -> Reply:
        outcome: list[Reply | BaseException] = []

        def post() -> None:
            try:
                outcome.append(self._post(body, timeout))
            except BaseException as error:
                outcome.append(error)  # raised again, in the caller's thread, below

        worker = Thread(target=post, name="typesafe-call", daemon=True)
        worker.start()
        worker.join(timeout)
        if not outcome:
            # Left to finish on httpx's own limits, and its answer thrown away.
            raise JevError("routing_timeout")
        result = outcome[0]
        if isinstance(result, BaseException):
            raise result
        return result

    def _post(self, body: dict[str, Any], timeout: float) -> Reply:
        deadline = monotonic() + timeout
        try:
            with self._client.stream(
                "POST", JEV_URL, json=body, timeout=httpx.Timeout(timeout),
                headers={"Authorization": "Bearer " + self._api_key},
            ) as response:
                if response.status_code >= 400:
                    raise refused(response.status_code)
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_REPLY_BYTES:
                        raise JevError("routing_invalid_response")
                    if monotonic() > deadline:
                        raise JevError("routing_timeout")
                request_id = (response.headers.get("x-typesafe-request-id")
                              or response.headers.get("x-request-id", ""))
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as error:
            # Never got as far as sending.
            raise JevError("routing_unavailable", uncharged=True) from error
        except httpx.TimeoutException as error:
            raise JevError("routing_timeout") from error
        except httpx.HTTPError as error:
            raise JevError("routing_unavailable") from error
        try:
            raw = json.loads(data)
            usage = raw["usage"]
            input_tokens, output_tokens = usage["input_tokens"], usage["output_tokens"]
        except (ValueError, KeyError, TypeError) as error:
            raise JevError("routing_usage_unknown") from error
        if not all(type(count) is int and count >= 0 for count in (input_tokens, output_tokens)):
            raise JevError("routing_usage_unknown")
        return Reply(
            answers=raw.get("answers"),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=str(raw.get("model", "")),
            response_id=str(raw.get("id") or request_id),
        )
