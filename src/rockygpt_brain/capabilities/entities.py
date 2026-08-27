from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

_MINOR = frozenset({"and", "of", "the", "in", "for", "to", "a", "an", "at", "on"})


class EntityResolutionFailed(Exception):
    pass


class EntityAmbiguous(EntityResolutionFailed):
    """Several entities matched equally well. Never resolvable by guessing."""


class EntityNotFound(EntityResolutionFailed):
    """Nothing in the closed set matched. A caller with a weaker handle on the
    mention — a code the catalogue has no name for — may still use it."""


@dataclass(frozen=True, slots=True)
class EntityCandidate:
    """One thing in a closed set, as the dataset spells it.

    `aliases` are what the data says this entity is also called. They belong to
    the data because that is where a rename or a new short form is known —
    scattered through prompts or a capability's own code they are invisible to
    every other capability that needs the same name.
    """

    id: str
    label: str
    aliases: tuple[str, ...] = field(default=())


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _initials(label: str) -> str:
    words = [word for word in re.findall(r"[a-z0-9]+", label.casefold()) if word not in _MINOR]
    return "".join(word[0] for word in words)


def resolve_entity(kind: str, mention: str, candidates: list[EntityCandidate]) -> str:
    """Turn one mention into dataset identity, or refuse.

    The order is the same for every capability, strongest evidence first:

        1. the canonical id or code, exactly
        2. the canonical name, exactly
        3. an alias the data gives the entity
        4. an abbreviation of the name, where only one entity claims it
        5. anything else, or more than one match at the deciding step: refuse

    The first step that matches anything decides, so a weaker kind of evidence
    never overrules a stronger one, and two entities matching equally well are
    ambiguous rather than a coin toss. Nothing here weighs how many rows an
    entity has: the more popular reading of an abbreviation is a guess wearing
    a statistic, and the data is where a real short form is recorded.
    """
    wanted = _key(mention)
    if not wanted:
        raise EntityNotFound(f"{kind} was given an empty mention")

    flat = wanted.replace(" ", "")
    steps: tuple[Callable[[EntityCandidate], bool], ...] = (
        lambda c: _key(c.id) == wanted,
        lambda c: _key(c.label) == wanted,
        lambda c: any(_key(alias) == wanted for alias in c.aliases),
        lambda c: _initials(c.label) == flat,
    )
    for step in steps:
        matched = [candidate for candidate in candidates if step(candidate)]
        if len(matched) == 1:
            return matched[0].id
        if matched:
            names = ", ".join(sorted(candidate.id for candidate in matched))
            raise EntityAmbiguous(f"{kind} mention {mention!r} matches several entities: {names}")
    raise EntityNotFound(f"{kind} mention {mention!r} matches nothing in the dataset")
