"""Each office page's marks, set by code when the meaning index is built: who owns the page, the
day it says it last changed, and whether it is still current.

A page is not current when its own address names only terms or years already over
(/costinfo/2024-2025-cost-information/, /wellness-services-fall-2022/) or says it holds previous or
past items (/previous-deadlines/). Search leaves such a page out so today's page comes first; the
page stays in the release for anyone who asks about that year. A page's first heading is not read:
"Student Handbook 2025-2026" is today's handbook, and a heading names its year far more often than
an old page does.

The owner is the linking office (or topic) that links the most pages of the page's site folder:
Housing for every /reslife/ page, though a hall desk links some of them; the Center for Student
Involvement for a club's page under its site. A tie goes to the lower id, so it is the same on
every build.
"""

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from rockygpt_brain.retrieval.entity_facts import address_terms, page_updated

# A word in a page's address that says the page holds old items. "pervious" is the site's own typo
# (/previous-deadlines/pervious-deadlines-2025/). Whole words only: not "pastoral" or "previously".
_PREVIOUS = re.compile(r"(?<![a-z0-9])(previous|pervious|past)(?![a-z0-9])")


@dataclass(frozen=True)
class PageMark:
    owner: str | None
    updated: date | None
    until: date | None
    previous: bool

    def current(self, today: date) -> bool:
        """Whether the page is still about today: it holds no previous items and the last term or
        year its address names, if any, has not ended."""
        return not self.previous and (self.until is None or self.until >= today)

    def to_meta(self) -> list[Any]:
        return [self.owner, _iso(self.updated), _iso(self.until), self.previous]

    @classmethod
    def from_meta(cls, value: Sequence[Any]) -> "PageMark":
        owner, updated, until, previous = value
        return cls(str(owner) if owner else None, _day(updated), _day(until), bool(previous))


def address_until(url: str) -> date | None:
    """The day the last term or year a page's address names ends; None when it names none."""
    named = address_terms(url)
    return max(end for end, _ in named) if named else None


def page_marks(row: Mapping[str, Any], owner: str | None) -> PageMark:
    """One office page's marks, from its captured row and its owner (page_owners)."""
    url = str(row.get("url") or "")
    path = urlsplit(url).path.casefold()
    return PageMark(owner=owner, updated=page_updated(row), until=address_until(url),
                    previous=bool(_PREVIOUS.search(path)))


def page_owners(links: Mapping[str, Iterable[str]],
                folders: Mapping[str, str | None]) -> dict[str, str]:
    """Each page's owner: of the nodes that link it, the one linking the most pages of its folder.
    `links` holds each page's linking node ids, `folders` each page's site folder."""
    linked = {page: sorted(set(ids)) for page, ids in links.items()}
    by_folder: dict[str | None, Counter[str]] = defaultdict(Counter)
    for page, ids in linked.items():
        by_folder[folders.get(page)].update(ids)
    owners: dict[str, str] = {}
    for page, ids in linked.items():
        if not ids:
            continue
        folder = folders.get(page)
        counts = by_folder[folder] if folder else Counter(ids)
        owners[page] = min(ids, key=lambda node: (-counts[node], node))
    return owners


def _iso(day: date | None) -> str | None:
    return day.isoformat() if day else None


def _day(value: Any) -> date | None:
    return date.fromisoformat(str(value)) if value else None
