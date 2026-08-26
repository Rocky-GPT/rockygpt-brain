"""Keeping the rows a filter actually meant.

A capability sends its free-text filters to the data service as one search
string, and the service does a real search on them — stemming, ranking, all of
it. What comes back then has to be narrowed per field, because the search
string is a blob: asked for `name=Birch` and `type=building`, the service sees
`Birch building` and cannot tell which term belongs to which field.

That narrowing has to be no stricter than the search that produced the rows.
It was: each filter had to appear in the field *verbatim*, so a plan filtering
`topic="withdraw from a course"` threw away the record titled "Session II
Courses - Last Day to Withdraw from Courses". The service had found it; this
undid that.

`holds` asks instead whether every word of the filter is somewhere in the
field, which is close to what the search did and keeps per-field precision.
"""

from __future__ import annotations

import re

_WORD = re.compile(r"[\w']+")


def holds(text: str, wanted: str) -> bool:
    """Whether every word of `wanted` appears in `text`.

    Word by word rather than as a phrase, so wording that differs in order,
    plurals or small joining words still matches — the difference between
    "withdraw from a course" and "Withdraw from Courses" is not a difference
    the person asking meant.
    """
    haystack = text.casefold()
    words = _WORD.findall(wanted.casefold())
    return all(word in haystack for word in words) if words else True
