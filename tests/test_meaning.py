import array
import math
from typing import Any

from rockygpt_brain.engine import meaning_query
from rockygpt_brain.provider import TurnBudget
from rockygpt_brain.retrieval.entity_facts import procedures_property
from rockygpt_brain.retrieval.meaning import MeaningIndex
from test_engine import (
    NOW,
    ScriptedGateway,
    advising_and_tutoring,
    answer,
    completion,
    finish,
)


def index_of(units: list[tuple[str, str, list[str], list[list[float]]]],
             names: dict[str, str] | None = None) -> MeaningIndex:
    """A small in-memory index: each unit (record, index, offices) with its vectors."""
    flat, owner = array.array("f"), []
    for n, (_, _, _, vectors) in enumerate(units):
        for vector in vectors:
            norm = math.sqrt(sum(x * x for x in vector))
            flat.extend(x / norm for x in vector)
            owner.append(n)
    meta = {"dataset_version": "release-1", "identity_hash": "identities-1", "model": "m",
            "dimensions": len(units[0][3][0]), "entities": names or {},
            "units": [[r, i, e] for r, i, e, _ in units], "owner": owner}
    return MeaningIndex(meta, flat)


def test_a_unit_scores_its_best_vector_and_offices_count_by_their_best_unit() -> None:
    index = index_of([("page-a", "0", ["advising"], [[1, 0, 0]]),
                      ("page-b", "e0", ["tutoring"], [[0, 1, 0], [0.9, 0.1, 0]]),
                      ("page-c", "0", ["library"], [[0, 0, 1]])],
                     {"advising": "Academic Advising", "tutoring": "Center for Reading and Writing",
                      "library": "Library"})
    scores = index.scores([1, 0, 0])
    # page-b's written question is near the query, though its own text is not.
    assert list(scores)[:2] == [("page-a", "0"), ("page-b", "e0")]
    assert scores[("page-b", "e0")] > 0.9
    # The asked office is never another office; the next best are named in order.
    assert index.offices(scores, exclude=["advising"], limit=2) == ["tutoring", "library"]


def test_by_meaning_a_section_sharing_no_word_with_the_question_is_offered() -> None:
    page = {"id": "page-1", "source_key": "office-pages", "source_record_key": "r",
            "url": "https://www.ramapo.edu/oss/attendance/", "collected_at": NOW,
            "freshness_sla_hours": 168, "content_hash": "h",
            "sections": [{"heading": "Class attendance",
                          "text": "Course attendance is determined by the instructor."},
                         {"heading": "Parking", "text": "Park in lot C with a permit."}]}
    words, _ = procedures_property([page], NOW, "excused for a funeral")
    assert words["status"] == "unknown"  # No asked word is on the page.
    meaning, _ = procedures_property([page], NOW, "excused for a funeral",
                                     {("page-1", "0"): 0.61, ("page-1", "1"): 0.12})
    assert [v["value"]["heading"] for v in meaning["values"]] == ["Class attendance", "Parking"]


class EmbeddingGateway(ScriptedGateway):
    def __init__(self, *steps: Any, vector: list[float]) -> None:
        super().__init__(*steps)
        self.vector, self.embedded = vector, []

    async def embed(self, text: str, *, dimensions: int, budget: TurnBudget) -> list[float]:
        self.embedded.append(text)
        return self.vector


class MeaningFacts(type(advising_and_tutoring())):  # type: ignore[misc]
    index: MeaningIndex | None = None

    def meaning_index(self) -> MeaningIndex | None:
        return self.index


def meaning_service() -> Any:
    service = advising_and_tutoring()
    service.__class__ = MeaningFacts
    service.index = index_of(
        [("page-advising", "0", ["advising"], [[0, 1, 0]]),
         ("page-advising", "1", ["advising"], [[0, 0.8, 0.2]]),
         ("page-tutoring", "0", ["tutoring"], [[1, 0, 0]]),
         ("page-library", "0", ["library"], [[0, 0, 1]])],
        {"advising": "Academic Advising", "tutoring": "Center for Reading and Writing",
         "library": "Library"})
    return service


