"""A how-to question answered from the one answer card it clearly matches, by code, and a page
answer that reads the page its card chose (answer cards, step 4: engine._card_answer and
engine._read_winners). Both are behind a setting that is off unless asked for."""

import asyncio
from collections.abc import Sequence
from typing import Any

import pytest

from rockygpt_brain import academics, card_answer
from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.engine import ChatEngine
from rockygpt_brain.provider import TurnBudget
from rockygpt_brain.retrieval import card_join
from rockygpt_brain.retrieval.meaning import MeaningIndex
from rockygpt_brain.turn import intake
from test_answer_cards import (
    CARD_ID,
    LOOK_ID,
    NOW,
    ORIENTATION,
    PERMITS,
    SAFETY,
    SAID,
    TODAY,
    WithCards,
    page,
    with_cards,
)
from test_engine import ScriptedGateway
from test_meaning import index_of
from test_one_call import one_reply

WIN, APPEAL = ("page-permits", "e0"), ("page-permits", "e2")
OLD, FAQ, SECTION = ("page-old", "e0"), ("page-faq", "e0"), ("page-permits", "0")
ASKED = "how do i get a parking permit"


class CardFacts(WithCards):
    index: MeaningIndex | None = None

    def meaning_index(self) -> MeaningIndex | None:
        return self.index


def release(appeal: str = "ready", *, appeal_at: Sequence[float] = (0, 1, 0, 0, 0),
            copies: bool = False, ahead: bool = False, catalog: bool = False) -> CardFacts:
    """Two cards: the permit card (its winner on Public Safety's page; an old page's task and a
    passed deadline left out) and the ticket appeal's. Each task and one plain section is a unit
    of the meaning index, each along its own axis unless the appeal's is given another place
    (`appeal_at`). `copies` has the two cards name each other as near-copies; `ahead` has the
    permit card name the appeal's as the near-copy whose page is said in place of its own;
    `catalog` gives the index a course card, as a release with the college catalog has."""
    source = with_cards()
    safety = {**source.entities[0], "links": [
        {**source.entities[0]["links"][0],
         "source_record_keys": [*source.entities[0]["links"][0]["source_record_keys"], "old"]},
        *source.entities[0]["links"][1:]]}
    held = CardFacts(
        dataset_version=source.dataset_version, identity_hash=source.identity_hash,
        entities=[safety, *source.entities[1:]], contacts=source.contacts, now=lambda: NOW,
        records={"office_sections": [
            *source.records["office_sections"],
            page("old", "https://www.ramapo.edu/publicsafety/permits-2019/",
                 [{"task": "Get a parking permit", "section": "Permits",
                   "steps": [{"text": "Mail the paper form.", "quote": "Mail the paper form"}]}],
                 page_modified_at="2019-05-01T14:00:00Z")]})
    held.cards = card_join.cards_file("release-1", "identities-1", TODAY, [
        {"id": CARD_ID, "status": "ready", "flags": [], "notes": [], "owners": [SAFETY],
         "members": [
             {"record_id": WIN[0], "key": WIN[1], "role": "winner"},
             {"record_id": OLD[0], "key": OLD[1], "role": "left_out", "why": "old"},
             {"record_id": FAQ[0], "key": FAQ[1], "role": "left_out", "why": "over",
              "label": "September 1, 2026"}],
         "near": [LOOK_ID] if copies or ahead else [], "ahead": [LOOK_ID] if ahead else []},
        {"id": LOOK_ID, "status": appeal, "flags": [], "notes": [], "owners": [SAFETY],
         "members": [{"record_id": APPEAL[0], "key": APPEAL[1], "role": "winner"}],
         "near": [CARD_ID] if copies or ahead else []}])
    held.index = index_of([(*WIN, [SAFETY], [[1, 0, 0, 0, 0]]),
                           (*APPEAL, [SAFETY], [list(appeal_at)]),
                           (*OLD, [SAFETY], [[0, 0, 1, 0, 0]]),
                           (*FAQ, [ORIENTATION], [[0, 0, 0, 1, 0]]),
                           (*SECTION, [SAFETY], [[0, 0, 0, 0, 1]])],
                          {SAFETY: "Public Safety", ORIENTATION: "Orientation"},
                          academic=[("course-1", "course", [0, 0, 0, 0, 1])] if catalog else ())
    return held


