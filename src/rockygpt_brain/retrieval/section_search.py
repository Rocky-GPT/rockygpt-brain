"""The sections a page answer reads (plan "one simple page writer", step 2): whole plain sections
by meaning, never a page marked not current.

Today's read mixes a page's task entries with its sections, and 20 entries from other pages can push
the one section that answers out of the read (11-16: the Counseling Center's own section ranked 1st
among sections and 21st among all units). It also reads old copies of a page (the 2017-18 to
2024-25 cost pages above today's). This search reads sections only and leaves out pages the index
marks not current (page_marks). No model call is made: the parts' vectors come from the caller.

On the free check of 11-16 (dev release v2-20261111224857) it held the graders' page for 17 of 19
older how-to misses (today's read: 13) and 30 of 30 good answers (29). Lifting the leading office's
own pages to break near ties was tried and dropped: by 0.02 it held 16 of the 19, by 0.04 15.
"""

from collections.abc import Sequence
from datetime import date

from rockygpt_brain.retrieval.meaning import MeaningIndex

# About 20 whole sections, at most 2 from one page, fit one writer call with room for the question.
SECTIONS = 20
PER_PAGE = 2


def sections(index: MeaningIndex, vectors: Sequence[Sequence[float]], today: date, *,
             cap: int = SECTIONS, per_page: int = PER_PAGE) -> list[tuple[str, str]]:
    """The (record id, unit) keys of the sections read for a question's parts (one vector each,
    the whole question first): each part's best in turn, at most `per_page` from one page and
    `cap` in all."""
    by_part = [_ranked(index, vector, today) for vector in vectors]
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


def _ranked(index: MeaningIndex, vector: Sequence[float], today: date) -> list[tuple[str, str]]:
    """One part's current plain sections, best first."""
    return [key for key in index.scores(vector)
            if not key[1].startswith("e") and _current(index, key[0], today)]


def _current(index: MeaningIndex, record_id: str, today: date) -> bool:
    mark = index.pages.get(record_id)
    return mark is None or mark.current(today)
