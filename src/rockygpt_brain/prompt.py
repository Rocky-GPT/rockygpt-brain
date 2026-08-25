"""Reading a model instruction off disk.

Every instruction sent to a model is a `prompt.md` beside the module that
sends it. They are prose — the model reads them as prose, and so does anyone
reviewing a change to one. As markdown they diff as sentences rather than as a
quoted string, they render in a reviewer's editor, and they cannot quietly
acquire an f-string, a conditional, or a value looked up at import. A prompt
that can compute is a prompt that behaves differently on some turns than the
file appears to say.

The file is the whole instruction and nothing else — no header, no notes, no
section that gets stripped on the way out. What you read is byte for byte what
the model is sent, which is the same reason these are not Python: any rule for
subtracting part of the file is one more difference between what it says and
what it does. Notes for whoever edits a prompt live in the docstring of the
module that loads it, where they cannot be sent by construction.
"""

from __future__ import annotations

from pathlib import Path


def beside(module_file: str) -> str:
    """The instruction in the `prompt.md` next to the given module.

    Pass `__file__`. Read at import, so a missing or unreadable prompt fails at
    startup rather than on the first turn that needed it.
    """
    return (Path(module_file).parent / "prompt.md").read_text(encoding="utf-8").strip()