class CardGateway(ScriptedGateway):
    """Every text embeds to one vector; a model call the test did not script fails the test."""

    def __init__(self, vector: Sequence[float], *steps: Any) -> None:
        super().__init__(*steps)
        self.vector = list(vector)

    async def embed_many(self, texts: Sequence[str], *, dimensions: int,
                         budget: TurnBudget) -> list[list[float]]:
        return [list(self.vector) for _ in texts]


def ask(gateway: CardGateway, question: str = ASKED, *, held: CardFacts | None = None,
        setting: bool | None = True, before: Sequence[dict[str, str]] = (),
        **sent: Any) -> Any:
    request = ChatRequest.model_validate(
        {"messages": [*before, {"role": "user", "content": question}], **sent})
    engine = ChatEngine(gateway, held or release(), output="written", howto_one_call=True,
                        read_first=False, card_answers=setting)
    return asyncio.run(engine.answer(intake(request, now=NOW), request))


# The question is the permit task's own (1.0), far from the appeal's (0.0).
CLEAR = [1, 0, 0, 0, 0]
# Nearest the appeal (0.71) and the permit task (0.70): no card leads.
TIED = [0.70, 0.71, 0, 0, 0]
PAGE_REPLY = "Public Safety: Register online, then pick it up at check-in. [1]"


def test_a_question_that_clearly_matches_one_card_is_answered_from_it_by_code() -> None:
    gateway = CardGateway(CLEAR)
    result = ask(gateway)
    assert result.body["answer"] == SAID and result.body["status"] == "answered"
    assert [c["url"] for c in result.body["citations"]][0] == PERMITS
    assert result.trace["decidedBy"] == "code" and gateway.inputs == []  # no model call
    assert result.trace["answerCard"] == {
        "card": CARD_ID, "score": 1.0, "lead": 1.0, "said": True,
        "task": "Get a student parking permit"}


def test_a_question_matching_a_left_out_page_is_answered_from_the_cards_winning_page() -> None:
    # The old page's task is the card's too: the card is matched by it and says the winner's steps.
    result = ask(CardGateway([0, 0, 1, 0, 0]))
    assert result.body["answer"] == SAID and "Mail the paper form" not in result.body["answer"]


def test_the_setting_is_off_unless_asked_for(monkeypatch: pytest.MonkeyPatch) -> None:
    result = ask(CardGateway(CLEAR, one_reply(PAGE_REPLY, [1])), setting=False)
    assert result.trace["decidedBy"] == "one_call" and "answerCard" not in result.trace
    assert result.body["answer"].startswith(PAGE_REPLY)
    # The Brain's setting is BRAIN_CARD_ANSWERS=1, and a release's engine keeps it.
    monkeypatch.delenv("BRAIN_CARD_ANSWERS", raising=False)
    assert not ChatEngine(CardGateway(CLEAR), release()).card_answers
    monkeypatch.setenv("BRAIN_CARD_ANSWERS", "1")
    engine = ChatEngine(CardGateway(CLEAR), release())
    assert engine.card_answers and engine.for_release(release()).card_answers
    monkeypatch.delenv("BRAIN_CARD_ANSWERS")
    assert ask(CardGateway(CLEAR, one_reply(PAGE_REPLY, [1])),
               setting=None).trace["decidedBy"] == "one_call"


def test_a_request_can_ask_for_the_cards_or_for_none_whatever_the_setting() -> None:
    assert ask(CardGateway(CLEAR), setting=False, cards=True).body["answer"] == SAID
    result = ask(CardGateway(CLEAR, one_reply(PAGE_REPLY, [1])), setting=True, cards=False)
    assert result.trace["decidedBy"] == "one_call" and "answerCard" not in result.trace


