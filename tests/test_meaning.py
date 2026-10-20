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
