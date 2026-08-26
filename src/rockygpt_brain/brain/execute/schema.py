from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import ceil
from typing import Any

OWN_KNOWLEDGE = "ownKnowledge"
CAMPUS_DATA = "campusData"
WEB = "web"
DOCUMENTS = "documents"
SAFETY = "safety"
RAG_DISABLED = "ragDisabled"

INSUFFICIENT_EVIDENCE = (
    "Ramapo's documents do not cover that, so I cannot answer it from them. "
    "The office that owns the subject will have it."
)


def nothing_matched(looked_for: dict[str, Any]) -> str:
    filters = looked_for.get("filters") or {}
    asked = " and ".join(f"{name} “{value}”" for name, value in filters.items())
    return (
        f"Nothing in Rocky's {looked_for.get('capability', 'campus')} records matched "
        f"{asked}. That is not the same as there being none — the records may file it "
        "under another name. It is worth asking again with a different word for it."
    )


class Mode(StrEnum):
    DETAILED = "detailed"
    COMPACT = "compact"
    PAGINATED = "paginated"


DETAILED_UP_TO = 10
COMPACT_UP_TO = 50
PAGE = 25


@dataclass(frozen=True, slots=True)
class Presentation:
    mode: Mode
    page_size: int
    page: int = 1
    total_pages: int = 1

    def summary(self) -> dict[str, Any]:
        return {"mode": self.mode.value, "page": self.page, "totalPages": self.total_pages}


def present(rows: int) -> Presentation:
    if rows <= DETAILED_UP_TO:
        return Presentation(Mode.DETAILED, rows)
    if rows <= COMPACT_UP_TO:
        return Presentation(Mode.COMPACT, rows)
    return Presentation(Mode.PAGINATED, PAGE, total_pages=ceil(rows / PAGE))


@dataclass(frozen=True, slots=True)
class Ordering:
    by: str
    direction: str

    def summary(self) -> dict[str, str]:
        return {"by": self.by, "direction": self.direction}


@dataclass(frozen=True, slots=True)
class Execution:
    answer_from: str
    note: str = ""
    results: list[dict[str, Any]] = field(default_factory=list)
    count: int | None = None
    looked_for: dict[str, Any] = field(default_factory=dict)
    found: int | None = None
    shown: Presentation | None = None
    ordering: Ordering | None = None

    @property
    def ran(self) -> bool:
        return self.answer_from in (CAMPUS_DATA, WEB, DOCUMENTS, SAFETY)

    def summary(self) -> dict[str, Any]:
        if not self.ran:
            return {"answerFrom": self.answer_from, "note": self.note}
        if self.count is not None:
            return {"answerFrom": self.answer_from, "count": self.count}
        summarised: dict[str, Any] = {"answerFrom": self.answer_from}
        if self.results:
            summarised["showing"] = len(self.results)
        if self.found is not None:
            summarised["outOf"] = self.found
        if self.ordering is not None and self.results:
            summarised["ordering"] = self.ordering.summary()
        if self.shown is not None and self.results:
            summarised["presentation"] = self.shown.summary()
        summarised["results"] = self.results
        return summarised

    def grounding(self) -> dict[str, Any]:
        if not self.ran:
            return {"answerFrom": self.answer_from}
        rows = [{"count": self.count}] if self.count is not None else self.results
        grounded: dict[str, Any] = {"answerFrom": self.answer_from, "results": rows}
        if self.ordering is not None and rows:
            grounded["ordering"] = self.ordering.summary()
        if self.shown is not None and rows:
            grounded["presentation"] = self.shown.mode.value
        if rows:
            grounded["showing"] = len(rows)
        if self.found is not None:
            grounded["outOf"] = self.found
        if not rows and self.looked_for:
            narrowed = bool(self.looked_for.get("filters"))
            grounded["matchedNothing" if narrowed else "foundNoneOf"] = self.looked_for
        return grounded
