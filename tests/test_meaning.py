import array
import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from rockygpt_brain.engine import meaning_query
from rockygpt_brain.provider import TurnBudget
from rockygpt_brain.retrieval import meaning
from rockygpt_brain.retrieval.entity_facts import procedures_property
from rockygpt_brain.retrieval.meaning import MeaningIndex, standout_bar
from test_engine import (
    NOW,
    ScriptedGateway,
    advising_and_tutoring,
    answer,
    completion,
    finish,
)


def index_of(units: list[tuple[str, str, list[str], list[list[float]]]],
             names: dict[str, str] | None = None,
             cards: Sequence[tuple[str, list[float]]] = (), labels: bool = False,
             academic: Sequence[tuple[str, str, list[float]]] = ()) -> MeaningIndex:
    """A small in-memory index: each unit (record, index, offices) with its vectors, then each
    office card (office, vector), then each program or course card (id, kind, vector)."""
    flat, owner = array.array("f"), []
    for n, (_, _, _, vectors) in enumerate(units):
        for vector in vectors:
            norm = math.sqrt(sum(x * x for x in vector))
            flat.extend(x / norm for x in vector)
            owner.append(n)
    for vector in [v for _, v in cards] + [v for _, _, v in academic]:
        norm = math.sqrt(sum(x * x for x in vector))
        flat.extend(x / norm for x in vector)
    meta = {"dataset_version": "release-1", "identity_hash": "identities-1", "model": "m",
            "dimensions": len(units[0][3][0]), "entities": names or {},
            "units": [[r, i, e] for r, i, e, _ in units], "owner": owner,
            "cards": [entity_id for entity_id, _ in cards],
            "academic": [[entity_id, kind] for entity_id, kind, _ in academic],
            "labels": [[r, f"heading {r} {i}"] for r, i, _, _ in units] if labels else []}
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


def test_an_office_is_found_by_its_card_and_units_are_scored_without_the_cards() -> None:
    index = index_of([("page-a", "0", ["advising"], [[1, 0, 0]])], {"advising": "Advising"},
                     cards=[("athletics", [0, 1, 0]), ("advising", [0.9, 0.1, 0])])
    assert [e for e, _ in index.office_scores([0.1, 1, 0])] == ["athletics", "advising"]
    assert list(index.scores([0.1, 1, 0])) == [("page-a", "0")]
    # An index built before office cards has none.
    assert index_of([("page-a", "0", ["advising"], [[1, 0, 0]])]).office_scores([1, 0, 0]) == []


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
        self.vector, self.embedded = vector, list[str]()

    async def embed(self, text: str, *, dimensions: int, budget: TurnBudget) -> list[float]:
        self.embedded.append(text)
        return self.vector


class MeaningFacts(type(advising_and_tutoring())):  # type: ignore[misc]
    index: MeaningIndex | None = None

    def meaning_index(self) -> MeaningIndex | None:
        return self.index


def meaning_service() -> Any:
    service: Any = advising_and_tutoring()
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


def test_a_text_whose_every_deadline_date_has_passed_is_past() -> None:
    from datetime import date

    from rockygpt_brain.retrieval.entity_facts import past_dates
    today = date(2026, 10, 21)
    assert past_dates("1. Pay your Fall 2026 term bill by August 12, 2026.", today) == (
        "August 12, 2026")
    # A deadline still to come keeps the text current, beside one that passed.
    assert past_dates("100% refund: September 1, 2026. 7-week session refund: October 21, 2026",
                      today) is None
    assert past_dates("The Spring 2027 payment deadline is December 16, 2026.", today) is None
    # An effective or revision date is not a deadline.
    assert past_dates("Effective date: July 1, 2019. Revised March 3, 2021.", today) is None