def test_no_card_leading_leaves_the_question_to_the_page_answer() -> None:
    result = ask(CardGateway(TIED, one_reply(PAGE_REPLY, [1])))
    assert result.trace["decidedBy"] == "one_call"
    assert result.trace["answerCard"] == {"card": LOOK_ID, "score": 0.712, "lead": 0.01,
                                          "said": False, "why": "not_clear"}


def test_a_card_does_not_have_to_lead_its_near_copies() -> None:
    # The appeal's task is worded close to the permit's (0.9 alike), so a question that is the
    # permit's (0.985) matches the appeal's too (0.961): neither card leads the other.
    close, asked = [0.9, 0.436, 0, 0, 0], [0.985, 0.17, 0, 0, 0]
    apart = ask(CardGateway(asked, one_reply(PAGE_REPLY, [1])), held=release(appeal_at=close))
    assert apart.trace["decidedBy"] == "one_call"
    assert apart.trace["answerCard"]["why"] == "not_clear" and "near" not in apart.trace[
        "answerCard"]
    # Named as near-copies, the same task on another page, the better match answers.
    gateway = CardGateway(asked)
    result = ask(gateway, held=release(appeal_at=close, copies=True))
    assert result.body["answer"] == SAID and gateway.inputs == []
    note = result.trace["answerCard"]
    assert (note["card"], note["said"], note["near"]) == (CARD_ID, True, [LOOK_ID])
    assert note["score"] == pytest.approx(0.985, abs=0.002)
    # Nothing but its near-copy scored: the lead is over no card at all.
    assert note["lead"] == pytest.approx(1.985, abs=0.002)


def test_a_near_copy_whose_page_is_ahead_is_said_in_place_of_the_best_card() -> None:
    close, asked = [0.9, 0.436, 0, 0, 0], [0.985, 0.17, 0, 0, 0]
    gateway = CardGateway(asked)
    result = ask(gateway, held=release(appeal_at=close, ahead=True))
    assert result.body["answer"].startswith("**Appeal a parking ticket**")
    assert result.body["status"] == "answered" and gateway.inputs == []
    # The trace still names the card the question matched, and the one whose page was said.
    note = result.trace["answerCard"]
    assert (note["card"], note["said"], note["near"], note["task"]) == (
        CARD_ID, True, [LOOK_ID], "Get a student parking permit")
    assert note["ahead"] == {"card": LOOK_ID, "task": "Appeal a parking ticket"}
    # One that code does not answer from leaves the best card its own page.
    own = ask(CardGateway(asked), held=release("needs_look", appeal_at=close, ahead=True))
    assert own.body["answer"] == SAID and "ahead" not in own.trace["answerCard"]
    # So does one that scored outside the lead: it is named ahead for its page, not its match.
    far = ask(CardGateway(CLEAR), held=release(ahead=True))
    assert far.body["answer"] == SAID and "ahead" not in far.trace["answerCard"]


def test_a_best_card_code_does_not_answer_from_is_not_answered_from_a_near_copy() -> None:
    # The question matched the appeal's card, which needs a look: its near-copy, the permit
    # card, may be another task, so the pages are read.
    close, asked = [0.9, 0.436, 0, 0, 0], [0.95, 0.31, 0, 0, 0]
    held = release("needs_look", appeal_at=close, copies=True)
    assert held.cards is not None
    held.cards["cards"][1]["ahead"] = [CARD_ID]
    result = ask(CardGateway(asked, one_reply(PAGE_REPLY, [1])), held=held)
    assert result.trace["decidedBy"] == "one_call"
    note = result.trace["answerCard"]
    assert (note["card"], note["said"], note["why"], note["near"]) == (
        LOOK_ID, False, "needs_look", [CARD_ID])


