"""The constrained scalars every stage schema is built from.

Two of them, shared rather than repeated, so a length or a pattern is changed
in one place. They live above the stages because `understand` must not import
from `plan` — the stages depend forwards, never back.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import StringConstraints

#: A name a plan may use for a capability or one of its fields.
FieldName = Annotated[
    str, StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
]
#: A short piece of prose: a question, a topic, a thing referred to.
Text = Annotated[str, StringConstraints(min_length=1, max_length=200)]
