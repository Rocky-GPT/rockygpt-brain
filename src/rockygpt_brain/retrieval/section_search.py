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

from collections.abc import Collection, Sequence
from datetime import date

from rockygpt_brain.retrieval.meaning import MeaningIndex, MeaningScores

# About 20 whole sections, at most 2 from one page, fit one writer call with room for the question.
SECTIONS = 20
PER_PAGE = 2


def sections(index: MeaningIndex, vectors: Sequence[Sequence[float]], today: date, *,
             cap: int = SECTIONS, per_page: int = PER_PAGE,
             skip: Collection[tuple[str, str]] = ()) -> tuple[MeaningScores, list[tuple[str, str]]]:
    """The (record id, unit) keys of the sections read for a question's parts (one vector each,
    the whole question first): each part's best in turn, at most `per_page` from one page and
    `cap` in all, none in `skip` (read already); with each one's best score and match offset, as
    the one call's own read gives them (one_call.chosen_units)."""
    by_part = [_ranked(index, vector, today, skip) for vector in vectors]
    chosen: list[tuple[str, str]] = []
    best: dict[tuple[str, str], float] = {}
    at: dict[tuple[str, str], int] = {}
    for scores in by_part:
        for key in scores:
            best[key] = max(best.get(key, -9.0), scores[key])
            if key in scores.at:
                at.setdefault(key, scores.at[key])
    ranked = [list(scores) for scores in by_part]
    on_page: dict[str, int] = {}
    for n in range(max((len(r) for r in ranked), default=0)):
        if len(chosen) >= cap:
            break
        for keys in ranked:
            if len(chosen) >= cap:
                break
            if n >= len(keys):
                continue
            key = keys[n]
            if key in chosen or on_page.get(key[0], 0) >= per_page:
                continue
            on_page[key[0]] = on_page.get(key[0], 0) + 1
            chosen.append(key)
    found = MeaningScores({key: best[key] for key in chosen})
    found.at = {key: at[key] for key in chosen if key in at}
    return found, chosen


def _ranked(index: MeaningIndex, vector: Sequence[float], today: date,
            skip: Collection[tuple[str, str]]) -> MeaningScores:
    """One part's current plain sections, best first, with their scores and match offsets."""
    raw = index.scores(vector)
    kept = MeaningScores((key, score) for key, score in raw.items()
                         if not key[1].startswith("e") and key not in skip
                         and _current(index, key[0], today))
    kept.at = {key: raw.at[key] for key in kept if key in raw.at}
    return kept


def _current(index: MeaningIndex, record_id: str, today: date) -> bool:
    mark = index.pages.get(record_id)
    return mark is None or mark.current(today)