def test_an_index_stored_in_8_bits_ranks_as_the_float_index_does() -> None:
    index = index_of([("page-a", "0", ["advising"], [[1, 0.2, 0]]),
                      ("page-b", "e0", ["tutoring"], [[0, 1, 0.1], [0.7, 0.3, 0]]),
                      ("page-c", "0", ["library"], [[0, 0.1, 1]])],
                     cards=[("athletics", [0, 1, 0])],
                     academic=[("biology", "program", [0.2, 0, 1])])
    vectors, scales = index.quantize()
    assert len(vectors) == 6 * 3 and len(scales) == 6 * 4
    meta = {"dataset_version": "release-1", "identity_hash": "identities-1", "model": "m",
            "dimensions": 3, "units": [["page-a", "0", ["advising"]],
                                       ["page-b", "e0", ["tutoring"]],
                                       ["page-c", "0", ["library"]]],
            "owner": [0, 1, 1, 2], "cards": ["athletics"], "academic": [["biology", "program"]]}
    stored = MeaningIndex.from_quantized(meta, vectors, scales)
    for query in ([1, 0, 0], [0, 1, 0], [0, 0, 1], [0.5, 0.5, 0.1]):
        assert list(stored.scores(query)) == list(index.scores(query))
        for (a, x), (b, y) in zip(stored.scores(query).items(), index.scores(query).items(),
                                  strict=True):
            assert a == b and abs(x - y) < 0.02
    assert stored.office_scores([0, 1, 0])[0][0] == "athletics"
    assert stored.academic_scores([0, 0, 1], "program")[0][0] == "biology"
    # Stored again, it is the same bytes.
    assert stored.quantize() == (vectors, scales)


