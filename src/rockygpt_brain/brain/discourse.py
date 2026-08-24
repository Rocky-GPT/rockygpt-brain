"""What Rocky said, kept apart from what the campus data says.

Two questions look alike and have different answers:

    "When is the next shuttle?"          -> current campus truth
    "What time did you tell me earlier?" -> conversation truth

The tool results answer the first. Nothing in them can answer the second: a
timetable row proves 10:15 AM is a real departure, never that 10:15 AM is the
departure this student was given. Measured, `dsc-conversation-truth` scored 20%
at k=5 — Rocky either re-queried and reported the trip that is next *now*, or
refused the turn outright, because a claim about the conversation has no campus
`sourceId` and the grounding rule treats an unsourced campus claim as a reason
to fall back.

This module holds the record that answers the second question. It is
deliberately not authoritative about anything else: it stores what was said and
what was asked, never what is true. No campus fact enters the citation path
through here, and `ProvenanceRegistry` remains the only place a `Citation` is
built.

Bounded by construction. A record keeps the last `MAX_SPOKEN` exchanges as
trimmed claims rather than raw transcript — the claim, not the prose around it —
which is what lets it outlive the ten-entry raw-history window the client sends
(`schemas/chat.py`; the browser fills it walking backwards, so five exchanges of
menu listings evict the fact worth remembering).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Exchanges retained. Enough to answer "what did you tell me" across a topic
# change, far short of a transcript.
MAX_SPOKEN = 8
# Characters kept of each side. A claim, not the prose around it.
MAX_QUESTION_CHARS = 160
MAX_ANSWER_CHARS = 220
# Entity mentions retained per turn, and their length. These are tool names and
# the subjects a turn was about — never record values.
MAX_ENTITIES_PER_TURN = 6
MAX_ENTITY_CHARS = 60


@dataclass(frozen=True, slots=True)
class SpokenTurn:
    """One exchange, as it actually happened."""

    turn: int
    question: str
    answer: str
    #: Whether the reply was withheld. A recap must not promote a refusal into
    #: a fact Rocky never stated.
    withheld: bool
    #: Subjects this turn touched — tool names, not arguments or values.
    entities: tuple[str, ...] = ()


@dataclass(slots=True)
class DiscourseRecord:
    spoken: list[SpokenTurn] = field(default_factory=list)
    turns: int = 0

    def record(
        self,
        *,
        question: str,
        answer: str,
        withheld: bool,
        entities: tuple[str, ...] = (),
    ) -> None:
        self.turns += 1
        self.spoken.append(
            SpokenTurn(
                turn=self.turns,
                question=_condense(question, MAX_QUESTION_CHARS),
                answer=_condense(answer, MAX_ANSWER_CHARS),
                withheld=withheld,
                entities=tuple(
                    _condense(entity, MAX_ENTITY_CHARS)
                    for entity in entities[:MAX_ENTITIES_PER_TURN]
                ),
            )
        )
        del self.spoken[:-MAX_SPOKEN]


def _condense(text: str, limit: int) -> str:
    return " ".join(text.split())[:limit]


def render_discourse(record: DiscourseRecord) -> str | None:
    """The conversation as Rocky's own record of it, for the system prompt.

    Rendered under its own heading and kept away from the evidence section, so
    the two cannot be read as the same kind of thing. The instruction that a
    retrieved row is not proof it was spoken is the point of the separation:
    without it, a model asked what it said answers from whatever the tools
    return now, which is a different claim that happens to be about the same
    subject.
    """
    if not record.spoken:
        return None

    lines = [
        "What you have already told this student, oldest first:",
    ]
    for turn in record.spoken:
        if turn.withheld:
            lines.append(
                f'  {turn.turn}. they asked "{turn.question}" — you held the reply '
                f"back and told them nothing"
            )
        else:
            lines.append(
                f'  {turn.turn}. they asked "{turn.question}" — '
                f"you answered: {turn.answer}"
            )

    lines += [
        "",
        "This is the record of the conversation, and it is the only evidence for",
        "what you said. It is not evidence about the campus: a row a tool returns",
        "now is not proof that you ever said it, and what you said earlier is not",
        "proof that it is still true.",
        "",
        "When the student asks what you told them, what you said earlier, or what",
        "time you gave them, answer from this record and use route",
        '"conversation". Report what you actually said even when a fresh lookup',
        "would now give a different answer — they asked about the conversation,",
        "not about the campus. Do not call a tool to answer it, and do not cite",
        "anything: there is no campus source for a fact about this conversation.",
        "If the exchange they mean is not in this record, say you do not have it",
        "rather than looking up a current answer and offering that instead.",
        "",
        "When they ask what is true now — even about the same subject — that is a",
        'campus question: look it up and answer it as usual with route "standard".',
    ]
    return "\n".join(lines)
