"""The search a page answer reads from (plan "one simple page writer", step 2): whole plain sections
by meaning, never a page marked not current."""

import array
import math
from collections.abc import Mapping, Sequence
from datetime import date

from rockygpt_brain.retrieval.meaning import MeaningIndex, MeaningScores
from rockygpt_brain.retrieval.section_search import sections

TODAY = date(2026, 11, 16)


def index_of(units: list[tuple[str, str, float]],
             pages: Mapping[str, Sequence[object]] | None = None) -> MeaningIndex:
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
    assert sections(index, [QUERY], TODAY)[1] == [("counseling", "0"), ("career", "1")]


def test_a_page_marked_not_current_is_left_out_and_an_unmarked_page_is_kept() -> None:
    index = index_of([("cost-2024", "0", 0.95), ("cost-now", "0", 0.90), ("unmarked", "0", 0.85)],
                     pages={"cost-2024": ["bursar", "2026-04-21", "2025-08-31", False],
                            "cost-now": ["bursar", "2026-04-21", None, False]})
    assert sections(index, [QUERY], TODAY)[1] == [("cost-now", "0"), ("unmarked", "0")]


def test_at_most_two_sections_of_one_page_and_at_most_the_cap() -> None:
    index = index_of([("a", str(n), 0.99 - n / 100) for n in range(5)]
                     + [(f"p{n}", "0", 0.90 - n / 100) for n in range(30)])
    found = sections(index, [QUERY], TODAY)[1]
    assert found[:2] == [("a", "0"), ("a", "1")]
    assert ("a", "2") not in found and len(found) == 20
    assert sections(index, [QUERY], TODAY, cap=5)[1] == found[:5]


def test_each_part_of_the_question_keeps_its_own_best_sections() -> None:
    # The second part asks something only one page speaks to; the whole question's best do not.
    index = index_of([(f"p{n}", "0", 0.95 - n / 100) for n in range(25)] + [("parking", "0", 0.10)])
    other = [0.0, 1.0]  # A part whose best section is the one the whole question scores lowest.
    found = sections(index, [QUERY, other], TODAY)[1]
    assert found[:2] == [("p0", "0"), ("parking", "0")] and len(found) == 20


def test_sections_read_already_are_skipped_and_each_keeps_its_score() -> None:
    index = index_of([("a", "0", 0.9), ("b", "0", 0.8), ("c", "0", 0.7)])
    scores, keys = sections(index, [QUERY], TODAY, skip={("a", "0")})
    assert keys == [("b", "0"), ("c", "0")]
    assert round(scores[("b", "0")], 3) == 0.8



def test_a_section_is_said_from_its_start_unless_its_matched_piece_lies_past_what_is_said() -> None:
    # 11-16: the guest limit sits 541 characters into Guest Procedures; saying the section from
    # just before its matched piece (offset 1,000) cut it off.
    index = index_of([("guests", "0", 0.9), ("long", "0", 0.8)])
    raw = index.scores
    def scores(query: Sequence[float]) -> MeaningScores:
        found = raw(query)
        found.at = {("guests", "0"): 1_000, ("long", "0"): 2_500}
        return found
    index.scores = scores  # type: ignore[method-assign]
    found, _ = sections(index, [QUERY], TODAY)
    assert found.at == {("long", "0"): 2_500}


def test_a_page_read_in_part_also_gives_its_short_opening_first() -> None:
    # 11-16: ResNET's registration page was read from its later sections; its opening says the
    # page is only for devices that cannot log in.
    index = index_of([("wifi", "0", 0.95), ("register", "1", 0.90), ("register", "2", 0.85),
                      ("register", "0", 0.10), ("other", "0", 0.05)])
    keys = sections(index, [QUERY], TODAY, cap=3)[1]
    assert keys == [("wifi", "0"), ("register", "0"), ("register", "1"), ("register", "2")]
    # An opening read already is not given again.
    assert ("register", "0") not in sections(index, [QUERY], TODAY, cap=3,
                                             skip={("register", "0")})[1]
