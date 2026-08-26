from __future__ import annotations

from typing import Annotated

from pydantic import StringConstraints

FieldName = Annotated[
    str, StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
]
Text = Annotated[str, StringConstraints(min_length=1, max_length=200)]
