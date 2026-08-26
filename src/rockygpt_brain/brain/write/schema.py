"""What BRAIN #3 returns: whether the evidence held, then the prose.

`supported` is declared first, and that is the whole point of it. A structured
response is generated field by field in the order declared, so the judgement is
made before a single word of the answer exists. Asked the other way round — write
an answer, then say whether it was supported — a model has already produced the
prose and will not disown it.

The same ordering trick is why `resolved` sits before `lane` in the understand
schema. Declared after, it came back as the question echoed verbatim.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    #: Whether what PYTHON supplied actually answers the question. Judged
    #: before the answer is written, and acted on by Python rather than here:
    #: a model that has decided the evidence is thin still writes something.
    sufficient_evidence: bool = Field(alias="sufficientEvidence")
    answer: str
    suggested_questions: list[str] = Field(default_factory=list, alias="suggestedQuestions")
