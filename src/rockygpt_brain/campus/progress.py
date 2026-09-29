"""Public operation metadata and explicitly unverified answer previews, never reasoning."""

from collections.abc import Callable
from time import monotonic
from typing import TYPE_CHECKING, Any, Literal, NotRequired, TypedDict

if TYPE_CHECKING:
    from rockygpt_brain.retrieval.models import SearchQuery

ProgressStage = Literal[
    "connecting", "understanding", "retrieving", "calculating", "composing", "reviewing"
]


class ProgressSubject(TypedDict):
    topic: str
    meal: NotRequired[str]
    date_from: NotRequired[str]
    date_to: NotRequired[str]


class SafetyBlock(TypedDict):
    """The code-written safety block, exactly as the final answer starts with it."""

    answer: str
    citations: list[dict[str, Any]]


class ProgressUpdate(TypedDict):
    stage: ProgressStage
    subjects: list[ProgressSubject]
    operation: NotRequired[str]
    draft: NotRequired[str]
    # Sent once, as soon as Jev reads danger, before any lookup or draft (09-29).
    safety: NotRequired[SafetyBlock]


ProgressCallback = Callable[[ProgressUpdate], None]


def search_subject(query: "SearchQuery") -> ProgressSubject:
    subject: ProgressSubject = {"topic": query.collection}
    if query.date_from:
        subject["date_from"] = query.date_from.isoformat()
    if query.date_to:
        subject["date_to"] = query.date_to.isoformat()
    if query.collection == "menu" and query.filters and query.filters.meal:
        meal = query.filters.meal.lower().replace("-", "").replace(" ", "")
        if meal in {"breakfast", "brunch", "lunch", "dinner", "latenight"}:
            subject["meal"] = meal
    return subject


class TurnCancelled(Exception):
    """The client left; settle in-flight work but start no further operation."""


class WorkLog:
    """Development only: when each step began and every Jev and GPT call made in it, so
    the Dev control room can say who did the work. Times are milliseconds from when the
    request arrived. Whatever a step spent outside these calls was the Brain's own code:
    lookups, calculations, rendering and the ledger's bookkeeping. Steps are every
    progress update, repeats included, except the early safety block, which belongs
    to the step it was sent in; a call names its step by index."""

    def __init__(self, started: float) -> None:
        self.started = started
        self.steps: list[dict[str, Any]] = [{"stage": "connecting", "subjects": [], "atMs": 0}]
        self.calls: list[dict[str, Any]] = []

    def ms(self, at: float) -> int:
        return round((at - self.started) * 1000)

    def watch(self, progress: ProgressCallback | None) -> ProgressCallback:
        """`progress`, noting each step first. Draft text stays in the drafts."""

        def noted(update: ProgressUpdate) -> None:
            # The safety block goes out mid-step, right after routing (09-29); a step of
            # its own would show the Timeline an empty second "understanding".
            if "safety" in update and update["stage"] == self.steps[-1]["stage"]:
                self.steps[-1]["safety"] = True
                if progress is not None:
                    progress(update)
                return
            step: dict[str, Any] = {"stage": update["stage"],
                                    "subjects": list(update["subjects"]),
                                    "atMs": self.ms(monotonic())}
            if "operation" in update:
                step["operation"] = update["operation"]
            self.steps.append(step)
            if progress is not None:
                progress(update)

        return noted

    def at(self) -> int:
        """The step now running, by index."""
        return len(self.steps) - 1

    def decided(self, step: int, **facts: Any) -> None:
        """What the Brain decided in a step, from its own state and never from the step's
        name: Jev's route (`routing`), what a draft call came back with (`draft`)."""
        self.steps[step].update(facts)

    def found(self, step: int, lookup: dict[str, Any]) -> None:
        """What one lookup in a step got back. Lookups run together share a step."""
        self.steps[step].setdefault("lookups", []).append(lookup)

    def call(self, who: str, what: str, sent: float, returned: float, failed: bool) -> None:
        self.calls.append({"who": who, "what": what, "step": len(self.steps) - 1,
                           "startMs": self.ms(sent), "ms": round((returned - sent) * 1000),
                           **({"failed": True} if failed else {})})

    def report(self) -> dict[str, Any]:
        return {"steps": list(self.steps), "calls": list(self.calls),
                "endMs": self.ms(monotonic())}
