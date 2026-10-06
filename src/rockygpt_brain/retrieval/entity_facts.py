"""Canonical office facts, preserving every linked observation and its boundaries.

Discovery is deliberately separate from fact authority. Names locate canonical
identities; only exact published identity links select the evidence records.
"""

import hashlib
import json
import math
import re
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

from rockygpt_brain.retrieval.projection import (
    CONTACT_FIELDS,
    FIELD_ALIASES,
    OFFICE_FIELDS,
    clean,
    project_contact,
)

MAX_ENTITIES = 5_000
MAX_CONTACTS = 128
MAX_SCHEDULE_ROWS = 128
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MAX_EVIDENCE_BYTES = 128_000
CONTACT_OBSERVATION_ARTIFACT = "development-office-contact-evidence"
OBSERVED_FIELDS = frozenset({"email", "phones", "offices"})
MAX_OBSERVATION_PAGES = 16
CAMPUS_TIMEZONE = ZoneInfo("America/New_York")
_DISCOVERY_FILLER = frozenset({"a", "an", "the", "of", "for", "and", "office", "offices"})


def _discovery_tokens(value: str) -> frozenset[str]:
    """Compare published name words only; no stemming, synonyms, or identity merging."""
    possessive_free = re.sub(r"(?<=\w)['’]s\b", "", value.casefold())
    return frozenset(re.findall(r"[^\W_]+", possessive_free)) - _DISCOVERY_FILLER


class EvidenceUnavailable(RuntimeError):
    """Published evidence cannot be read completely and safely."""


class DatasetChanged(EvidenceUnavailable):
    """The caller's release pin no longer names the active publication."""


class UnknownEntity(ValueError):
    """The requested office does not exist in this publication."""


class InvalidFactRequest(ValueError):
    """The request names unsupported fields or exceeds a bounded input."""


