"""A schedule's season label only ever rules a date out when it plainly cannot include it."""

from datetime import date

import pytest

from rockygpt_brain.season import season_fit

# Every label the published hours carry (read from the 10-14 release), by month of 2026.
#            Jan  Feb  Mar  Apr  May  Jun  Jul  Aug  Sep  Oct  Nov  Dec
U, IN, OUT = "unknown", "in", "out"
LABELS = {
    "Summer":                   [OUT, OUT, OUT, OUT, U, IN, IN, U, OUT, OUT, OUT, OUT],
    "Summer 2026":              [OUT, OUT, OUT, OUT, U, IN, IN, U, OUT, OUT, OUT, OUT],
    "Fall/Spring":              [U, IN, IN, IN, U, OUT, OUT, U, IN, IN, IN, U],
    "Fall and Spring":          [U, IN, IN, IN, U, OUT, OUT, U, IN, IN, IN, U],
    "Fall and Spring Semester": [U, IN, IN, IN, U, OUT, OUT, U, IN, IN, IN, U],
    "Academic Year":            [U, IN, IN, IN, U, OUT, OUT, U, IN, IN, IN, U],
    "During the semester":      [U, IN, IN, IN, U, OUT, OUT, U, IN, IN, IN, U],
    "late August to early May": [U, IN, IN, IN, U, OUT, OUT, U, IN, IN, IN, U],
    "Fall 2026":                [OUT, OUT, OUT, OUT, OUT, OUT, OUT, U, IN, IN, IN, U],
    "2026 Fall Semester Hours": [OUT, OUT, OUT, OUT, OUT, OUT, OUT, U, IN, IN, IN, U],
}


@pytest.mark.parametrize("label", sorted(LABELS))
def test_each_published_label_by_month(label: str) -> None:
    seen = [season_fit(label, date(2026, month, 15)) for month in range(1, 13)]
    assert seen == LABELS[label], label


@pytest.mark.parametrize("label", [None, "", "  ", "Early arrival period", "Regular hours",
                                   "Office Hours:", "By appointment"])
def test_a_label_with_no_season_in_it_is_never_ruled_out(label: str | None) -> None:
    assert all(season_fit(label, date(2026, month, 15)) == "unknown" for month in range(1, 13))


def test_a_label_for_another_year_is_out_but_winter_may_cross_the_new_year() -> None:
    assert season_fit("Fall 2026", date(2027, 10, 15)) == "out"
    assert season_fit("Spring 2026", date(2027, 3, 15)) == "out"
    assert season_fit("Winter 2026", date(2027, 1, 15)) == "unknown"


def test_the_october_case_that_started_this() -> None:
    day = date(2026, 10, 14)
    assert season_fit("Summer", day) == "out"
    assert season_fit("Academic Year", day) == "in"
