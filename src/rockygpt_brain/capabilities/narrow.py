from __future__ import annotations

import re

_WORD = re.compile(r"[\w']+")


def holds(text: str, wanted: str) -> bool:
    haystack = text.casefold()
    words = _WORD.findall(wanted.casefold())
    return all(word in haystack for word in words) if words else True
