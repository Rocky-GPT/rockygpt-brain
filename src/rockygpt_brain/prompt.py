"""Reading a model instruction off disk.

The instructions are `prompt.md` files, not Python. They are prose — the model
reads them as prose, and so does anyone reviewing a change to one. As a `.md`
they diff as sentences rather than as a quoted string, they render in a
reviewer's editor, and they cannot quietly acquire an f-string, a conditional,
or a value looked up at import time. A prompt that can compute is a prompt that
behaves differently on some turns than the file appears to say.

Every prompt in this codebase is a file like that, wherever it is called from.
Each is in two halves, split by a `---` rule. Above it is for whoever edits the
file: what this instruction is for, and what it has broken before. Below it is
what the model is sent, and nothing else. Keeping the rationale in
the same file is what makes it likely to be read; keeping it above the rule is
what stops the model reading it too — told about a past routing bug, a model
will try to be helpful about it.
"""

from __future__ import annotations

from pathlib import Path

_RULE = "\n---\n"


def beside(module_file: str) -> str:
    """The instruction in the `prompt.md` next to the given module.

    Pass `__file__`. Read at import, so a missing or unreadable prompt fails at
    startup rather than on the first turn that needed it.
    """
    text = (Path(module_file).parent / "prompt.md").read_text(encoding="utf-8")
    _, separator, instruction = text.partition(_RULE)
    if not separator:
        raise ValueError(f"{module_file}: prompt.md has no `---` rule before the instruction")
    return instruction.strip()