def test_the_question_is_searched_by_meaning_across_offices_when_an_index_is_published() -> None:
    # The question shares no word with the tutoring page; its meaning is closest to it.
    ask = completion("graph_lookup", {"requests": [
        {"query": "Academic Advising", "fields": ["procedures"],
         "about": "help other kids with their essays"}]})
    pick = completion("pick_sections", {"picks": [{"lookup": 0, "sections": []},
                                                  {"lookup": 1, "sections": [0]}]})
    gateway = EmbeddingGateway(ask, pick, finish(), vector=[1, 0, 0])
    result = answer(gateway, service=meaning_service(),
                    messages=[{"role": "user", "content": "can i help other kids with essays"}])
    assert "March 1" in result.body["answer"]
    assert gateway.embedded == ["can i help other kids with essays\n"
                                "help other kids with their essays"]
    assert result.trace is not None and result.trace["procedureSearch"] == "meaning"
    (other,) = [e for e in result.trace["lookups"] if e.get("foundBy") == "other_office_pages"]
    assert other["office"] == "Center for Reading and Writing"


def test_without_an_embedding_how_to_sections_are_found_by_the_asked_words() -> None:
    ask = completion("graph_lookup", {"requests": [
        {"query": "Academic Advising", "fields": ["procedures"], "about": "become a peer tutor"}]})
    pick = completion("pick_sections", {"picks": [{"lookup": 0, "sections": []},
                                                  {"lookup": 1, "sections": [0]}]})
    gateway = ScriptedGateway(ask, pick, finish())  # No embed: the gateway cannot embed.
    result = answer(gateway, service=meaning_service(),
                    messages=[{"role": "user", "content": "how do i become a peer tutor"}])
    assert "March 1" in result.body["answer"]
    assert result.trace is not None and "procedureSearch" not in result.trace


def test_a_follow_up_is_searched_with_what_the_student_wants() -> None:
    asked = meaning_query(" and for grad students? ",
                          ["apply for graduation", "apply for graduation"])
    assert asked == "and for grad students?\napply for graduation"


def test_an_entry_carries_its_own_section_for_what_its_steps_leave_out() -> None:
    policy = ("Students are reviewed annually. Graduate students must have a cumulative GPA of "
              "3.0. Undergraduate students must have a cumulative GPA of at least 2.0.")
    page = {"id": "page-1", "source_key": "office-pages", "source_record_key": "r",
            "url": "https://www.ramapo.edu/finaid/policies/", "collected_at": NOW,
            "freshness_sla_hours": 168, "content_hash": "h",
            "sections": [{"heading": "Other", "text": "Unrelated words. " * 200},
                         {"heading": "Satisfactory Academic Progress", "text": policy}],
            "howto_entries": [{"task": "Keep satisfactory academic progress for aid",
                               "section": "Satisfactory Academic Progress",
                               "steps": [{"text": "Graduate students need a 3.0 GPA.",
                                          "quote": "Graduate students must have a cumulative "
                                                   "GPA of 3.0"}]}]}
    prop, _ = procedures_property([page], NOW, "satisfactory academic progress gpa for aid")
    value = prop["values"][0]["value"]
    assert value["text"] == ("1. Graduate students need a 3.0 GPA. (page: \u201cGraduate students "
                             "must have a cumulative GPA of 3.0\u201d)")
    assert "Undergraduate students must have a cumulative GPA of at least 2.0." in (
        value["section_words"])


