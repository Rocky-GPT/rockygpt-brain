"""Every office and dining venue in a release graph, said in plain sentences (no model calls).

  python scripts/check_plain_writer.py <graph-dir>

For each office and venue the release publishes, each lookup a student's question makes (its
hours, today's hours, how to reach it, everything at once, a venue's menu) is read the way the
lookup tool reads it, and its writer view is given to the plain writer. A view the plain writer
can't say goes to the student as code's evidence text instead, with notes like "captured
2026-10-23T14:20" (11-03: the gym's hours). This lists every lookup that would, and exits 1 when
there is one, so a new kind of card is caught before a release, not by a student.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from rockygpt_brain.engine import ChatEngine, OfficeRequest, answered
from rockygpt_brain.plain_writer import plain_text
from rockygpt_brain.provider import GatewayError
from rockygpt_brain.retrieval import EntityFacts
from rockygpt_brain.retrieval.campus_graph import CampusGraph
from rockygpt_brain.turn import CAMPUS_TZ, Turn
from rockygpt_brain.writer import says_something

# The lookups a student's question makes of one office, by the fields the lookup tool asks for.
SHAPES: tuple[tuple[tuple[str, ...], str | None], ...] = (
    (("hours",), None),
    (("hours",), "today"),
    (("phones", "email", "offices"), None),
    (("hours", "phones", "email", "offices", "website", "contact_note"), None),
)
MENU_SHAPE: tuple[tuple[str, ...], str | None] = (("menu",), "today")


class NoModel:
    """A gateway for a check that must never call a model."""

    async def complete(self, **_: Any) -> Any:
        raise AssertionError("the plain-writer check calls no model")

    async def ready(self) -> bool:
        return True


def unsayable(facts: EntityFacts, now: datetime) -> list[dict[str, Any]]:
    """Every lookup whose writer view the plain writer can't say, with what was looked up."""
    engine = ChatEngine(NoModel(), facts, output="written")
    turn = Turn("plain-writer check", "check", "check", now)
    places: list[tuple[str, Sequence[tuple[tuple[str, ...], str | None]]]] = [
        (str(o["name"]), SHAPES) for o in facts.list_offices(limit=500)["offices"]]
    for venue in engine._published_venues() or []:
        shapes: Sequence[tuple[tuple[str, ...], str | None]] = [
            *SHAPES, *([MENU_SHAPE] if "menu" in (venue.get("publishes") or []) else [])]
        places.append((str(venue["name"]), shapes))
    found: list[dict[str, Any]] = []
    for name, shapes in places:
        for fields, day in shapes:
            asked = {"office": name, "fields": list(fields), "day": day}
            request = OfficeRequest.model_validate({"query": name, "fields": list(fields),
                                                    **({"day": day} if day else {})})
            try:
                result = engine._lookup(request, CampusGraph(facts), now, exact_name_only=True)
            except Exception as error:  # noqa: BLE001 - every failure is reported, none stops it
                found.append({**asked, "why": f"lookup failed: {type(error).__name__}"})
                continue
            if result.part is None:
                continue  # No office by that name: the lookup's own text says so.
            try:
                reply = engine._json(answered(turn, result.rendered.text, "answered",
                                              result.rendered.citations), [result.part], turn)
            except GatewayError as error:
                found.append({**asked, "why": f"no writer view: {error.code}"})
                continue
            view = reply.body.get("writerInput")
            if isinstance(view, dict) and not says_something(view):
                continue  # Nothing to write: code's fixed wording is the reply.
            if not isinstance(view, dict) or plain_text(view) is None:
                found.append({**asked, "why": "the plain writer can't say it"})
    return found


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("graph", type=Path, help="A release graph directory (active.json).")
    args = parser.parse_args(argv)
    from rockygpt_brain.retrieval.graph_store import GraphOnlyFacts

    found = unsayable(GraphOnlyFacts(args.graph), datetime.now(CAMPUS_TZ).replace(microsecond=0))
    for row in found:
        print(f"{row['office']} {row['fields']} day={row['day']}: {row['why']}")
    print(f"{len(found)} lookups would be said as evidence text "
          f"({dict(Counter(row['why'] for row in found))})")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
