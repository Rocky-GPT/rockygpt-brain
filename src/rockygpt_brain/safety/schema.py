"""The four concerns. A plan carries any number of them, including none."""

from __future__ import annotations

from enum import StrEnum


class Concern(StrEnum):
    """What is wrong with a question, apart from where its answer lives.

    A list rather than one value, because a question can be more than one of
    these at once and Python has to act on all of them. Four, and they stay
    four: this is a list of things Rocky must handle, not a taxonomy of things
    people ask, and it grows only if Rocky learns to handle something new.

    Not a lane. A lane says where an answer lives and none of these is a
    place — they say a question must be answered a particular way wherever the
    answer would have come from. As a lane, SAFETY had no executor, so the one
    turn that must never fail was the only one guaranteed to.
    """

    #: the person asking, or someone with them, may be harmed now
    EMERGENCY = "emergency"
    #: it asks for someone else's personal information
    PRIVACY = "privacy"
    #: it asks for credentials, or how Rocky is built
    SECRET = "secret"  # noqa: S105 — the name of a concern, not a credential
    #: answering it as asked would cause harm
    HARMFUL = "harmful"
