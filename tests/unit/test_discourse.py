"""Discourse record and its scoping.

The behaviours under test are the ones the corpus measured failing:
`dsc-conversation-truth` (20%) needs the record to exist and to be presented as
distinct from campus evidence, and `dsc-topic-shift-recall` (0%) needs it to
outlive the client's ten-entry history window.
"""

from rockygpt_brain.brain.conversation_state import (
    MAX_CONVERSATIONS,
    record_for,
    reset,
)
from rockygpt_brain.brain.discourse import (
    MAX_ANSWER_CHARS,
    MAX_SPOKEN,
    DiscourseRecord,
    render_discourse,
)


def _speak(record: DiscourseRecord, question: str, answer: str) -> None:
    record.record(question=question, answer=answer, withheld=False)


class TestRecordBounds:
    def test_keeps_only_the_most_recent_exchanges(self) -> None:
        record = DiscourseRecord()
        for index in range(MAX_SPOKEN + 4):
            _speak(record, f"question {index}", f"answer {index}")

        assert len(record.spoken) == MAX_SPOKEN
        # Turn numbering counts the whole conversation, not the window, so a
        # rendered record cannot imply the conversation started later than it
        # did.
        assert record.turns == MAX_SPOKEN + 4
        assert record.spoken[-1].question == f"question {MAX_SPOKEN + 3}"

    def test_answer_is_trimmed_to_the_claim(self) -> None:
        record = DiscourseRecord()
        _speak(record, "q", "x" * (MAX_ANSWER_CHARS * 3))
        assert len(record.spoken[0].answer) == MAX_ANSWER_CHARS

    def test_whitespace_is_collapsed(self) -> None:
        record = DiscourseRecord()
        _speak(record, "when\n\nis  it", "at\t10:15   AM")
        assert record.spoken[0].question == "when is it"
        assert record.spoken[0].answer == "at 10:15 AM"

    def test_survives_beyond_the_client_history_window(self) -> None:
        # The request carries at most 10 raw entries (schemas/chat.py), which is
        # five exchanges. A record holding eight still answers a question about
        # the first of them.
        record = DiscourseRecord()
        _speak(record, "When is the next shuttle?", "The next shuttle departs at 10:15 AM.")
        for topic in ("menu", "clubs", "library", "spring break", "events"):
            _speak(record, f"what about {topic}?", f"here is {topic}")

        rendered = render_discourse(record)
        assert rendered is not None
        assert "10:15 AM" in rendered


class TestWithheldRepliesAreNotFacts:
    def test_withheld_turn_renders_as_nothing_said(self) -> None:
        record = DiscourseRecord()
        record.record(question="when is the shuttle?", answer="", withheld=True)
        rendered = render_discourse(record)
        assert rendered is not None
        assert "held the reply back" in rendered
        assert "you answered:" not in rendered


class TestRendering:
    def test_empty_record_renders_nothing(self) -> None:
        assert render_discourse(DiscourseRecord()) is None

    def test_separates_conversation_evidence_from_campus_evidence(self) -> None:
        record = DiscourseRecord()
        _speak(record, "when is the next shuttle?", "It departs at 10:15 AM.")
        rendered = render_discourse(record)
        assert rendered is not None
        # Compared against whitespace-collapsed text: the section is wrapped for
        # the prompt, so a phrase can straddle a line break.
        flat = " ".join(rendered.split())
        # The whole point of the section: a retrieved row is not proof it was
        # spoken, and a spoken value is not proof it is still true.
        assert "not proof that you ever said it" in flat
        assert "not proof that it is still true" in flat
        assert '"conversation"' in flat


class TestScoping:
    def setup_method(self) -> None:
        reset()

    def test_same_visitor_and_conversation_share_a_record(self) -> None:
        first = record_for("visitor-a", "conv-1")
        second = record_for("visitor-a", "conv-1")
        assert first is second

    def test_a_repeated_conversation_id_does_not_cross_visitors(self) -> None:
        # A conversation id is a client-supplied value. Keying on it alone would
        # hand one visitor's record to anyone who guessed or replayed the id.
        mine = record_for("visitor-a", "conv-1")
        theirs = record_for("visitor-b", "conv-1")
        assert mine is not theirs

    def test_a_turn_missing_either_id_is_not_retained(self) -> None:
        assert record_for(None, "conv-1") is None
        assert record_for("visitor-a", None) is None
        assert record_for(None, None) is None

    def test_oldest_conversation_is_evicted_first(self) -> None:
        for index in range(MAX_CONVERSATIONS + 5):
            record = record_for("visitor", f"conv-{index}")
            assert record is not None
            _speak(record, "q", "a")

        # The first conversations are gone; a fresh record comes back empty
        # rather than someone else's.
        revived = record_for("visitor", "conv-0")
        assert revived is not None
        assert revived.spoken == []

    def test_recent_use_protects_a_record_from_eviction(self) -> None:
        kept = record_for("visitor", "conv-keep")
        assert kept is not None
        _speak(kept, "q", "a")
        for index in range(MAX_CONVERSATIONS - 1):
            record_for("visitor", f"conv-{index}")
            record_for("visitor", "conv-keep")  # touch it

        still_there = record_for("visitor", "conv-keep")
        assert still_there is not None
        assert len(still_there.spoken) == 1
