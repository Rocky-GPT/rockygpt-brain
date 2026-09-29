"""A Jev and a ledger that live in memory, for tests that must not spend."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from rockygpt_brain.decisions import ASKS, NAMED, NEEDS, SUBJECTS
from rockygpt_brain.jev import JEV_PRICE, Jev, JevError, Reply
from rockygpt_brain.spending import Category, SpendingError


@dataclass
class MemoryLedger:
    refuse: str | None = None
    holds: dict[str, dict[str, Any]] = field(default_factory=dict)

    def hold(self, request_id: str, category: Category, amount: int,
             metadata: dict[str, Any], now: datetime) -> str:
        if self.refuse:
            raise SpendingError(self.refuse, reset_at="2026-10-01T00:00:00-04:00")
        operation = f"op{len(self.holds) + 1}"
        self.holds[operation] = {"state": "reserved", "amount": amount, "category": category}
        return operation

    def settle(self, operation: str, cost: int, usage: dict[str, int], response_id: str,
               model: str, elapsed_ms: int, now: datetime) -> None:
        self.holds[operation].update(state="settled", cost=cost, model=model)

    def uncertain(self, operation: str, code: str, elapsed_ms: int) -> None:
        self.holds[operation].update(state="uncertain", code=code)


def yes(probability: float) -> dict[str, Any]:
    return {"type": "noul", "noul": probability}


def pick(chosen: str, spread: dict[str, float], confidence: float = 0.95) -> dict[str, Any]:
    return {"type": "choice", "choice": chosen, "probabilities": spread,
            "confidence": confidence}


def sure_pick(chosen: str, options: Any, probability: float = 0.96) -> dict[str, Any]:
    """Jev sure of `chosen` among the keys of `options`, the rest shared evenly."""
    rest = (1 - probability) / (len(options) - 1)
    return pick(chosen, {option: probability if option == chosen else rest
                         for option in options})


def calm(**changes: dict[str, Any]) -> dict[str, Any]:
    """Jev's answers to an ordinary question ("Where is the Registrar?"), with `changes`
    on top."""
    return {
        "danger": pick("none", {"self_harm": 0.01, "danger": 0.02, "none": 0.97}),
        "own_account": yes(0.05),
        "own_account_only": yes(0.05),
        "needs_earlier": yes(0.03),
        "asks": sure_pick("fact", ASKS),
        "subject": sure_pick("places", SUBJECTS),
        "named": sure_pick("office", NAMED),
        "needs": sure_pick("campus_info", NEEDS),
        "multi_part": yes(0.02),
        "open_ended": yes(0.03),
        **changes,
    }


@dataclass
class ScriptedJev:
    """Answers every call with `answers`, or raises `error`. Keeps what it was sent."""

    answers: dict[str, Any] = field(default_factory=calm)
    error: JevError | None = None
    input_tokens: int = 1000
    model: str = JEV_PRICE.model
    sent: list[dict[str, Any]] = field(default_factory=list)

    def __call__(self, body: dict[str, Any], timeout: float) -> Reply:
        self.sent.append(body)
        if self.error is not None:
            raise self.error
        return Reply(self.answers, self.input_tokens, 0, self.model, "resp-1")


def fake_jev(script: ScriptedJev | None = None,
             ledger: MemoryLedger | None = None) -> tuple[Jev, ScriptedJev, MemoryLedger]:
    script = script or ScriptedJev()
    ledger = ledger or MemoryLedger()
    return Jev(script, ledger), script, ledger
