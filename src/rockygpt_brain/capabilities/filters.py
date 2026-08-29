from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class FilterKind(StrEnum):
    """The small shared vocabulary used to validate capability filters."""

    ENUM = "enum"
    ENTITY = "entity"
    DATE = "date"
    INSTANT = "instant"
    TEXT = "text"


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


@dataclass(frozen=True, slots=True)
class FilterSpec:
    """What one planner-visible filter can contain.

    Enum values are canonical domain concepts. Entity values are mentions that
    a capability resolver must turn into dataset identity before execution.
    Text is deliberately reserved for genuinely open-ended search language.
    """

    kind: FilterKind
    values: frozenset[str] = frozenset()
    entity: str | None = None
    aliases: Mapping[str, str] = field(default_factory=dict)
    description: str = ""
    multiple: bool = False

    def __post_init__(self) -> None:
        if self.kind is FilterKind.ENUM and not self.values:
            raise ValueError("an enum filter needs at least one value")
        if self.kind is FilterKind.ENTITY and not self.entity:
            raise ValueError("an entity filter needs an entity kind")
        if self.kind is not FilterKind.ENUM and (self.values or self.aliases):
            raise ValueError("only enum filters may declare values or aliases")
        if self.multiple and self.kind is not FilterKind.ENUM:
            raise ValueError("only enum filters may accept several values")
        canonical = {_key(value): value for value in self.values}
        for alias, value in self.aliases.items():
            if value not in self.values:
                raise ValueError(f"enum alias {alias!r} points to unknown value {value!r}")
            canonical[_key(alias)] = value
        object.__setattr__(self, "aliases", MappingProxyType(canonical))

    def enum_value(self, value: str) -> str | None:
        """The canonical value for a mention, or None if this filter cannot take it.

        A `multiple` filter accepts several values separated by commas and
        answers with them joined the same way, because a question may name more
        than one and dropping the rest is not a smaller answer — it is a
        different one. "Breakfast and dinner" reached a scalar `meal` as
        `breakfast` alone, and the day it was asked about served no breakfast
        at all, so a menu with forty-three dinner items answered "nothing
        matched". Duplicates collapse and order is kept, so the filter reads
        back the way it was asked.
        """
        if self.kind is not FilterKind.ENUM:
            return None
        mentions = [part.strip() for part in value.split(",") if part.strip()]
        if not mentions or (len(mentions) > 1 and not self.multiple):
            return None
        canonical: list[str] = []
        for mention in mentions:
            resolved = self.aliases.get(_key(mention))
            if resolved is None:
                return None
            if resolved not in canonical:
                canonical.append(resolved)
        return ",".join(canonical)

    def catalogue(self, name: str) -> dict[str, Any]:
        out: dict[str, Any] = {"field": name, "type": self.kind.value}
        if self.values:
            out["values"] = sorted(self.values)
        if self.multiple:
            out["multiple"] = True
        if self.entity:
            out["entity"] = self.entity
        if self.description:
            out["description"] = self.description
        return out


def enum(
    *values: str,
    aliases: dict[str, str] | None = None,
    description: str = "",
    multiple: bool = False,
) -> FilterSpec:
    return FilterSpec(
        FilterKind.ENUM,
        frozenset(values),
        aliases=aliases or {},
        description=description,
        multiple=multiple,
    )


def entity(kind: str) -> FilterSpec:
    return FilterSpec(FilterKind.ENTITY, entity=kind)


def date() -> FilterSpec:
    return FilterSpec(FilterKind.DATE)


def instant() -> FilterSpec:
    return FilterSpec(FilterKind.INSTANT)


def text() -> FilterSpec:
    return FilterSpec(FilterKind.TEXT)
