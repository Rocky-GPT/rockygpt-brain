"""The sections a page answer reads (plan "one simple page writer", step 2): whole plain sections
by meaning, never a page marked not current, the leading office's own pages first.

Today's read mixes a page's task entries with its sections, and 20 entries from other pages can push
the one section that answers out of the read (11-16: the Counseling Center's own section ranked 1st
among sections and 21st among all units). It also reads old copies of a page (the 2017-18 to
2024-25 cost pages above today's). This search reads sections only, leaves out pages the index
marks not current (page_marks), and lets the pages of the office that leads a part's results
break near ties in their favor. No model call is made: the parts' vectors come from the caller.
"""

from collections.abc import Sequence
from datetime import date

from rockygpt_brain.retrieval.meaning import MeaningIndex

# About 20 whole sections, at most 2 from one page, fit one writer call with room for the question.
SECTIONS = 20
PER_PAGE = 2
# A section on a page of the office leading a part's results gains this much: enough to pass a
# near tie (another office's section a point or two higher), never a clearly better section.
OWNER_BONUS = 0.02


def sections(index: MeaningIndex, vectors: Sequence[Sequence[float]], today: date, *,
             cap: int = SECTIONS, per_page: int = PER_PAGE,
             owner_bonus: float = OWNER_BONUS) -> list[tuple[str, str]]:
    """The (record id, unit) keys of the sections read for a question's parts (one vector each,
    the whole question first): each part's best in turn, at most `per_page` from one page and
    `cap` in all."""
    by_part = [_ranked(index, vector, today, owner_bonus) for vector in vectors]
    chosen: list[tuple[str, str]] = []
    on_page: dict[str, int] = {}
    for n in range(max((len(r) for r in by_part), default=0)):
        for ranked in by_part:
            if len(chosen) >= cap:
                return chosen
            if n >= len(ranked):
                continue
            key = ranked[n]
            if key in chosen or on_page.get(key[0], 0) >= per_page:
                continue
            on_page[key[0]] = on_page.get(key[0], 0) + 1
            chosen.append(key)
    return chosen


def _ranked(index: MeaningIndex, vector: Sequence[float], today: date,
            owner_bonus: float) -> list[tuple[str, str]]:
    """One part's current plain sections, best first, the leading office's pages lifted a little."""
    kept = [(key, score) for key, score in index.scores(vector).items()
            if not key[1].startswith("e") and _current(index, key[0], today)]
    if not kept or not owner_bonus:
        return [key for key, _ in kept]
    leader = _owner(index, kept[0][0][0])
    if leader is None:
        return [key for key, _ in kept]
    lifted = [(key, score + owner_bonus if _owner(index, key[0]) == leader else score)
              for key, score in kept]
    return [key for key, _ in sorted(lifted, key=lambda ks: -ks[1])]


def _current(index: MeaningIndex, record_id: str, today: date) -> bool:
    mark = index.pages.get(record_id)
    return mark is None or mark.current(today)


def _owner(index: MeaningIndex, record_id: str) -> str | None:
    mark = index.pages.get(record_id)
    return mark.owner if mark else None
