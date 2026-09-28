"""Keep tests from loading a developer's .env.

rockygpt-brain/.env can be a 1Password mount, and opening it waits for an
unlock prompt. CI has no .env, so this also makes local runs match CI.
"""

import os

os.environ["PYTHON_DOTENV_DISABLED"] = "1"

from collections.abc import Iterator

import pytest

from rockygpt_brain.core.provider import JEV_PAUSES


@pytest.fixture(autouse=True)
def _jev_answers() -> Iterator[None]:
    """A Jev pause one test causes never reaches another."""
    yield
    for pause in JEV_PAUSES.values():
        pause.reset()
