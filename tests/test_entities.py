from __future__ import annotations

import pytest

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
