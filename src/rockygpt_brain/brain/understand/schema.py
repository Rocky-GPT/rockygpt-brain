from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from rockygpt_brain.brain.values import Text


class Reference(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    text: Text
    refers_to: Text = Field(alias="refersTo")


class Understanding(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    normalized: Text
    references: list[Reference] = Field(default_factory=list, max_length=6)
    used_turns: list[int] = Field(default_factory=list, max_length=20, alias="usedTurns")
    uses_context: bool = Field(default=False, alias="usesContext")
    resolved: Text
