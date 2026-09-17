"""Public operation metadata and explicitly unverified answer previews, never reasoning."""

from collections.abc import Callable
from typing import TYPE_CHECKING, Literal, NotRequired, TypedDict

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


class ProgressUpdate(TypedDict):
    stage: ProgressStage
    subjects: list[ProgressSubject]
    operation: NotRequired[str]
    draft: NotRequired[str]


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
