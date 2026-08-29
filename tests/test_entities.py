from __future__ import annotations

from datetime import date

import pytest

from rockygpt_brain.capabilities.calendar.normalize import _bare_season, _term_for_season
from rockygpt_brain.capabilities.entities import (
    EntityCandidate,
    EntityResolutionFailed,
    resolve_entity,
)

SUBJECTS = [
    EntityCandidate("CMPS", "Computer Science", ("CS", "Comp Sci")),
    EntityCandidate("CNST", "Contemplative Studies"),
    EntityCandidate("MATH", "Mathematics"),
    EntityCandidate("INFO", "Info Systems"),
]


def resolve(mention: str, candidates: list[EntityCandidate] | None = None) -> str:
    return resolve_entity("course_subject", mention, candidates or SUBJECTS)


def test_a_canonical_code_resolves_to_itself() -> None:
    assert resolve("CMPS") == "CMPS"
    assert resolve("cmps") == "CMPS"


def test_a_canonical_name_resolves() -> None:
    assert resolve("Computer Science") == "CMPS"
    assert resolve("computer  science") == "CMPS"


def test_an_alias_the_data_owns_resolves() -> None:
    """`CS` is Computer Science because the dataset says so, not because it is bigger."""
    assert resolve("CS") == "CMPS"
    assert resolve("comp sci") == "CMPS"


def test_a_unique_abbreviation_resolves_without_an_alias() -> None:
    assert resolve("MATH") == "MATH"
    assert resolve("IS", [EntityCandidate("INFO", "Info Systems")]) == "INFO"


def test_an_alias_outranks_an_abbreviation_another_entity_claims() -> None:
    """Both entities abbreviate to CS; the alias decides before initials are tried."""
    assert resolve("CS") == "CMPS"


def test_an_abbreviation_two_entities_claim_is_refused() -> None:
    contested = [
        EntityCandidate("CMPS", "Computer Science"),
        EntityCandidate("CNST", "Contemplative Studies"),
    ]
    with pytest.raises(EntityResolutionFailed) as raised:
        resolve("CS", contested)
    assert "CMPS" in str(raised.value) and "CNST" in str(raised.value)


def test_row_counts_never_break_a_tie() -> None:
    """The popular reading of an abbreviation is a guess wearing a statistic."""
    contested = [
        EntityCandidate("CMPS", "Computer Science"),
        EntityCandidate("CNST", "Contemplative Studies"),
    ]
    with pytest.raises(EntityResolutionFailed):
        resolve("CS", contested + [EntityCandidate("CMPS", "Computer Science")] * 60)


def test_a_mention_matching_nothing_is_refused() -> None:
    with pytest.raises(EntityResolutionFailed) as raised:
        resolve("underwater basket weaving")
    assert "matches nothing" in str(raised.value)


def test_an_empty_mention_is_refused() -> None:
    with pytest.raises(EntityResolutionFailed):
        resolve("   ")


def test_a_stronger_step_is_never_overruled_by_a_weaker_one() -> None:
    """An exact code wins over another entity that has it as an alias."""
    tangled = [
        EntityCandidate("MATH", "Mathematics"),
        EntityCandidate("STAT", "Statistics", ("MATH",)),
    ]
    assert resolve("MATH", tangled) == "MATH"


def test_minor_words_are_left_out_of_an_abbreviation() -> None:
    assert resolve("LS", [EntityCandidate("LAWS", "Law and Society")]) == "LAWS"


# --- A term named by season, with no year -------------------------------------


def _calendar(rows: list[tuple[str, str, str]]) -> list[dict[str, str]]:
    return [{"termId": t, "term": label, "date": day} for t, label, day in rows]


TERMS = _calendar(
    [
        ("fall-2026", "Fall 2026", "2026-08-21"),
        ("fall-2026", "Fall 2026", "2026-12-21"),
        ("spring-2027", "Spring 2027", "2027-01-18"),
        ("spring-2027", "Spring 2027", "2027-05-14"),
        ("fall-2027", "Fall 2027", "2027-08-25"),
        ("fall-2027", "Fall 2027", "2027-12-14"),
    ]
)


def test_a_season_with_a_year_is_left_to_the_strict_resolver() -> None:
    """An exact name is exact. Only a mention with no year needs the clock."""
    assert _bare_season("Fall 2026") is None
    assert _bare_season("fall 2027") is None


def test_a_season_alone_names_a_season() -> None:
    for mention in ("fall", "fall semester", "the fall term", "this academic year spring"):
        assert _bare_season(mention) is not None, mention
    assert _bare_season("fall semester") == "fall"


def test_a_phrase_that_merely_contains_a_season_is_not_a_term() -> None:
    """`fall break` is an event in a term, not the term. Refusing here leaves
    the strict resolver to say so rather than silently answering about Fall."""
    assert _bare_season("fall break") is None
    assert _bare_season("fall and spring") is None
    assert _bare_season("orientation") is None


def test_a_bare_season_resolves_to_the_term_being_lived_through() -> None:
    assert _term_for_season("fall", TERMS, date(2026, 9, 1)) == "fall-2026"
    assert _term_for_season("fall", TERMS, date(2026, 12, 1)) == "fall-2026"


def test_a_finished_season_resolves_to_the_next_one() -> None:
    """Asked in spring, "the fall semester" is the fall still to come."""
    assert _term_for_season("fall", TERMS, date(2027, 3, 1)) == "fall-2027"
    assert _term_for_season("spring", TERMS, date(2027, 9, 1)) == "spring-2027"


def test_every_instance_finished_resolves_to_the_most_recent() -> None:
    """Not the earliest, which would name a term years gone."""
    assert _term_for_season("fall", TERMS, date(2030, 1, 1)) == "fall-2027"


def test_a_season_the_calendar_does_not_carry_resolves_to_nothing() -> None:
    assert _term_for_season("summer", TERMS, date(2026, 9, 1)) is None
