from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from rockygpt_brain.brain.values import Text


class Unresolved(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    text: Text


class Reading(BaseModel):
    """What the question says on its own, read with no conversation in scope."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    normalized: Text
    unresolved: list[Unresolved] = Field(
        default_factory=list, max_length=6, alias="unresolvedReferences"
    )
    needs_context: bool = Field(default=False, alias="needsContext")


class Reference(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    text: Text
    refers_to: Text = Field(alias="refersTo")


class Understanding(BaseModel):
    """The two readings composed: what was asked, and what it turned out to mean.

    Not a model response. A question that stands on its own is composed from
    the first reading alone, because the second one never runs.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    normalized: Text
    references: list[Reference] = Field(default_factory=list, max_length=6)
    used_turns: list[int] = Field(default_factory=list, max_length=20, alias="usedTurns")
    uses_context: bool = Field(default=False, alias="usesContext")
    resolved: Text
