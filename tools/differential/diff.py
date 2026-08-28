"""Comparing two captures, and deciding which differences would change an answer.

Not every difference matters, and a harness that says so is one nobody reads.
Three severities, and each is a different decision:

  BLOCKING  the answer changes. A record present on one side only, a lookup
            that failed on one side, or a field the capability publishes
            holding a different value. Do not cut this method over.
  WARN      the records match but the order does not. The transportation
            query fetches 100 and stops, so a reordering above that limit
            silently swaps which trips reach the answer. Below it, sorting may
            or may not settle the difference — `Ordering` is set only where a
            sort actually ran, so an unsorted capability hands the model the
            port's order and a reordering is visible in the reply.
  INFO      a field the registry does not publish. It differs, and nothing
            downstream can read it.

The published set is `Capability.read` from the registry rather than a list
kept here. A second list would be a second source of truth about what reaches
an answer, and it would be the one that went stale.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from tools.differential.capture import Capture, canonical

from rockygpt_brain.capabilities.registry import capability_for

Record = dict[str, Any]


class Severity(StrEnum):
    BLOCKING = "BLOCKING"
    WARN = "WARN"
    INFO = "INFO"


@dataclass(frozen=True)
class Divergence:
    case: str
    covers: str
    locus: str
    kind: str
    severity: Severity
    detail: str

    def line(self) -> str:
        return f"{self.severity.value:<8} {self.case}  {self.locus}  {self.kind}: {self.detail}"


def published(capability: str) -> dict[str, Any]:
    entry = capability_for(capability)
    return dict(entry.read) if entry is not None else {}


def project(record: Record, readers: dict[str, Any]) -> dict[str, Any]:
    """The record as the capability publishes it — what actually reaches BRAIN #3."""
    out: dict[str, Any] = {}
    for name, read in readers.items():
        try:
            out[name] = read(record)
        except Exception as exc:  # a reader that cannot read this shape is itself the finding
            out[name] = f"<unreadable: {type(exc).__name__}>"
    return out


def _overlap(left: Record, right: Record) -> float:
    """How much of two records is identical, between 0 and 1."""
    keys = set(left) | set(right)
    if not keys:
        return 1.0
    same = sum(1 for k in keys if canonical(left.get(k)) == canonical(right.get(k)))
    return same / len(keys)


# Below this, two records are different records rather than one record that
# drifted. Above it they are the same row and the fields that differ are the
# finding. Pairing on the published projection instead — the first version of
# this — meant any change to a published field broke the pairing and reported
# as one missing record plus one extra, which is precisely the misleading shape
# this function exists to avoid.
_SAME_ROW = 0.5


def _pair_leftovers(
    left: list[Record], right: list[Record]
) -> tuple[list[tuple[Record, Record]], list[Record], list[Record]]:
    """Match unmatched records to their closest counterpart before diffing fields."""
    remaining = list(right)
    pairs: list[tuple[Record, Record]] = []
    only_left: list[Record] = []
    for record in left:
        best, score = None, 0.0
        for candidate in remaining:
            overlap = _overlap(record, candidate)
            if overlap > score:
                best, score = candidate, overlap
        if best is None or score < _SAME_ROW:
            only_left.append(record)
            continue
        remaining.remove(best)
        pairs.append((record, best))
    return pairs, only_left, remaining


def _field_detail(left: Record, right: Record, readers: dict[str, Any]) -> list[str]:
    names = sorted(set(left) | set(right))
    return [
        f"{name}: {canonical(left.get(name))} != {canonical(right.get(name))}"
        for name in names
        if canonical(left.get(name)) != canonical(right.get(name))
    ]