@pytest.mark.parametrize(("change", "why"), [
    ({"appeal": "needs_look"}, "needs_look"),
    ({}, "check"),
])
def test_a_clear_card_code_does_not_answer_from_is_left_to_the_page_answer(
        change: dict[str, str], why: str, monkeypatch: pytest.MonkeyPatch) -> None:
    if why == "check":  # The reply held something its facts do not.
        monkeypatch.setattr(ChatEngine, "card_said", lambda self, card, today: None)
    reply = one_reply("Public Safety: File the appeal form. [1]", [1])
    result = ask(CardGateway([0, 1, 0, 0, 0], reply), held=release(change.get("appeal", "ready")))
    assert result.trace["decidedBy"] == "one_call"
    assert result.trace["answerCard"] == {
        "card": LOOK_ID, "score": 1.0, "lead": 1.0, "said": False, "why": why,
        "task": "Appeal a parking ticket"}


def test_a_release_without_cards_or_with_another_releases_is_the_page_answer() -> None:
    for cards in (None, card_join.cards_file("release-0", "identities-1", TODAY, [])):
        held = release()
        held.cards = cards
        result = ask(CardGateway(CLEAR, one_reply(PAGE_REPLY, [1])), held=held)
        assert result.trace["decidedBy"] == "one_call"
        assert result.trace["answerCard"] == {"said": False, "why": "no_cards"}


@pytest.mark.parametrize(("question", "before"), [
    # A follow-up is read with the question before it: not one plain question.
    ("and for commuters?", [{"role": "user", "content": ASKED},
                            {"role": "assistant", "content": "Register online."}]),
    # Two asks: a card answers one.
    ("how do i get a parking permit? and what does it cost each term", []),
])
def test_only_a_plain_single_question_is_answered_from_a_card(
        question: str, before: list[dict[str, str]]) -> None:
    result = ask(CardGateway(CLEAR, one_reply(PAGE_REPLY, [1])), question, before=before)
    assert result.trace["decidedBy"] == "one_call" and "answerCard" not in result.trace


def test_the_card_is_asked_before_the_catalogs_pick() -> None:
    # "class" makes it a question the catalog's pick reads first when the release has catalog
    # cards; it names no program or course, and its card answers it with no model call.
    question = "how do i get a parking permit for my night class"
    held = release(catalog=True)
    assert held.index is not None and held.index.academic
    assert academics.asks_academics(question)
    gateway = CardGateway(CLEAR)
    result = ask(gateway, question, held=held)
    assert result.body["answer"] == SAID and gateway.inputs == []
    assert result.trace["answerCard"]["said"] and "catalogPick" not in result.trace


# Nearest the old page's task, then the appeal and the page's own section: with two units of the
# winning page ahead of it, the winning task is not among the sections chosen by meaning.
NEAR_OLD = [0, 0.5, 0.7, 0, 0.5]


def test_a_page_answer_reads_the_cards_winning_page_in_place_of_an_old_one() -> None:
    off = CardGateway(NEAR_OLD, one_reply("Public Safety: Mail the paper form. [1]", [1]))
    result = ask(off, setting=False)
    given = str(off.inputs[0])
    assert "Get a parking permit" in given and "Get a student parking permit" not in given
    assert "answerCardWinners" not in result.trace

    on = CardGateway(NEAR_OLD, one_reply(PAGE_REPLY, [1]))
    result = ask(on)
    assert result.trace["decidedBy"] == "one_call"
    assert result.trace["answerCard"]["why"] == "not_clear"
    given = str(on.inputs[0])
    assert "Get a student parking permit" in given and "Get a parking permit" not in given
    # The winning task was brought in; the appeal, the page's own section and the passed
    # deadline are read as chosen.
    assert len(result.trace["answerCardWinners"]) == 1
    assert len(result.trace["oneCallRead"]) == 4