def test_a_long_section_is_said_from_the_piece_that_matched_its_meaning() -> None:
    listing = "Prohibited items: " + "candles, " * 150 + "Animals of any kind except fish."
    at = listing.index("Animals")
    flat = array.array("f", [1.0, 0.0, 0.0, 1.0])  # The opening, then the piece at `at`.
    index = MeaningIndex({"dataset_version": "release-1", "identity_hash": "identities-1",
                          "model": "m", "dimensions": 2, "entities": {},
                          "units": [["page-1", "0", ["housing"]]], "owner": [0, 0],
                          "offsets": [0, at]}, flat)
    scores = index.scores([0.0, 1.0])
    assert scores.at == {("page-1", "0"): at}
    page = {"id": "page-1", "source_key": "office-pages", "source_record_key": "r",
            "url": "https://www.ramapo.edu/reslife/prohibited-items/", "collected_at": NOW,
            "freshness_sla_hours": 168, "content_hash": "h",
            "sections": [{"heading": "Prohibited items", "text": listing}]}
    prop, _ = procedures_property([page], NOW, "can my kid keep a cat in the dorm", scores)
    value = prop["values"][0]["value"]
    # Said from a little before the piece that matched, so a rule keeps what comes before it.
    assert "Animals of any kind" in value["text"] and value["text_cut"]
    assert len(value["text"].split("Animals")[0]) <= 200



def test_an_entrys_section_is_said_from_where_its_meaning_matched() -> None:
    listing = "Prohibited items: " + "candles, " * 600 + "Animals of any kind except fish."
    at = listing.index("Animals")
    page = {"id": "page-1", "source_key": "office-pages", "source_record_key": "r",
            "url": "https://www.ramapo.edu/reslife/prohibited-items/", "collected_at": NOW,
            "freshness_sla_hours": 168, "content_hash": "h",
            "sections": [{"heading": "Prohibited items", "text": listing}],
            "howto_entries": [{"task": "Avoid prohibited items in campus housing",
                               "section": "Prohibited items",
                               "steps": [{"text": "Leave prohibited items at home.",
                                          "quote": "Prohibited items"}]}]}
    # The entry, the section's opening, then the section's piece at `at`, which matches best.
    flat = array.array("f", [1.0, 0.0, 1.0, 0.0, 0.0, 1.0])
    index = MeaningIndex({"dataset_version": "release-1", "identity_hash": "identities-1",
                          "model": "m", "dimensions": 2, "entities": {},
                          "units": [["page-1", "e0", ["housing"]], ["page-1", "0", ["housing"]]],
                          "owner": [0, 1, 1], "offsets": [0, 0, at]}, flat)
    prop, _ = procedures_property([page], NOW, "keep a cat in the dorm", index.scores([0.0, 1.0]))
    entry = next(v["value"] for v in prop["values"] if v["value"]["heading"].startswith("Avoid"))
    assert "Animals of any kind except fish." in entry["section_words"]


def test_a_long_text_is_cut_between_words_never_inside_a_link() -> None:
    from rockygpt_brain.retrieval.entity_facts import _cut
    text = ("Log into RCNJ Experience (https://experience.elluciancloud.com/rconj/) and open "
            "the card.")
    said = _cut(text, 0, 40)
    assert said == "Log into RCNJ Experience"
    # A start inside a word moves to the next word.
    assert _cut(text, 2, 200).startswith("into RCNJ")
    assert _cut(text, 0, 500) == text


def test_a_page_whose_address_names_only_ended_terms_or_years_is_past() -> None:
    from datetime import date

    from rockygpt_brain.retrieval.entity_facts import past_page
    today = date(2026, 10, 20)
    base = "https://www.ramapo.edu/student-accounts/"
    assert past_page(base + "previous-deadlines/previous-deadlines-2021/", today) == "2021"
    assert past_page("https://www.ramapo.edu/student-affairs/wellness-services-fall-2023/",
                     today) == "Fall 2023"
    # A year whose academic year is not over, or no year at all, is not past.
    assert past_page("https://www.ramapo.edu/x/support-beyond-the-classroom-april-2026/",
                     today) is None
    assert past_page("https://www.ramapo.edu/x/catalog-2025-2026/", today) is None
    assert past_page(base + "future-deadlines/", today) is None


def test_a_section_heading_naming_only_an_old_year_is_past() -> None:
    from datetime import date

    from rockygpt_brain.retrieval.entity_facts import past_heading
    today = date(2026, 10, 20)
    assert past_heading("April 1-30, 2014", today) == "2014"
    assert past_heading("Make an appointment", today) is None
    assert past_heading("Fall 2025 deadlines", today) is None
