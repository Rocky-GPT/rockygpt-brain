"""Whether a schedule's own season label can include a date.

An office's page prints schedules under labels such as "Summer", "Fall/Spring" or "Academic Year"
and often no dates. Such a schedule is never *established* for a date (the data policy: an undated
source stays undated; generic class dates do not date office hours), so it is listed with the note
that its dates are not published. That note is the right one for the schedule that could apply. It
is noise for a schedule whose label names a season the date is plainly not in: a Summer schedule
beside an October date.

This module only says "plainly not in". It never says a schedule applies, and it answers "unknown"
for the months where seasons meet (a Summer label in late August) and for any label it does not
recognize, so the only effect it can have is to leave out a schedule that cannot be the answer.
"""

import re
from datetime import date
from typing import Literal

Fit = Literal["in", "out", "unknown"]

# Months a season label covers outright, and months where it may or may not (the seasons meet).
_IN: dict[str, frozenset[int]] = {
    "summer": frozenset({6, 7}),
    "fall": frozenset({9, 10, 11}),
    "spring": frozenset({2, 3, 4}),
    "winter": frozenset(),
}
_MAYBE: dict[str, frozenset[int]] = {
    "summer": frozenset({5, 8}),
    "fall": frozenset({8, 12}),
    "spring": frozenset({1, 5}),
    "winter": frozenset({12, 1, 2}),
}
# A label that names a school year or a semester without a season ("Academic Year", "During the
# semester", "late August to early May") stands for fall and spring together.
_YEAR_LONG = re.compile(r"\bacademic\s+year\b|\bsemesters?\b|\blate august\b.*\bearly may\b", re.I)
_WORD = {
    "summer": re.compile(r"\bsummer\b", re.I),
    "fall": re.compile(r"\b(?:fall|autumn)\b", re.I),
    "spring": re.compile(r"\bspring\b", re.I),
    "winter": re.compile(r"\bwinter\b", re.I),
}
_YEAR = re.compile(r"\b(20\d{2})\b")


def season_fit(label: str | None, day: date) -> Fit:
    """`out` only when every season the label names is plainly not the one `day` is in."""
    if not isinstance(label, str) or not label.strip():
        return "unknown"
    named = [season for season, word in _WORD.items() if word.search(label)]
    if not named and _YEAR_LONG.search(label):
        named = ["fall", "spring"]
    if not named:
        return "unknown"
    # A fall, spring or summer label that carries a different year is another year's schedule.
    year = _YEAR.search(label)
    if year and int(year.group(1)) != day.year and "winter" not in named:
        return "out"
    inside = set().union(*(_IN[season] for season in named))
    near = set().union(*(_MAYBE[season] for season in named))
    if day.month in inside:
        return "in"
    return "unknown" if day.month in near else "out"