def compare_records(
    case: str,
    covers: str,
    locus: str,
    capability: str,
    left: Sequence[Record] | None,
    right: Sequence[Record] | None,
    left_name: str,
    right_name: str,
) -> list[Divergence]:
    def flag(kind: str, severity: Severity, detail: str) -> Divergence:
        return Divergence(case, covers, locus, kind, severity, detail)

    if left is None and right is None:
        return []
    if left is None or right is None:
        absent, present_name = (left_name, right_name) if left is None else (right_name, left_name)
        return [
            flag(
                "one-sided",
                Severity.BLOCKING,
                f"{absent} returned nothing at all; {present_name} returned records",
            )
        ]

    readers = published(capability)
    left_rows, right_rows = list(left), list(right)
    left_hashes = [canonical(r) for r in left_rows]
    right_hashes = [canonical(r) for r in right_rows]

    if left_hashes == right_hashes:
        return []

    if sorted(left_hashes) == sorted(right_hashes):
        first = next(
            (i for i, (a, b) in enumerate(zip(left_hashes, right_hashes, strict=True)) if a != b),
            0,
        )
        return [
            flag(
                "reordered",
                Severity.WARN,
                f"same {len(left_rows)} records, first differing position {first}",
            )
        ]

    shared = set(left_hashes) & set(right_hashes)
    pairs, only_left, only_right = _pair_leftovers(
        [r for r, h in zip(left_rows, left_hashes, strict=True) if h not in shared],
        [r for r, h in zip(right_rows, right_hashes, strict=True) if h not in shared],
    )

    found: list[Divergence] = []
    if only_left:
        found.append(
            flag(
                "missing",
                Severity.BLOCKING,
                f"{len(only_left)} record(s) only {left_name} returned, "
                f"e.g. {canonical(project(only_left[0], readers))[:200]}",
            )
        )
    if only_right:
        found.append(
            flag(
                "extra",
                Severity.BLOCKING,
                f"{len(only_right)} record(s) only {right_name} returned, "
                f"e.g. {canonical(project(only_right[0], readers))[:200]}",
            )
        )

    for left_row, right_row in pairs:
        raw = _field_detail(left_row, right_row, readers)
        shown = _field_detail(project(left_row, readers), project(right_row, readers), readers)
        if shown:
            found.append(
                flag(
                    "field-drift",
                    Severity.BLOCKING,
                    f"published field(s) differ — {'; '.join(shown[:4])}",
                )
            )
        elif raw:
            found.append(
                flag(
                    "internal-drift",
                    Severity.INFO,
                    f"unpublished field(s) differ — {'; '.join(raw[:4])}",
                )
            )
    return found


def compare(left: Capture, right: Capture, left_name: str, right_name: str) -> list[Divergence]:
    """Every way one case's two captures disagree."""
    covers = left.covers or right.covers
    case = left.case or right.case
    capability = left.capability or right.capability

    def flag(locus: str, kind: str, severity: Severity, detail: str) -> Divergence:
        return Divergence(case, covers, locus, kind, severity, detail)

    found: list[Divergence] = []

    if left.failure != right.failure:
        found.append(
            flag(
                "case",
                "failure",
                Severity.BLOCKING,
                f"{left_name}={left.failure or 'ok'} / {right_name}={right.failure or 'ok'}",
            )
        )

    # A key is a method plus the exact query it was called with, so a key on one
    # side only means the two implementations asked campus data different
    # questions. That is normalize diverging, upstream of any record.
    left_keys, right_keys = set(left.port_calls), set(right.port_calls)
    for key in sorted(left_keys - right_keys):
        found.append(flag(key, "unasked", Severity.BLOCKING, f"only {left_name} made this call"))
    for key in sorted(right_keys - left_keys):
        found.append(flag(key, "unasked", Severity.BLOCKING, f"only {right_name} made this call"))

    for key in sorted(left_keys & right_keys):
        a, b = left.port_calls[key], right.port_calls[key]
        if a.error != b.error:
            found.append(
                flag(
                    key,
                    "call-error",
                    Severity.BLOCKING,
                    f"{left_name}={a.error or 'ok'} / {right_name}={b.error or 'ok'}",
                )
            )
            continue
        found.extend(
            compare_records(
                case, covers, key, capability, a.records, b.records, left_name, right_name
            )
        )

    found.extend(
        compare_records(
            case, covers, "output", capability, left.output, right.output, left_name, right_name
        )
    )
    return found


def worst(found: Sequence[Divergence]) -> Severity | None:
    for severity in (Severity.BLOCKING, Severity.WARN, Severity.INFO):
        if any(d.severity is severity for d in found):
            return severity
    return None
