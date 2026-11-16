"""The search a page answer reads from (plan "one simple page writer", step 2): whole plain sections
by meaning, never a page marked not current, the leading office's own pages first."""

import array
import math
from datetime import date

from rockygpt_brain.retrieval.meaning import MeaningIndex
from rockygpt_brain.retrieval.section_search import sections

TODAY = date(2026, 11, 16)


def index_of(units: list[tuple[str, str, float]],
             pages: dict[str, list[object]] | None = None) -> MeaningIndex:
    """An index whose unit k scores `score` against the query (1, 0), its vector at that cosine."""
    flat = array.array("f")
    for _, _, score in units:
        flat.extend([score, math.sqrt(max(0.0, 1 - score * score))])
    meta = {"dataset_version": "v", "identity_hash": "h", "model": "m", "dimensions": 2,
            "units": [[rid, key, ["office"]] for rid, key, _ in units],
            "owner": list(range(len(units))),
            "labels": [[f"ramapo.edu/{rid}", f"Heading {key}"] for rid, key, _ in units],
            "pages": pages or {}}
    return MeaningIndex(meta, flat)


QUERY = [1.0, 0.0]


def test_only_plain_sections_are_read_never_task_entries() -> None:
    # 11-16: on the counseling miss, 20 task entries from other pages ranked above the Counseling
    # Center's own section, so the read never held it.
    index = index_of([("other", "e0", 0.95), ("other", "e1", 0.94), ("counseling", "0", 0.90),
                      ("career", "1", 0.80)])
    assert sections(index, [QUERY], TODAY) == [("counseling", "0"), ("career", "1")]


def test_a_page_marked_not_current_is_left_out_and_an_unmarked_page_is_kept() -> None:
    index = index_of([("cost-2024", "0", 0.95), ("cost-now", "0", 0.90), ("unmarked", "0", 0.85)],
                     pages={"cost-2024": ["bursar", "2026-04-21", "2025-08-31", False],
                            "cost-now": ["bursar", "2026-04-21", None, False]})
    assert sections(index, [QUERY], TODAY) == [("cost-now", "0"), ("unmarked", "0")]


def test_at_most_two_sections_of_one_page_and_at_most_the_cap() -> None:
    index = index_of([("a", str(n), 0.99 - n / 100) for n in range(5)]
                     + [(f"p{n}", "0", 0.90 - n / 100) for n in range(30)])
    found = sections(index, [QUERY], TODAY)
    assert found[:2] == [("a", "0"), ("a", "1")]
    assert ("a", "2") not in found and len(found) == 20
    assert sections(index, [QUERY], TODAY, cap=5) == found[:5]


def test_each_part_of_the_question_keeps_its_own_best_sections() -> None:
    # The second part asks something only one page speaks to; the whole question's best do not.
    index = index_of([(f"p{n}", "0", 0.95 - n / 100) for n in range(25)] + [("parking", "0", 0.10)])
    other = [0.0, 1.0]  # A part whose best section is the one the whole question scores lowest.
    found = sections(index, [QUERY, other], TODAY)
    assert found[:2] == [("p0", "0"), ("parking", "0")] and len(found) == 20


def test_the_leading_offices_pages_come_first_when_they_score_close_to_the_top() -> None:
    pages = {"housing-1": ["housing", None, None, False],
             "housing-2": ["housing", None, None, False],
             "dining": ["dining", None, None, False]}
    index = index_of([("housing-1", "0", 0.90), ("dining", "0", 0.89), ("housing-2", "0", 0.88)],
                     pages=pages)
    assert sections(index, [QUERY], TODAY, owner_bonus=0.0) == [
        ("housing-1", "0"), ("dining", "0"), ("housing-2", "0")]
    assert sections(index, [QUERY], TODAY, owner_bonus=0.02) == [
        ("housing-1", "0"), ("housing-2", "0"), ("dining", "0")]
    # Not when it is far behind: a bonus only breaks near ties.
    far = index_of([("housing-1", "0", 0.90), ("dining", "0", 0.89), ("housing-2", "0", 0.70)],
                   pages=pages)
    assert sections(far, [QUERY], TODAY, owner_bonus=0.02)[1] == ("dining", "0")
