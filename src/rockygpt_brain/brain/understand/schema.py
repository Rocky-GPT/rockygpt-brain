"""What BRAIN #1 returns.

The field order is load-bearing. A structured response is generated field by
field as declared, so tidying, then finding what points elsewhere, then naming
the turns it points into, then writing it all out, each happens with the
previous already on the page. Reorder them and the later ones are guesses —
declared last, `resolved` comes back as the question echoed verbatim.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from rockygpt_brain.brain.values import Text


class Reference(BaseModel):
    """A word in the question that points somewhere else, and where it points."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    text: Text
    refers_to: Text = Field(alias="refersTo")


class Understanding(BaseModel):
    """The question, read. BRAIN #1's whole output."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    #: The question with its wording tidied and nothing else — no reference
    #: followed, no subject filled in.
    normalized: Text
    #: Everything the question borrows from the conversation.
    references: list[Reference] = Field(default_factory=list, max_length=6)
    #: Where in the conversation those references point, counting from 0.
    used_turns: list[int] = Field(default_factory=list, max_length=20, alias="usedTurns")
    #: Whether this question needs the conversation at all. The only stage that
    #: can see the turns is the only one in a position to say.
    uses_context: bool = Field(default=False, alias="usesContext")
    #: The question rewritten to stand on its own. This is all BRAIN #2 gets.
    resolved: Text