@dataclass(frozen=True)
class Snapshot:
    dataset_version: str
    identity_hash: str
    entities: list[dict[str, Any]]
    contact_reader: Callable[[dict[str, Any]], list[dict[str, Any]]]
    alias_sources: list[dict[str, Any]]
    # Linked schedule records (one per weekday). An adapter with none reads nothing.
    schedule_reader: Callable[[dict[str, Any]], list[dict[str, Any]]] = lambda entity: []


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _instant(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            return None
    return None


def _date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _json_safe(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise EvidenceUnavailable("Published values must be finite JSON numbers.")
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise EvidenceUnavailable("Unsupported published value type.")


def _present(value: Any) -> bool:
    # False and zero are observations, not missing values.
    return value is not None and value != "" and value != [] and value != {}


def _url(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 4_096:
        return None
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or any(character.isspace() or ord(character) < 32 for character in value)
        or any(ord(character) < 32 for character in unquote(value))
        or "\\" in value
    ):
        return None
    return value


def _source(row: dict[str, Any], now: datetime) -> dict[str, Any]:
    captured = _instant(row.get("collected_at"))
    since, until = _date(row.get("valid_from")), _date(row.get("valid_until"))
    for key, parsed in (("valid_from", since), ("valid_until", until)):
        if row.get(key) is not None and parsed is None:
            raise EvidenceUnavailable("Invalid published validity boundary.")
    if since and until and since > until:
        raise EvidenceUnavailable("Invalid published validity interval.")
    freshness = "unknown"
    sla = row.get("freshness_sla_hours")
    if captured and isinstance(sla, int) and not isinstance(sla, bool) and sla > 0:
        freshness = "fresh" if captured <= now <= captured + timedelta(hours=sla) else "stale"
    today = now.astimezone(CAMPUS_TIMEZONE).date()
    validity = "unspecified"
    if since or until:
        validity = (
            "future"
            if since and today < since
            else ("expired" if until and today > until else "current")
        )
    metadata = row.get("normalization_metadata") or {}
    evidence = metadata.get("evidence", {}) if isinstance(metadata, dict) else {}
    urls = evidence.get("source_urls", []) if isinstance(evidence, dict) else []
    if not isinstance(urls, list):
        raise EvidenceUnavailable("Invalid citation provenance.")
    primary = _url(row.get("canonical_url"))
    citations = list(dict.fromkeys(url for value in urls if (url := _url(value))))
    if not citations and primary:
        citations = [primary]
    caveats = []
    if not captured:
        caveats.append("No usable capture time is published.")
    if not since and not until:
        caveats.append("The source publishes no validity interval.")
    if not citations:
        caveats.append("The original record has no usable citation URL.")
    if freshness != "fresh":
        caveats.append(f"Source freshness is {freshness}; this does not establish current facts.")
    if isinstance(evidence, dict) and evidence.get("withheld"):
        caveats.append("The publisher withheld unsupported contact values; see metadata.")
    return {
        "id": row["id"],
        "collection": "contacts",
        "source_key": row["source_key"],
        "source_record_key": row["source_record_key"],
        "url": primary,
        "citation_urls": citations,
        "collected_at": captured.isoformat() if captured else None,
        "valid_from": since.isoformat() if since else None,
        "valid_until": until.isoformat() if until else None,
        "freshness": freshness,
        "validity": validity,
        "freshness_sla_hours": sla,
        "content_hash": row.get("content_hash"),
        "normalization_metadata": _json_safe(metadata),
        "caveats": caveats,
    }


def _disjoint(left: dict[str, Any], right: dict[str, Any]) -> bool:
    # Explicit complete intervals only. Unknown boundaries never establish separation.
    a, b = left.get("valid_from"), left.get("valid_until")
    c, d = right.get("valid_from"), right.get("valid_until")
    return bool(a and b and c and d and (b < c or d < a))


def _bounded_observation_text(value: Any, maximum: int) -> bool:
    return (isinstance(value, str) and bool(value.strip()) and len(value) <= maximum
            and not any(ord(character) < 32 for character in value))


def _observation_time(value: Any, now: datetime) -> datetime:
    if not _bounded_observation_text(value, 64):
        raise EvidenceUnavailable("Invalid field-observation time.")
    try:
        captured = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise EvidenceUnavailable("Invalid field-observation time.") from error
    if captured.tzinfo is None or captured.utcoffset() is None or captured > now:
        raise EvidenceUnavailable("Field-observation times must be aware and not in the future.")
    return captured


def _observation_hash(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _projection_hash(row: dict[str, Any], field: str) -> str:
    if field == "email":
        raw = row.get("email")
    elif field in {"phones", "offices"}:
        singular = {"phones": "phone", "offices": "office"}[field]
        raw = {singular: row.get(singular), field: row.get(field)}
    else:
        raise EvidenceUnavailable("Unsupported field-observation projection.")

    def valid(value: Any, depth: int = 0) -> bool:
        if depth > 12:
            return False
        if value is None or isinstance(value, str):
            return True
        if isinstance(value, list):
            return all(valid(item, depth + 1) for item in value)
        if isinstance(value, dict):
            return all(isinstance(key, str) and valid(item, depth + 1)
                       for key, item in value.items())
        return False  # Numbers and booleans are not contact-observation representations.

    if not valid(raw):
        raise EvidenceUnavailable("Invalid field-observation raw projection.")
    encoded = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _observation_sources(row: dict[str, Any], original: dict[str, Any],
                         now: datetime) -> list[dict[str, Any]]:
    """Refresh only a fully observed projection, retaining the original record source.

    The hash-only database join supplies the trusted same-release artifact hash.
    The publisher verifies page support; this reader validates its bounded metadata
    and binding to the complete raw projection, without fetching or interpreting pages.
    """
    metadata = row.get("normalization_metadata")
    if not isinstance(metadata, dict) or "contact_observations" not in metadata:
        return []
    observation = metadata["contact_observations"]
    required = {"schema_version", "artifact_key", "artifact_hash", "base_version", "fields"}
    if (not isinstance(observation, dict) or set(observation) != required
            or type(observation["schema_version"]) is not int or observation["schema_version"] != 1
            or observation["artifact_key"] != CONTACT_OBSERVATION_ARTIFACT
            or not _observation_hash(observation["artifact_hash"])
            or observation["artifact_hash"] != row.get("contact_observation_artifact_hash")
            or not _bounded_observation_text(observation["base_version"], 256)):
        raise EvidenceUnavailable("Invalid or unbound field-observation metadata.")
    fields = observation["fields"]
    if not isinstance(fields, dict) or not fields or not set(fields) <= OBSERVED_FIELDS:
        raise EvidenceUnavailable("Unsupported field-observation projection.")
    derived = []
    for field, details in fields.items():
        if (not isinstance(details, dict)
                or set(details) != {"captured_at", "value_sha256", "pages"}
                or not _observation_hash(details["value_sha256"])
                or details["value_sha256"] != _projection_hash(row, field)
                or not _present(project_contact(row, field)[0])):
            raise EvidenceUnavailable("Field observation does not match the complete raw value.")
        captured = _observation_time(details["captured_at"], now)
        pages = details["pages"]
        if not isinstance(pages, list) or not 1 <= len(pages) <= MAX_OBSERVATION_PAGES:
            raise EvidenceUnavailable("Invalid field-observation pages.")
        instants = []
        urls = []
        for page in pages:
            page_keys = {"url", "section", "fetched_at", "html_sha256"}
            if (not isinstance(page, dict) or not page_keys <= set(page)
                    or not set(page) <= page_keys | {"near"} or _url(page["url"]) is None
                    or not _bounded_observation_text(page["section"], 1_000)
                    or ("near" in page and not _bounded_observation_text(page["near"], 2_000))
                    or not _observation_hash(page["html_sha256"])):
                raise EvidenceUnavailable("Invalid field-observation page provenance.")
            urls.append(page["url"])
            instants.append(_observation_time(page["fetched_at"], now))
        if captured != min(instants):
            raise EvidenceUnavailable("Field-observation capture must be its oldest cited page.")
        original_capture = _instant(row.get("collected_at"))
        if original_capture is not None and captured <= original_capture:
            continue  # The record itself is at least as recent; an older look must not demote it.
        source = _source({
            **row, "id": f"{row['id']}:contact_observation:{field}", "collected_at": captured,
            "canonical_url": urls[0], "normalization_metadata": {"evidence": {"source_urls": urls}},
        }, now)
        source.update(
            original_record_id=row["id"], observation_field=field,
            original_collected_at=original["collected_at"],
            original_freshness=original["freshness"], original_caveats=list(original["caveats"]),
            normalization_metadata={"contact_observation": {
                key: observation[key] for key in ("artifact_key", "artifact_hash", "base_version")
            } | {"value_sha256": details["value_sha256"], "pages": deepcopy(pages)}},
        )
        source["caveats"].append(
            f"Only {field} was re-observed; the original record capture "
            f"({original['collected_at'] or 'unknown'}) and other fields remain unchanged."
        )
        derived.append(source)
    return derived


def canonical_properties(
    rows: Sequence[dict[str, Any]],
    fields: Sequence[str],
    sources: Sequence[dict[str, Any]],
    *,
    registry_name: str = "",
    reviewed_aliases: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """Resolve observations without voting, preferring a record, or inventing support."""
    if not all(isinstance(item.get("id"), str) and item["id"] for item in [*rows, *sources]):
        raise EvidenceUnavailable("Every fact needs an original evidence identifier.")
    source_map = {source["id"]: source for source in sources}
    row_ids = {row["id"] for row in rows}
    if (len(source_map) != len(sources) or len(row_ids) != len(rows)
            or not row_ids <= source_map.keys()):
        raise EvidenceUnavailable("Every fact needs exactly one original evidence record.")
    field_sources: dict[tuple[str, str], str] = {}
    for sid, source in source_map.items():
        if sid in row_ids:
            continue
        original_id = source.get("original_record_id")
        observed_field = source.get("observation_field")
        if (not isinstance(original_id, str) or original_id not in row_ids
                or not isinstance(observed_field, str) or observed_field not in OBSERVED_FIELDS
                or sid != f"{original_id}:contact_observation:{observed_field}"):
            raise EvidenceUnavailable("A derived source needs its original record and exact field.")
        field_sources[(original_id, observed_field)] = sid
    canonical_name_published = any(clean(row.get("name")) == registry_name for row in rows)
    properties = []
    for field in fields:
        assertions: list[dict[str, Any]] = []
        groups: dict[str, dict[str, Any]] = {}
        for row in rows:
            value, raw, caveats = project_contact(row, field)
            if field == "name" and canonical_name_published and value in reviewed_aliases:
                caveats.append(f"Published name is a reviewed alias of {registry_name}.")
                value = registry_name
            assertion_id = f"{row['id']}:{field}"
            source_id = field_sources.get((row["id"], field), row["id"])
            assertion = {
                "id": assertion_id,
                "source_id": source_id,
                "field": field,
                "raw_value": _json_safe(raw),
                "value": _json_safe(value),
                "caveats": caveats,
            }
            assertions.append(assertion)
            if _present(value):
                # JSON keeps true, 1 and "1" distinct, unlike Python equality.
                key = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
                group = groups.setdefault(
                    key,
                    {
                        "value": _json_safe(value),
                        "assertion_ids": [],
                        "source_ids": [],
                    },
                )
                group["assertion_ids"].append(assertion_id)
                if source_id not in group["source_ids"]:
                    group["source_ids"].append(source_id)
        values = list(groups.values())
        status = "unknown" if not values else "known"
        if len(values) > 1:
            distinct_pairs = (
                (left, right) for i, left in enumerate(values) for right in values[i + 1 :]
            )
            separate = all(
                _disjoint(source_map[a], source_map[b])
                for left, right in distinct_pairs
                for a in left["source_ids"]
                for b in right["source_ids"]
            )
            status = "multiple" if separate else "conflicting"
        properties.append(
            {
                "key": field,
                "label": field.replace("_", " ").capitalize(),
                "category": "identity" if field in {"name", "department"} else "contact",
                "status": status,
                "values": values,
                "assertions": assertions,
            }
        )
    return properties


def validate_entities(entities: Any) -> list[dict[str, Any]]:
    if not isinstance(entities, list) or len(entities) > MAX_ENTITIES:
        raise EvidenceUnavailable("Invalid or oversized identity registry.")
    ids: set[str] = set()
    owners: dict[tuple[str, str], list[set[str] | None]] = {}
    for entity in entities:
        if not isinstance(entity, dict) or not all(
            isinstance(entity.get(key), str) and entity[key] for key in ("id", "name", "kind")
        ):
            raise EvidenceUnavailable("Invalid canonical identity.")
        if entity["id"] in ids:
            raise EvidenceUnavailable("Duplicate canonical identity.")
        ids.add(entity["id"])
        aliases = entity.get("aliases")
        if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
            raise EvidenceUnavailable("Invalid canonical aliases.")
        links = entity.get("links")
        if not isinstance(links, list) or len(links) > 32:
            raise EvidenceUnavailable("Invalid canonical links.")
        for link in links:
            if not isinstance(link, dict):
                raise EvidenceUnavailable("Invalid canonical link.")
            if link.get("collection") == "campus_hours":
                # A schedule belongs to its office by exact record key, like a contact does. Two
                # entities may name the same schedule, so no single-owner rule applies here.
                schedule_keys = link.get("source_record_keys")
                if (not isinstance(schedule_keys, list)
                        or not all(isinstance(k, str) for k in schedule_keys)
                        or len(schedule_keys) > MAX_SCHEDULE_ROWS
                        or not _text(link.get("source_key"))):
                    raise EvidenceUnavailable("Invalid schedule identity link.")
                continue
            if link.get("collection") != "contacts":
                continue
            keys, pinned = link.get("source_record_keys"), link.get("source_record_ids")
            if (
                not isinstance(keys, list)
                or not all(isinstance(k, str) for k in keys)
                or len(keys) > MAX_CONTACTS
                or not _text(link.get("source_key"))
            ):
                raise EvidenceUnavailable("Invalid contact identity link.")
            if pinned is not None and (
                not isinstance(pinned, list)
                or not pinned
                or not all(isinstance(p, str) for p in pinned)
            ):
                raise EvidenceUnavailable("Invalid contact row pins.")
            pin_set = set(pinned) if pinned else None
            for key in keys:
                ownership = owners.setdefault((link["source_key"], key), [])
                if any(
                    prior is None or pin_set is None or bool(prior & pin_set) for prior in ownership
                ):
                    raise EvidenceUnavailable("Contact evidence has multiple canonical owners.")
                ownership.append(pin_set)
    return entities


def linked_contacts(entity: dict[str, Any], rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    links = [link for link in entity["links"] if link.get("collection") == "contacts"]
    return [
        row
        for row in rows
        if any(
            row.get("source_key") == link["source_key"]
            and row.get("source_record_key") in link["source_record_keys"]
            and (not link.get("source_record_ids") or row.get("id") in link["source_record_ids"])
            for link in links
        )
    ]


def linked_schedules(
    entity: dict[str, Any], rows: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    links = [link for link in entity["links"] if link.get("collection") == "campus_hours"]
    return [
        row
        for row in rows
        if any(
            row.get("source_key") == link["source_key"]
            and row.get("source_record_key") in link["source_record_keys"]
            for link in links
        )
    ]


def _day_order(day: str) -> tuple[int, str]:
    return (WEEKDAYS.index(day), day) if day in WEEKDAYS else (len(WEEKDAYS), day)


def schedule_property(
    rows: Sequence[dict[str, Any]], now: datetime
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The office's published schedules as one `hours` property, plus the sources behind it.

    One value is one named schedule for one published validity window: its weekdays, exactly as
    published, and the official sentence it was read from. The days of a schedule are never
    values of their own (Monday and Tuesday do not disagree). Two schedules disagree only when
    they have the same name, overlapping validity, and different content. Different names (a
    library's circulation desk and its research desk) are separate answers.
    """
    buckets: dict[tuple[str, str | None, str | None], list[dict[str, Any]]] = {}
    for row in rows:
        name, day, text = clean(row.get("name")), clean(row.get("day")), clean(row.get("schedule"))
        if not all(isinstance(part, str) and part for part in (name, day, text)):
            raise EvidenceUnavailable("A schedule record needs a name, a day and its hours.")
        since, until = _date(row.get("valid_from")), _date(row.get("valid_until"))
        buckets.setdefault((name, since.isoformat() if since else None,
                            until.isoformat() if until else None), []).append(row)
    sources: list[dict[str, Any]] = []
    assertions: list[dict[str, Any]] = []
    groups: dict[str, dict[str, Any]] = {}
    ordered = sorted(
        buckets.items(), key=lambda item: (item[0][0], item[0][1] or "", item[0][2] or ""))
    for (name, _, _), bucket in ordered:
        by_day: dict[str, list[dict[str, Any]]] = {}
        for row in sorted(bucket, key=lambda r: (_day_order(clean(r["day"])), str(r.get("id")))):
            by_day.setdefault(clean(row["day"]), []).append(row)
        # Two different texts for one day are a disagreement, so each reading gets its own value.
        readings: dict[str, list[dict[str, Any]]] = {}
        for index in range(max(len(rows_for_day) for rows_for_day in by_day.values())):
            picked = [days[min(index, len(days) - 1)] for days in by_day.values()]
            key = json.dumps([[clean(r["day"]), clean(r["schedule"])] for r in picked])
            readings.setdefault(key, picked)
        for picked in readings.values():
            value: dict[str, Any] = {
                "schedule": name,
                "days": [{"day": clean(r["day"]), "hours": clean(r["schedule"])} for r in picked],
                "notes": list(dict.fromkeys(
                    clean(r["notes"]) for r in picked if _present(r.get("notes")))),
            }
            captured = [t for r in picked if (t := _instant(r.get("collected_at")))]
            urls = list(dict.fromkeys(u for r in picked if (u := _url(r.get("source_url")))))
            source = _source({
                **picked[0], "id": f"{picked[0]['id']}:schedule",
                "collected_at": min(captured) if len(captured) == len(picked) else None,
                "canonical_url": urls[0] if urls else picked[0].get("canonical_url"),
                "normalization_metadata": {"evidence": {"source_urls": urls}},
            }, now)
            source.update(
                collection="campus_hours",
                source_record_keys=[r["source_record_key"] for r in picked],
                record_ids=[r["id"] for r in picked],
                content_hash=hashlib.sha256(
                    "|".join(sorted(str(r.get("content_hash")) for r in picked)).encode()
                ).hexdigest(),
            )
            sources.append(source)
            assertion_id = f"{source['id']}:hours"
            assertions.append({
                "id": assertion_id, "source_id": source["id"], "field": "hours",
                "raw_value": _json_safe([{"day": r["day"], "schedule": r["schedule"],
                                           "notes": r.get("notes")} for r in picked]),
                "value": _json_safe(value), "caveats": [],
            })
            key = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
            group = groups.setdefault(key, {"value": _json_safe(value), "assertion_ids": [],
                                            "source_ids": []})
            group["assertion_ids"].append(assertion_id)
            group["source_ids"].append(source["id"])
    values = list(groups.values())
    status = "known" if values else "unknown"
    by_schedule: dict[str, list[dict[str, Any]]] = {}
    for found in values:
        by_schedule.setdefault(found["value"]["schedule"], []).append(found)
    source_map = {source["id"]: source for source in sources}
    for same_name in by_schedule.values():
        if len(same_name) < 2:
            continue
        separate = all(
            _disjoint(source_map[a], source_map[b])
            for i, left in enumerate(same_name) for right in same_name[i + 1:]
            for a in left["source_ids"] for b in right["source_ids"]
        )
        if not separate:
            status = "conflicting"
        elif status != "conflicting":
            status = "multiple"
    return ({"key": "hours", "label": "Hours", "category": "schedule", "status": status,
             "values": values, "assertions": assertions}, sources)


class EntityFacts:
    """Shared office search and canonical fact reader; adapters supply one snapshot."""

    def __init__(self, *, now: Callable[[], datetime] | None = None) -> None:
        self.now = now or (lambda: datetime.now(UTC))

    @contextmanager
    def snapshot(self) -> Iterator[Snapshot]:
        raise NotImplementedError
        yield  # pragma: no cover

    @staticmethod
    def _pin(snapshot: Snapshot, version: str | None, identity_hash: str | None) -> None:
        if (version is not None and version != snapshot.dataset_version) or (
            identity_hash is not None and identity_hash != snapshot.identity_hash
        ):
            raise DatasetChanged("The active dataset changed; resolve the office again.")

    def readiness(self) -> dict[str, Any]:
        try:
            with self.snapshot() as snapshot:
                offices = [entity for entity in snapshot.entities if entity["kind"] == "office"]
                if not offices:
                    raise EvidenceUnavailable("No published offices.")
                # Verify the contact read path as well as registry availability.
                if not snapshot.contact_reader(offices[0]):
                    raise EvidenceUnavailable("No published office contact evidence.")
                return {
                    "ready": True,
                    "dataset_version": snapshot.dataset_version,
                    "identity_hash": snapshot.identity_hash,
                }
        except EvidenceUnavailable:
            return {"ready": False}

    def list_offices(
        self,
        *,
        dataset_version: str | None = None,
        identity_hash: str | None = None,
        limit: int = 200,
        with_ids: bool = False,
    ) -> dict[str, Any]:
        """Published office names and aliases, so callers choose real names, not guesses."""
        if not 1 <= limit <= 500:
            raise InvalidFactRequest("Office listing limit must be between 1 and 500.")
        with self.snapshot() as snapshot:
            self._pin(snapshot, dataset_version, identity_hash)
            offices = sorted(
                ({"name": entity["name"], "aliases": sorted(set(entity["aliases"])),
                  **({"entity_id": entity["id"]} if with_ids else {})}
                 for entity in snapshot.entities if entity["kind"] == "office"),
                key=lambda office: office["name"],
            )
            return {
                "dataset_version": snapshot.dataset_version,
                "identity_hash": snapshot.identity_hash,
                "offices": offices[:limit],
                "truncated": len(offices) > limit,
            }

    def search_offices(
        self,
        query: str,
        *,
        dataset_version: str | None = None,
        identity_hash: str | None = None,
        limit: int = 8,
    ) -> dict[str, Any]:
        if not isinstance(query, str) or not query.strip() or len(query) > 200:
            raise InvalidFactRequest("Office search needs 1–200 characters.")
        if not 1 <= limit <= 20:
            raise InvalidFactRequest("Office search limit must be between 1 and 20.")
        needle = " ".join(query.casefold().split())
        query_tokens = _discovery_tokens(query)
        with self.snapshot() as snapshot:
            self._pin(snapshot, dataset_version, identity_hash)
            found = []
            for entity in snapshot.entities:
                if entity["kind"] != "office":
                    continue
                names = [
                    " ".join(name.casefold().split())
                    for name in [entity["name"], *entity["aliases"]]
                ]
                exact = needle in names
                name_tokens = [_discovery_tokens(name) for name in names]
                partial = bool(query_tokens) and any(
                    needle in name
                    or (bool(tokens) and (query_tokens <= tokens or tokens <= query_tokens))
                    for name, tokens in zip(names, name_tokens, strict=True)
                )
                if exact or partial:
                    found.append(
                        {
                            "entity_id": entity["id"],
                            "name": entity["name"],
                            "aliases": list(entity["aliases"]),
                            "match": "exact" if exact else "partial",
                        }
                    )
            found.sort(key=lambda item: (item["match"] != "exact", item["name"], item["entity_id"]))
            return {
                "dataset_version": snapshot.dataset_version,
                "identity_hash": snapshot.identity_hash,
                "candidates": found[:limit],
                "truncated": len(found) > limit,
            }

    def get_office_facts(
        self,
        entity_id: str,
        fields: Sequence[str] | None,
        dataset_version: str,
        *,
        identity_hash: str | None = None,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        if fields is None:
            fields = OFFICE_FIELDS
        if (
            not isinstance(entity_id, str)
            or not entity_id
            or len(entity_id) > 100
            or not isinstance(dataset_version, str)
            or not dataset_version
        ):
            raise InvalidFactRequest("A canonical entity and dataset pin are required.")
        if (
            isinstance(fields, str)
            or not fields
            or len(fields) > len(OFFICE_FIELDS)
            or not all(isinstance(field, str) for field in fields)
        ):
            raise InvalidFactRequest("Select a bounded list of office fields.")
        selected = list(dict.fromkeys(FIELD_ALIASES.get(field, field) for field in fields))
        if any(field not in OFFICE_FIELDS for field in selected):
            raise InvalidFactRequest("An unsupported office field was requested.")
        with self.snapshot() as snapshot:
            self._pin(snapshot, dataset_version, identity_hash)
            entity = next(
                (
                    entity
                    for entity in snapshot.entities
                    if entity["id"] == entity_id and entity["kind"] == "office"
                ),
                None,
            )
            if not entity:
                raise UnknownEntity("No office has that canonical identity.")
            rows = snapshot.contact_reader(entity)
            schedule_rows = snapshot.schedule_reader(entity) if "hours" in selected else []
            if len(schedule_rows) > MAX_SCHEDULE_ROWS:
                raise EvidenceUnavailable("Office schedule evidence exceeds the bounded read.")
            if len(linked_schedules(entity, schedule_rows)) != len(schedule_rows):
                raise EvidenceUnavailable("A schedule record has no exact canonical identity link.")
            schedule_ids = [_text(row.get("id")) for row in schedule_rows]
            if not all(schedule_ids) or len(set(schedule_ids)) != len(schedule_ids):
                raise EvidenceUnavailable("Duplicate or missing schedule evidence identifiers.")
            if len(rows) > MAX_CONTACTS:
                raise EvidenceUnavailable("Office evidence exceeds the bounded read.")
            if len(json.dumps(_json_safe([*rows, *schedule_rows]),
                              allow_nan=False).encode()) > MAX_EVIDENCE_BYTES:
                raise EvidenceUnavailable("Office evidence exceeds the bounded response.")
            if len(linked_contacts(entity, rows)) != len(rows):
                raise EvidenceUnavailable("A fact record has no exact canonical identity link.")
            row_ids = [_text(row.get("id")) for row in rows]
            if not all(row_ids) or len(set(row_ids)) != len(row_ids):
                raise EvidenceUnavailable("Duplicate or missing original evidence identifiers.")
            now = as_of if as_of is not None else self.now()
            if now.tzinfo is None:
                raise InvalidFactRequest("The fact clock must include its timezone.")
            sources = []
            for row in rows:
                original = _source(row, now)
                sources.extend([original, *_observation_sources(row, original, now)])
            reviewed = [
                entry["alias"]
                for entry in snapshot.alias_sources
                if entry.get("entity_id") == entity_id
                and isinstance(entry.get("alias"), str)
                and any(
                    source.get("basis") in {"identity_map", "human_reviewed", "department"}
                    for source in entry.get("sources", [])
                    if isinstance(source, dict)
                )
            ]
            properties = canonical_properties(
                rows,
                [field for field in selected if field in CONTACT_FIELDS],
                sources,
                registry_name=entity["name"],
                reviewed_aliases=reviewed,
            )
            if "hours" in selected:
                hours, schedule_sources = schedule_property(schedule_rows, now)
                sources.extend(schedule_sources)
                properties = sorted(
                    [*properties, hours], key=lambda prop: list(selected).index(prop["key"]))
            caveats = []
            for link in entity["links"]:
                if link.get("collection") != "contacts":
                    continue
                for key in link["source_record_keys"]:
                    if not any(
                        row["source_key"] == link["source_key"] and row["source_record_key"] == key
                        for row in rows
                    ):
                        caveats.append(
                            f"Linked contact evidence is missing: {link['source_key']}/{key}."
                        )
                if link.get("source_record_ids") and not set(link["source_record_ids"]) <= set(
                    row_ids
                ):
                    caveats.append("One or more pinned original contact records are missing.")
            if "hours" in selected:
                for link in entity["links"]:
                    if link.get("collection") != "campus_hours":
                        continue
                    for key in link["source_record_keys"]:
                        if not any(
                            row["source_key"] == link["source_key"]
                            and row["source_record_key"] == key
                            for row in schedule_rows
                        ):
                            caveats.append(
                                f"Linked schedule evidence is missing: {link['source_key']}/{key}."
                            )
            if not rows:
                caveats.append("No original contact evidence is published for this office.")
            return {
                "schema_version": 3,
                "mapping_version": "entity-facts-3",
                "dataset_version": snapshot.dataset_version,
                "identity_hash": snapshot.identity_hash,
                "entity": {key: entity[key] for key in ("id", "kind", "name")},
                "properties": properties,
                "sources": sources,
                "evidence_count": len(rows) + len(schedule_rows),
                "caveats": caveats,
                "complete": not caveats,
            }


class MemoryEntityFacts(EntityFacts):
    """Same resolver with explicit published fixtures; never a production fallback."""

    def __init__(
        self,
        *,
        dataset_version: str,
        identity_hash: str,
        entities: list[dict[str, Any]],
        contacts: list[dict[str, Any]],
        alias_sources: list[dict[str, Any]] | None = None,
        schedules: list[dict[str, Any]] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(now=now)
        self.dataset_version = dataset_version
        self.identity_hash = identity_hash
        self.entities = deepcopy(validate_entities(entities))
        self.contacts = deepcopy(contacts)
        self.alias_sources = deepcopy(alias_sources or [])
        self.schedules = deepcopy(schedules or [])

    @contextmanager
    def snapshot(self) -> Iterator[Snapshot]:
        entities, contacts = deepcopy(self.entities), deepcopy(self.contacts)
        schedules = deepcopy(self.schedules)
        yield Snapshot(
            self.dataset_version,
            self.identity_hash,
            validate_entities(entities),
            lambda entity: linked_contacts(entity, contacts),
            deepcopy(self.alias_sources),
            lambda entity: linked_schedules(entity, schedules),
        )
