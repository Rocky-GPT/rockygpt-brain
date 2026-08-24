"""Route/citation normalisation at the boundary.

Both directions of a route/citation mismatch used to be handled differently:
"standard" with no citations was downgraded, while "ungrounded" carrying
citations was rejected outright — which discarded the whole turn and returned a
canned apology in place of an answer whose text was fine. Once route
"conversation" came into use that rejection started firing in production
traces (`submit_malformed:unknown:value_error`).

Both are normalised here now, and only ever in the conservative direction.
"""

from rockygpt_brain.brain.answer import parse_submit_answer
from rockygpt_brain.brain.finalize import finalize
from rockygpt_brain.schemas.common import Citation

_CITATION = Citation(
    sourceId="src-1",
    title="Campus Hours",
    url="https://www.ramapo.edu/about/campus-hours/",
)


def _parsed(**overrides: object):
    payload: dict[str, object] = {
        "answerMarkdown": "The library closes at 9pm.",
        "route": "standard",
    }
    payload.update(overrides)
    parsed = parse_submit_answer(__import__("json").dumps(payload))
    assert parsed is not None
    return parsed


def _finalize(parsed, citations: list[Citation]):
    return finalize(parsed, citations=citations, tools_invoked=[], tool_calls_log=[])


class TestConservativeNormalisation:
    def test_standard_without_citations_is_downgraded(self) -> None:
        outcome = _finalize(_parsed(route="standard"), [])
        assert outcome.route == "ungrounded"

    def test_standard_with_citations_is_left_alone(self) -> None:
        outcome = _finalize(_parsed(route="standard", citedSourceIds=["src-1"]), [_CITATION])
        assert outcome.route == "standard"
        assert len(outcome.citations) == 1

    def test_ungrounded_carrying_citations_keeps_the_answer_and_drops_them(self) -> None:
        parsed = _parsed(route="ungrounded", citedSourceIds=["src-1"])
        outcome = _finalize(parsed, [_CITATION])
        # The answer survives; only the contradictory field is discarded.
        assert outcome.answer == "The library closes at 9pm."
        assert outcome.route == "ungrounded"
        assert outcome.citations == []

    def test_conversation_carrying_citations_keeps_the_answer_and_drops_them(self) -> None:
        parsed = _parsed(route="conversation", citedSourceIds=["src-1"])
        outcome = _finalize(parsed, [_CITATION])
        assert outcome.answer == "The library closes at 9pm."
        assert outcome.route == "conversation"
        assert outcome.citations == []

    def test_conversation_without_citations_is_never_downgraded(self) -> None:
        # A recollection is expected to have no campus source. Downgrading it to
        # "ungrounded" would relabel a verified answer as an unverifiable one.
        outcome = _finalize(_parsed(route="conversation"), [])
        assert outcome.route == "conversation"

    def test_an_uncited_answer_is_never_promoted(self) -> None:
        # The one direction normalisation must never take: no route that claims
        # nothing was verified may come out claiming it was.
        for route in ("ungrounded", "conversation"):
            outcome = _finalize(_parsed(route=route, citedSourceIds=["src-1"]), [_CITATION])
            assert outcome.route != "standard"
