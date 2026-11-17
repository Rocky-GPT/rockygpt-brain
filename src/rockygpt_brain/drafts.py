"""The reply while the model writes it (audit item 7, 11-17).

The student site already reads a stream from the Brain and shows a "Draft · not verified" box
while an answer is checked; the Brain sent one block at the end, so a student waited for the
whole answer. When a turn has a sink (the chat route sets one for a streamed request), the
writer's call is streamed and every growth of its reply text goes to the sink. The finished
answer still replaces the draft, after every check, exactly as before.
"""

import contextvars
import re
from collections.abc import Callable

# Where this turn's draft goes: the chat route's queue for a streamed request, else nothing.
DRAFT_SINK: contextvars.ContextVar[Callable[[str], None] | None] = contextvars.ContextVar(
    "draft_sink", default=None)

_TEXT_KEY = re.compile(r'"text"\s*:\s*"')
_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r",
            "t": "\t"}
# The writer's section marks ("[3]", "[2, 5]") mean nothing to a student; a mark still being
# written at the end of the draft ("[1", "[") is left out too.
_MARKS = re.compile(r"\s*\[\d+(?:\s*,\s*\d+)*\]|\s*\[[\d,\s]*$")


def text_so_far(arguments: str) -> str | None:
    """The reply text in the write_answer arguments streamed so far: the JSON string after the
    "text" key, decoded up to the last whole character. None until that key has arrived."""
    match = _TEXT_KEY.search(arguments)
    if match is None:
        return None
    out: list[str] = []
    i, end = match.end(), len(arguments)
    while i < end:
        char = arguments[i]
        if char == '"':
            break
        if char != "\\":
            out.append(char)
            i += 1
            continue
        if i + 1 >= end:
            break  # an escape cut in two: wait for the rest
        code = arguments[i + 1]
        if code == "u":
            digits = arguments[i + 2:i + 6]
            if len(digits) < 4:
                break
            try:
                value = int(digits, 16)
            except ValueError:
                return "".join(out)
            if 0xD800 <= value < 0xDC00:  # the first half of a pair; wait for the second
                low = arguments[i + 6:i + 12]
                if len(low) < 6:
                    break
                if low.startswith("\\u"):
                    try:
                        second = int(low[2:], 16)
                    except ValueError:
                        return "".join(out)
                    out.append(chr(0x10000 + ((value - 0xD800) << 10) + (second - 0xDC00)))
                    i += 12
                    continue
            out.append(chr(value))
            i += 6
            continue
        out.append(_ESCAPES.get(code, code))
        i += 2
    return "".join(out)


def shown(text: str) -> str:
    """The draft as a student sees it: without the section marks, trimmed."""
    return _MARKS.sub("", text).strip()