def test_the_cards_tasks_and_the_best_card() -> None:
    held = release()
    cards = held.cards["cards"]  # type: ignore[index]
    known = {WIN, APPEAL, OLD, FAQ, SECTION}
    card_of, passed, left_out, near = card_answer.tasks_of(cards, known)
    assert card_of == {WIN: 0, OLD: 0, FAQ: 0, APPEAL: 1} and passed == frozenset()
    assert left_out == {OLD: (WIN, "old"), FAQ: (WIN, "over")} and near == {}
    # A task the index does not hold is no one's; one whose winner it lacks is not swapped.
    assert card_answer.tasks_of(cards, known - {WIN})[2] == {}
    assert card_answer.tasks_of(cards, known - {OLD})[0] == {WIN: 0, FAQ: 0, APPEAL: 1}
    # A card whose own date has passed has no current page to read before a term that is over.
    over = [{**cards[0], "status": "date_passed"}, cards[1]]
    assert card_answer.tasks_of(over, known)[1:3] == (frozenset({0}), {OLD: (WIN, "old")})
    # Near-copies are named by their places; an id no card has, and a card's own, name none.
    copies = [{**cards[0], "near": [LOOK_ID, "c-gone", CARD_ID]}, {**cards[1], "near": [CARD_ID]}]
    assert card_answer.tasks_of(copies, known)[3] == {0: frozenset({1}), 1: frozenset({0})}

    def best(*given: Any, **more: Any) -> tuple[int, float, float, list[int]] | None:
        found = card_answer.best_card(*given, **more)
        return found and (found[0], round(found[1], 6), round(found[2], 6), found[3])

    scores = {WIN: 0.5, OLD: 0.9, APPEAL: 0.84, SECTION: 0.99}
    # A card's score is its best task's; a section that is no task is no card's.
    assert best(scores, card_of) == (0, 0.9, 0.06, [])
    # A card whose date has passed is marked down, so today's card leads.
    assert best(scores, card_of, passed={0}) == (1, 0.84, 0.04, [])
    assert best({SECTION: 0.9}, card_of) is None
    assert best({WIN: 0.9}, card_of) == (0, 0.9, 1.9, [])
    # A card does not have to lead its near-copies: the lead is over the best card that is none,
    # and the near-copies that scored within the lead are named.
    both = {0: frozenset({1}), 1: frozenset({0})}
    assert best(scores, card_of, near=both) == (0, 0.9, 1.9, [1])
    assert best({WIN: 0.9, APPEAL: 0.7}, card_of, near=both) == (0, 0.9, 1.9, [])
    assert best(scores, card_of, near={1: frozenset({0})}) == (0, 0.9, 0.06, [])

    assert card_answer.clear(0.80, 0.08) and card_answer.clear(0.95, 0.5)
    assert not card_answer.clear(0.79, 0.5) and not card_answer.clear(0.95, 0.07)


def test_the_sections_read_keep_the_cards_choice_of_page() -> None:
    left_out = {OLD: (WIN, "old"), FAQ: (WIN, "over")}
    # An old page's task gives its place to the winner; the rest keep their order.
    assert card_answer.read_winners([APPEAL, OLD, SECTION], left_out) == (
        [APPEAL, WIN, SECTION], {WIN: OLD})
    # A task whose date is over stays, after the winner.
    assert card_answer.read_winners([FAQ, APPEAL], left_out) == ([WIN, FAQ, APPEAL], {WIN: FAQ})
    # A winner chosen further down moves up to the left-out task's place, once.
    assert card_answer.read_winners([OLD, FAQ, APPEAL, WIN], left_out) == (
        [WIN, FAQ, APPEAL], {WIN: OLD})
    # A winner chosen first, or already read by an earlier look, is not read again.
    assert card_answer.read_winners([WIN, OLD, APPEAL], left_out) == ([WIN, APPEAL], {})
    assert card_answer.read_winners([OLD, FAQ], left_out, given={WIN}) == ([FAQ], {})
    assert card_answer.read_winners([APPEAL, SECTION], left_out) == ([APPEAL, SECTION], {})
