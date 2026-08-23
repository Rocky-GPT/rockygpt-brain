"""Typed shapes for campus-data responses, parsed strictly.

The data service is a separate, network-reachable system, not a fully
trusted internal component. Every `from_json` here validates an *exact*
shape (required keys present, no unexpected keys where the contract says
`additionalProperties: false`, exact expected types) against
spec/data-api.openapi.yaml, and raises `DataContractError` — never a bare
`KeyError`/`TypeError`/`ValueError` — the moment the input doesn't match.
That keeps a malformed or compromised upstream response from ever reaching
business logic as if it were trustworthy structured data; it fails the
individual data-service call, not the process.

`normalize_source` is a second, separate validation pass specifically for
`Source` (the part of every record that becomes a user-visible citation).
It is the single place a raw, contract-valid `Source` is checked before it
can become model-visible or citable — `brain/tools.py` (building the
model-visible `sourceId`) and `brain/grounding.py` (the citation-provenance
registry) both call this same function on the same input, so a source that
tools.py exposes to the model is *exactly* the source grounding.py will
register under that id: there is no second, independently-tuned
normalization step that could disagree.

`normalize_source` rejects outright (never repairs/truncates):
- an empty `sourceId`, `title`, or `url` (checked before any trimming);
- a control or Unicode *format* character (category `Cc`/`Cf`/`Cs`/`Co` —
  covers C0/C1 controls as well as spoofing-relevant invisible/bidi-override
  characters like U+202E) anywhere in the *original*, untrimmed `sourceId`,
  `title`, or `url`. Checking before trimming matters: `str.strip()` removes
  some control characters (e.g. `\\x0b`, `\\x0c`, tab, CR/LF) as whitespace,
  so checking only the post-strip string would silently repair and accept
  exactly the input this rule exists to reject;
- surrounding whitespace on `sourceId` or `url` — these are identity-bearing
  (used as a lookup key / a link target), so they are rejected rather than
  silently trimmed, which could otherwise collide two different ids or
  change what a URL points to;
- a `sourceId` or `url` over its schema length limit — clipping either would
  create a citable id different from the one the model saw, or a URL
  pointing somewhere the source never intended;
- a `url` that isn't an absolute `http`/`https` URL (blocks `javascript:`,
  `data:`, `file:`, relative paths, etc. from ever becoming a citation link).

`title` is the one field safely trimmed and length-bounded on overflow
*after* the control/format-character check above: it is display text, not
an identifier or a link, so bounding it cannot create a different citable
identity or a malformed/dangerous URL.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from rockygpt_brain.data_client.errors import DataContractError

MAX_SOURCE_ID_LENGTH = 256
MAX_TITLE_LENGTH = 500
MAX_URL_LENGTH = 2048
MAX_DATASET_ID_LENGTH = 256
MAX_DATASET_VERSION_LENGTH = 256
MAX_DATETIME_STRING_LENGTH = 64
MAX_PHONE_LENGTH = 64

_ALLOWED_URL_SCHEMES = frozenset({"http", "https"})
_CONTROL_OR_FORMAT_CATEGORIES = ("Cc", "Cf", "Cs", "Co")

_SOURCE_REQUIRED_KEYS = frozenset({"sourceId", "title", "url"})
_SOURCE_ALLOWED_KEYS = _SOURCE_REQUIRED_KEYS | {"collectedAt"}
_DATASET_KEYS = frozenset({"id", "version", "activatedAt"})
_SEARCH_RESPONSE_KEYS = frozenset({"dataset", "records"})
_SAFETY_RESOURCES_KEYS = frozenset({"dataset", "emergencyPhone", "sources"})
_SAFETY_SOURCES_KEYS = frozenset({"safety", "counseling"})


def _require_object(data: Any, *, what: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise DataContractError(f"{what} must be a JSON object.")
    return data


def _require_exact_keys(data: dict[str, Any], keys: frozenset[str], *, what: str) -> None:
    if data.keys() != keys:
        raise DataContractError(f"{what} has missing or unexpected fields.")


def _require_keys_subset(
    data: dict[str, Any], *, required: frozenset[str], allowed: frozenset[str], what: str
) -> None:
    if not required <= data.keys() or not data.keys() <= allowed:
        raise DataContractError(f"{what} has missing or unexpected fields.")


def _require_str(value: Any, *, field: str, max_length: int) -> str:
    if not isinstance(value, str) or len(value) > max_length:
        raise DataContractError(f"{field} must be a bounded string.")
    return value


def _parse_optional_datetime(value: Any, *, field: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > MAX_DATETIME_STRING_LENGTH:
        raise DataContractError(f"{field} must be a bounded ISO-8601 datetime string.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise DataContractError(f"{field} is not a valid ISO-8601 datetime.") from exc
    if parsed.tzinfo is None:
        raise DataContractError(f"{field} must include an explicit timezone.")
    return parsed


def _has_control_or_format_chars(text: str) -> bool:
    return any(unicodedata.category(ch) in _CONTROL_OR_FORMAT_CATEGORIES for ch in text)


@dataclass(frozen=True, slots=True)
class Source:
    source_id: str
    title: str
    url: str
    collected_at: datetime | None = None

    @classmethod
    def from_json(cls, data: Any) -> Source:
        obj = _require_object(data, what="Source")
        _require_keys_subset(
            obj, required=_SOURCE_REQUIRED_KEYS, allowed=_SOURCE_ALLOWED_KEYS, what="Source"
        )
        return cls(
            source_id=_require_str(
                obj["sourceId"], field="sourceId", max_length=MAX_SOURCE_ID_LENGTH
            ),
            title=_require_str(obj["title"], field="title", max_length=MAX_TITLE_LENGTH),
            url=_require_str(obj["url"], field="url", max_length=MAX_URL_LENGTH),
            collected_at=_parse_optional_datetime(obj.get("collectedAt"), field="collectedAt"),
        )


def normalize_source(source: Source) -> Source | None:
    """Validate a contract-valid `Source`, or reject it (return None) —
    never repair it by trimming/truncating an identifier or URL. See the
    module docstring for the exact rejection rules."""
    source_id = source.source_id
    title = source.title
    url = source.url

    if not source_id or not title or not url:
        return None

    if (
        _has_control_or_format_chars(source_id)
        or _has_control_or_format_chars(title)
        or _has_control_or_format_chars(url)
    ):
        return None

    if source_id != source_id.strip() or url != url.strip():
        return None

    if len(source_id) > MAX_SOURCE_ID_LENGTH or len(url) > MAX_URL_LENGTH:
        return None

    parsed = urlsplit(url)
    if parsed.scheme.lower() not in _ALLOWED_URL_SCHEMES or not parsed.netloc:
        return None

    trimmed_title = title.strip()
    if not trimmed_title:
        return None

    return Source(
        source_id=source_id,
        title=trimmed_title[:MAX_TITLE_LENGTH],
        url=url,
        collected_at=source.collected_at,
    )


@dataclass(frozen=True, slots=True)
class Dataset:
    id: str
    version: str
    activated_at: datetime

    @classmethod
    def from_json(cls, data: Any) -> Dataset:
        obj = _require_object(data, what="Dataset")
        _require_exact_keys(obj, _DATASET_KEYS, what="Dataset")
        activated_at = _parse_optional_datetime(obj["activatedAt"], field="activatedAt")
        if activated_at is None:
            raise DataContractError("Dataset.activatedAt is required.")
        return cls(
            id=_require_str(obj["id"], field="id", max_length=MAX_DATASET_ID_LENGTH),
            version=_require_str(
                obj["version"], field="version", max_length=MAX_DATASET_VERSION_LENGTH
            ),
            activated_at=activated_at,
        )


@dataclass(frozen=True, slots=True)
class SearchResult:
    dataset: Dataset
    records: list[dict[str, Any]]

    @classmethod
    def from_json(cls, data: Any) -> SearchResult:
        obj = _require_object(data, what="Search response")
        _require_exact_keys(obj, _SEARCH_RESPONSE_KEYS, what="Search response")
        dataset = Dataset.from_json(obj["dataset"])
        records_raw = obj["records"]
        if not isinstance(records_raw, list):
            raise DataContractError("Search response records must be an array.")
        records: list[dict[str, Any]] = []
        for record in records_raw:
            records.append(_require_object(record, what="Search response record"))
        return cls(dataset=dataset, records=records)


@dataclass(frozen=True, slots=True)
class SafetyResources:
    dataset: Dataset
    emergency_phone: str | None
    safety_source: Source
    counseling_source: Source

    @classmethod
    def from_json(cls, data: Any) -> SafetyResources:
        obj = _require_object(data, what="Safety resources response")
        _require_exact_keys(obj, _SAFETY_RESOURCES_KEYS, what="Safety resources response")
        dataset = Dataset.from_json(obj["dataset"])

        emergency_phone_raw = obj["emergencyPhone"]
        emergency_phone: str | None = None
        if emergency_phone_raw is not None:
            emergency_phone = _require_str(
                emergency_phone_raw, field="emergencyPhone", max_length=MAX_PHONE_LENGTH
            )

        sources = _require_object(obj["sources"], what="Safety resources sources")
        _require_exact_keys(sources, _SAFETY_SOURCES_KEYS, what="Safety resources sources")

        return cls(
            dataset=dataset,
            emergency_phone=emergency_phone,
            safety_source=Source.from_json(sources["safety"]),
            counseling_source=Source.from_json(sources["counseling"]),
        )
