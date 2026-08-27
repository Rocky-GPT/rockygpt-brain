from __future__ import annotations

import re
from dataclasses import dataclass


class EntityResolutionFailed(Exception):
    pass


@dataclass(frozen=True, slots=True)
class EntityCandidate:
    id: str
    label: str
    aliases: tuple[str, ...] = ()


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def resolve_entity(kind: str, mention: str, candidates: list[EntityCandidate]) -> str:
    """Resolve one mention only when the closed dataset identifies one entity."""
    wanted = _key(mention)
    exact = [
        candidate
        for candidate in candidates
        if wanted
        in {
            _key(candidate.id),
            _key(candidate.label),
            *(_key(alias) for alias in candidate.aliases),
        }
    ]
    if len(exact) == 1:
        return exact[0].id

    contained = [
        candidate
        for candidate in candidates
        if wanted
        and any(
            wanted in _key(value)
            for value in (candidate.id, candidate.label, *candidate.aliases)
        )
    ]
    if len(contained) == 1:
        return contained[0].id
    raise EntityResolutionFailed(
        f"{kind} mention {mention!r} resolved to {len(exact) or len(contained)} entities"
    )