def test_an_index_file_is_loaded_in_8_bits_as_the_database_stores_it(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vectors: list[list[float]] = [
        [1, 0.2, 0], [0.3, -0.9, 0.1],  # page-a
        [0, 1, 0.1], [0.7, 0.3, 0], [0, 0, 0],  # page-b, a vector of zeros included
        [0, 0.1, 1],  # page-c
        [0, 1, 0], [0.2, 0, 1]]  # the athletics card, the biology card
    flat = array.array("f")
    for vector in vectors:
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        flat.extend(x / norm for x in vector)
    meta = {"dataset_version": "release-1", "identity_hash": "identities-1", "model": "m",
            "dimensions": 3, "entities": {},
            "units": [["page-a", "0", ["advising"]], ["page-b", "e0", ["tutoring"]],
                      ["page-c", "0", ["library"]]],
            "owner": [0, 0, 1, 1, 1, 2], "cards": ["athletics"],
            "academic": [["biology", "program"]]}
    index = MeaningIndex(meta, flat)
    release = tmp_path / "release-1.lbug"
    release.with_suffix(".meaning.json").write_text(json.dumps(meta))
    release.with_suffix(".meaning.f32").write_bytes(flat.tobytes())  # As the build writes it.
    # Read two vectors at a time, so a chunk ends inside a unit's vectors.
    monkeypatch.setattr(meaning, "MATRIX_CHUNK", 2)
    loaded = MeaningIndex.load(release)
    assert loaded is not None
    # The same bytes the database stores (a vector of zeros is scaled by one).
    assert loaded.quantize() == index.quantize()
    for query in ([1, 0, 0], [0, 1, 0], [0, 0, 1], [0.5, 0.5, 0.1]):
        assert list(loaded.scores(query)) == list(index.scores(query))
    assert loaded.office_scores([0, 1, 0])[0][0] == "athletics"
    assert loaded.academic_scores([0, 0, 1], "program")[0][0] == "biology"
    # Without numpy, the floats are kept as they are.
    expected = dict(index.scores([0.5, 0.5, 0.1]))
    monkeypatch.setattr(meaning, "numpy", None)
    plain = MeaningIndex.load(release)
    assert plain is not None and plain.quantize() == index.quantize()
    assert dict(plain.scores([0.5, 0.5, 0.1])) == pytest.approx(expected)
    # A file cut inside a vector is not an index.
    monkeypatch.undo()
    release.with_suffix(".meaning.f32").write_bytes(flat.tobytes()[:-4])
    with pytest.raises(ValueError):
        MeaningIndex.load(release)


def test_a_card_stands_out_from_its_kind_by_the_questions_own_bar() -> None:
    # Each question's own bar: the median card plus three spreads. The same 0.47 stands out
    # among cards at 0.28-0.36 and is one of the crowd among cards at 0.38-0.49.
    quiet = [0.28 + (n % 9) * 0.01 for n in range(40)]
    busy = [0.38 + (n % 12) * 0.01 for n in range(40)]
    quiet_bar, busy_bar = standout_bar(quiet), standout_bar(busy)
    assert quiet_bar is not None and busy_bar is not None
    assert quiet_bar < 0.47 < busy_bar
    # A big group of matching cards (every Greek Life club for "frats and sororities") does not
    # lift the bar over itself, as a mean and standard deviation would.
    greek = [0.60 + (n % 3) * 0.01 for n in range(12)]
    rest = [0.36 + (n % 7) * 0.01 for n in range(48)]
    bar = standout_bar([*greek, *rest])
    assert bar is not None and bar < 0.60
    mean = sum([*greek, *rest]) / 60
    sd = (sum((x - mean) ** 2 for x in [*greek, *rest]) / 60) ** 0.5
    assert mean + 3 * sd > 0.60
    # Too few cards, or all alike, tell nothing.
    assert standout_bar([0.5] * 10) is None and standout_bar([0.5] * 40) is None


def test_how_to_entries_are_given_student_questions_until_the_build_spends_its_budget(
        monkeypatch: pytest.MonkeyPatch) -> None:
    import build_meaning_index as build

    asked: list[dict[str, Any]] = []

    def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
        sent = json.loads(body["input"][1]["content"])
        asked.append(sent)
        assert path == "responses" and body["reasoning"] == {"effort": "low"}
        assert body["text"]["format"]["schema"] == build.QUESTIONS_SCHEMA
        if "broken" in sent["page"]:
            raise RuntimeError("OpenAI responses failed with HTTP 500")
        items = [{"n": x["n"],
                  "questions": [f"how do i {x['task'].lower()}", " ", "b", "c", "d"]}
                 for x in sent["entries"]]
        return {"output": [{"type": "message", "content": [
                    {"type": "output_text", "text": json.dumps({"items": items})}]}],
                "usage": {"input_tokens": 1000, "input_tokens_details": {"cached_tokens": 200},
                          "output_tokens": 300}}

    monkeypatch.setattr(build, "_post", post)
    call = 800 * 125 + 200 * 10 + 300 * 500  # nUSD: uncached input, cached input, output.
    assert build.cost_nusd({"usage": {"input_tokens": 1000, "output_tokens": 300,
                                      "input_tokens_details": {"cached_tokens": 200}}}) == call
    # Only how-to entries are asked: 14 on one page (two calls), 1 on another. A page's other
    # sections get no questions (written for every section, they ranked near misses higher, 11-04).
    units = [{"page": "https://www.ramapo.edu/bursar/", "heading": f"Step {n}",
              "text": f"What step {n} says.", "entry": True} for n in range(14)]
    units.append({"page": "https://www.ramapo.edu/cardservices/", "heading": "Buy Ramapo Dollars",
                  "text": "Add money to your ID card online.", "entry": True})
    units.append({"page": "https://www.ramapo.edu/its/", "heading": "Need More Pages?",
                  "text": "Add funds through PaperCut.", "entry": False})
    cache: dict[str, list[str]] = {}
    saved: list[int] = []
    assert build.write_questions(units, cache, lambda: saved.append(len(cache))) == (15, 3 * call)
    assert sorted(len(x["entries"]) for x in asked) == [1, 2, 12]
    assert all("its" not in x["page"] for x in asked)
    first = next(x for x in asked if len(x["entries"]) == 12)
    assert first["page"] == "https://www.ramapo.edu/bursar/"
    assert first["entries"][0] == {"n": 0, "task": "Step 0", "steps": "What step 0 says."}
    # Three questions an entry, blank ones dropped; kept under the entry's own text.
    assert cache[build._digest("Buy Ramapo Dollars", "Add money to your ID card online.")] == [
        "how do i buy ramapo dollars", "b", "c"]
    assert build._digest("Need More Pages?", "Add funds through PaperCut.") not in cache
    assert saved == [15]
    # The next build asks for none of them again.
    assert build.write_questions(units, cache) == (0, 0) and len(asked) == 3

    # A build stops asking once its calls cost its budget; the entries left wait for the next
    # build. 16 calls are asked at once, so the first build asks 16 of 40 one-entry pages.
    asked.clear()
    pages = [{"page": f"https://www.ramapo.edu/page-{n}/", "heading": f"Heading {n}",
              "text": f"Text {n}.", "entry": True} for n in range(40)]
    assert build.write_questions(pages, cache, budget_nusd=1) == (16, 16 * call)
    assert len(asked) == 16
    assert build.write_questions(pages, cache) == (24, 24 * call) and len(asked) == 40
    # A call that fails costs nothing, and its entries are asked again by the next build.
    broken = [{"page": "https://www.ramapo.edu/broken/", "heading": "Hours",
               "text": "Open 9 to 5.", "entry": True}]
    assert build.write_questions(broken, cache) == (1, 0)
    assert build._digest("Hours", "Open 9 to 5.") not in cache
    assert build.write_questions(broken, cache) == (1, 0) and len(asked) == 42


def test_who_leads_an_office_reads_its_own_sections_whatever_fields_were_asked() -> None:
    # 11-04 blind run: "Who is the director of the Center for Health and Counseling Services?
    # Also what's the phone number" was read for name and phones only, and "Who is the provost
    # and what does the provost's office do?" for name, department and website: neither reply
    # could name anyone. The pick keeps none here, yet the best two by meaning are read.
    ask = completion("graph_lookup", {"requests": [
        {"query": "Academic Advising", "fields": ["email"]}]})
    pick = completion("pick_sections", {"picks": [{"lookup": 0, "sections": []}]})
    gateway = EmbeddingGateway(ask, pick, finish(), vector=[0, 1, 0])
    result = answer(gateway, service=meaning_service(), messages=[
        {"role": "user", "content": "who runs academic advising and what is their email"}])
    assert "advising@example.edu" in result.body["answer"]
    assert "Book an advising appointment." in result.body["answer"]
    assert "Become a peer mentor for first-years." in result.body["answer"]
    assert result.trace is not None and result.trace["procedureSearch"] == "meaning"
    # A plain contact question reads the fields asked, and no pages.
    gateway = EmbeddingGateway(ask, finish(), vector=[0, 1, 0])
    result = answer(gateway, service=meaning_service(), messages=[
        {"role": "user", "content": "what is academic advising's email"}])
    assert "advising@example.edu" in result.body["answer"]
    assert "Book an advising appointment." not in result.body["answer"]


def test_the_pick_adds_to_the_best_sections_by_meaning_and_never_drops_them() -> None:
    ask = completion("graph_lookup", {"requests": [
        {"query": "Academic Advising", "fields": ["procedures"], "about": "peer mentors"}]})
    pick = completion("pick_sections", {"picks": [{"lookup": 0, "sections": []}]})
    gateway = EmbeddingGateway(ask, pick, finish(), vector=[0, 1, 0])
    result = answer(gateway, service=meaning_service(), messages=[
        {"role": "user", "content": "how do i become a peer mentor"}])
    assert "Become a peer mentor for first-years." in result.body["answer"]
    # Found by the asked words alone (no embedding), the pick still decides.
    result = answer(ScriptedGateway(ask, pick, finish()), service=meaning_service(), messages=[
        {"role": "user", "content": "how do i become a peer mentor"}])
    assert "Become a peer mentor for first-years." not in result.body["answer"]


def test_who_leads_or_what_an_office_does_is_told_from_other_questions() -> None:
    from rockygpt_brain.engine import asks_who_leads
    for message in ("Who is the provost and what does the provost's office do?",
                    "Who is the director of the Center for Health and Counseling Services? "
                    "Also what's the phone number",
                    "who's the EOF program director and what's her email",
                    "which dean runs social science and social work, and what room is their office",
                    "who's in charge of the student accounts office",
                    "Who is the Title IX coordinator at Ramapo, and which office are they in?",
                    "what does the office of specialized services do"):
        assert asks_who_leads(message), message
    for message in ("what is the registrar's email", "is the library open sunday",
                    "who do I call to get a parking permit", "where is the bursar's office"):
        assert not asks_who_leads(message), message
