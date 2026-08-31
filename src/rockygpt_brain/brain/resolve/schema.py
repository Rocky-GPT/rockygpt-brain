from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from rockygpt_brain.brain.values import Text


class Filled(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    text: Text
    refers_to: Text = Field(alias="refersTo")


class Resolution(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    references: list[Filled] = Field(default_factory=list, max_length=6)
    used_turns: list[int] = Field(default_factory=list, max_length=20, alias="usedTurns")
    resolved: Text
