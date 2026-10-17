"""Canonical office facts, preserving every linked observation and its boundaries.

Discovery is deliberately separate from fact authority. Names locate canonical
identities; only exact published identity links select the evidence records.
"""

import dataclasses
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
MAX_DINING_ROWS = 128
MAX_MENU_ROWS = 1_500
MAX_TRIP_ROWS = 128
# The record collections a derived copy of a release carries beside contacts and campus hours,
# with the most rows one entity may link to.
RECORD_COLLECTIONS = {
    "dining_hours": MAX_DINING_ROWS, "menu": MAX_MENU_ROWS, "shuttle_trips": MAX_TRIP_ROWS,
    "menu_documents": 10, "menu_listings": 2, "shuttle_stops": 40, "campus_topics": 1,
}
# Entities a student can ask about by name: offices and dining venues. A route is not named by
# students; it is read through the shuttle timetable.
NAMED_KINDS = ("office", "venue")
# An Archway group (a residence hall desk, a center) or a campus facility that a reviewed
# directory contact belongs to is read like an office: it has the same contact and hours records.
DESK_KINDS = ("club", "organization", "facility")
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MAX_EVIDENCE_BYTES = 128_000
CONTACT_OBSERVATION_ARTIFACT = "development-office-contact-evidence"
OBSERVED_FIELDS = frozenset({"email", "phones", "offices"})
# The office fields a reviewed "not published" claim can cover, and the publisher's name for each.
ABSENCE_FIELDS = {"email": "email", "phones": "phone", "offices": "office", "hours": "hours",
                  "prefers_email": "prefers_email", "preferred_contact": "preferred_contact",
                  "contact_note": "contact_note", "department": "department", "website": "website"}
MAX_OBSERVATION_PAGES = 16
CAMPUS_TIMEZONE = ZoneInfo("America/New_York")
_DISCOVERY_FILLER = frozenset({"a", "an", "the", "of", "for", "and", "office", "offices"})


def _discovery_tokens(value: str) -> frozenset[str]:
    """Compare published name words only; no stemming, synonyms, or identity merging."""
    possessive_free = re.sub(r"(?<=\w)['’]s\b", "", value.casefold())
    return frozenset(re.findall(r"[^\W_]+", possessive_free)) - _DISCOVERY_FILLER


# The one way published names are read by their words, for callers outside this module.
discovery_tokens = _discovery_tokens


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
    # A digest of the evidence rows the readers return, read in the same transaction, so a derived
    # copy can tell when rows were edited in place. An adapter that cannot say returns "".
    fingerprint_reader: Callable[[], str] = lambda: ""
    # Linked rows of one of RECORD_COLLECTIONS (dining hours, a menu, a route's trips). An adapter
    # with none reads nothing.
    record_reader: Callable[[dict[str, Any], str], list[dict[str, Any]]] = (
        lambda entity, collection: [])


@dataclass(frozen=True)
class ReleaseInputs:
    """Everything a derived copy of one release needs, read as one consistent snapshot."""

    dataset_version: str
    identity_hash: str
    entities: list[dict[str, Any]]
    alias_sources: list[dict[str, Any]]
    # entity id -> (contact rows, schedule rows), only for the entity kinds that were asked for.
    evidence: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]]
    fingerprint: str
    # entity id -> collection -> rows, for the kinds that were asked for and only where linked.
    records: dict[str, dict[str, list[dict[str, Any]]]] = dataclasses.field(
        default_factory=dict)


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


def _source(row: dict[str, Any], now: datetime, on: date | None = None) -> dict[str, Any]:
    """`on` is the date the record is read for. A record valid for exactly that date is current
    for it, though that date is tomorrow: freshness is still judged at `now`."""
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
    today = on or now.astimezone(CAMPUS_TIMEZONE).date()
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


# The raw contact columns that hold a published value for each field a claim can cover.
_ABSENCE_COLUMNS = {"email": ("email",), "phones": ("phone", "phones"),
                    "offices": ("office", "offices"), "hours": (),
                    "prefers_email": ("prefers_email",),
                    "preferred_contact": ("preferred_contact",),
                    "contact_note": ("contact_note",), "department": ("department",),
                    # A website is a reviewed claim, never a raw column; a value always wins anyway.
                    "website": ()}
MAX_ABSENCE_CHECKS = 16
MAX_ABSENCE_TEXT = 300


def _check(item: Any) -> dict[str, str] | None:
    """One section the publisher read: a secure page, a heading and an instant, all plain text."""
    if not isinstance(item, dict) or _url(item.get("url")) is None:
        return None
    for name in ("section", "checked_at"):
        text = item.get(name)
        if (not isinstance(text, str) or not text.strip() or len(text) > MAX_ABSENCE_TEXT
                or any(ord(character) < 32 for character in text)):
            return None
    if _instant(item["checked_at"]) is None:
        return None
    if "text_sha256" in item and not _observation_hash(item["text_sha256"]):
        return None
    return {name: item[name] for name in ("url", "section", "checked_at", "text_sha256")
            if name in item}


def confirmed_absence(rows: Sequence[dict[str, Any]], key: str, now: datetime | None = None,
                      ) -> dict[str, Any] | None:
    """The publisher's confirmation that the office's own pages do not publish this field.

    A reviewed claim reaches a row only after the run's capture of the cited page sections was
    read and none stated a value (`normalization_metadata.evidence.not_published`). It says
    which sections were read and when. This is read only when no value is published (a value
    always wins), and a confirmation is refused, leaving the field unknown, when: any check is
    malformed, insecure or dated after the clock; the row holds any value for the field in a raw
    column; or the same row's publisher record doubts it (`absence_issues` or `withheld`).
    `current` says whether every check is still inside the row's own freshness allowance.
    """
    wanted = ABSENCE_FIELDS.get(key)
    if wanted is None:
        return None
    source_ids: list[str] = []
    checks: list[dict[str, str]] = []
    current = True
    scopes: list[str] = []
    reasons: list[str] = []
    for row in rows:
        if any(_present(row.get(column)) for column in _ABSENCE_COLUMNS.get(key, ())):
            continue
        metadata = row.get("normalization_metadata")
        evidence = metadata.get("evidence") if isinstance(metadata, dict) else None
        claims = evidence.get("not_published") if isinstance(evidence, dict) else None
        if not isinstance(claims, list):
            continue
        doubted = {entry.get("field") for name in ("absence_issues", "withheld")
                   for entry in (evidence[name] if isinstance(evidence, dict)
                                 and isinstance(evidence.get(name), list) else [])
                   if isinstance(entry, dict)}
        if wanted in doubted:
            continue
        for claim in claims:
            if not isinstance(claim, dict) or claim.get("field") != wanted:
                continue
            raw = claim.get("checks")
            valid = [check for item in (raw if isinstance(raw, list) else [])
                     if (check := _check(item)) is not None]
            if (not valid or not isinstance(raw, list) or len(valid) != len(raw)
                    or len(valid) > MAX_ABSENCE_CHECKS):
                continue
            instants = [_instant(check["checked_at"]) for check in valid]
            if now is not None and any(instant is None or instant > now for instant in instants):
                continue
            sla = row.get("freshness_sla_hours")
            allowance = (timedelta(hours=sla) if isinstance(sla, int)
                         and not isinstance(sla, bool) and sla > 0 else None)
            if now is None or allowance is None or any(
                    instant is None or now - instant > allowance for instant in instants):
                current = False
            if row["id"] not in source_ids:
                source_ids.append(row["id"])
            checks.extend(check for check in valid if check not in checks)
            for name, output in (("scope", scopes), ("reason", reasons)):
                value = claim.get(name)
                if isinstance(value, str) and value.strip() and value not in output:
                    output.append(value)
    if not checks:
        return None
    latest = datetime.max.replace(tzinfo=UTC)
    oldest = min(checks, key=lambda check: _instant(check["checked_at"]) or latest)
    return {"source_ids": source_ids, "checks": checks, "checked_at": oldest["checked_at"],
            "current": current, **({"scope": " ".join(scopes)} if scopes else {}),
            **({"reason": " ".join(reasons)} if reasons else {})}


def _contact_projections(row: dict[str, Any], field: str) -> list[tuple[Any, Any, list[str]]]:
    """Keep separately reviewed same-purpose claims in the original published record."""
    projections = [project_contact(row, field)]
    metadata = row.get("normalization_metadata")
    evidence = metadata.get("evidence") if isinstance(metadata, dict) else None
    claims = evidence.get("contact_conflicts", []) if isinstance(evidence, dict) else []
    if not isinstance(claims, list) or len(claims) > 32:
        raise EvidenceUnavailable("Invalid reviewed contact conflicts.")
    for claim in claims:
        if not isinstance(claim, dict):
            raise EvidenceUnavailable("Invalid reviewed contact conflict.")
        raw_field = claim.get("field")
        if not isinstance(raw_field, str):
            raise EvidenceUnavailable("Invalid reviewed contact conflict field.")
        target = FIELD_ALIASES.get(raw_field, raw_field)
        if target != field:
            continue
        if (not all(isinstance(claim.get(key), str) and claim[key].strip()
                    for key in ("value", "label", "url", "section", "checked_at"))
                or _url(claim["url"]) is None or _instant(claim["checked_at"]) is None):
            raise EvidenceUnavailable("A conflicting contact needs its reviewed value and source.")
        column = {"phones": "phone", "offices": "office", "email": "email"}.get(field)
        if column is None:
            raise EvidenceUnavailable("Unsupported reviewed contact conflict field.")
        value, _, caveats = project_contact({column: claim["value"]}, field)
        projections.append((value, deepcopy(claim), [*caveats,
            f"Different same-purpose contact published in {claim['section']} of {claim['url']}."]))
    return projections


def canonical_properties(
    rows: Sequence[dict[str, Any]],
    fields: Sequence[str],
    sources: Sequence[dict[str, Any]],
    *,
    registry_name: str = "",
    reviewed_aliases: Sequence[str] = (),
    now: datetime | None = None,
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
            for index, (value, raw, caveats) in enumerate(_contact_projections(row, field)):
                if field == "name" and canonical_name_published and value in reviewed_aliases:
                    caveats.append(f"Published name is a reviewed alias of {registry_name}.")
                    value = registry_name
                assertion_id = f"{row['id']}:{field}" + (f":conflict:{index}" if index else "")
                source_id = row["id"] if index else field_sources.get((row["id"], field), row["id"])
                assertions.append({"id": assertion_id, "source_id": source_id, "field": field,
                                   "raw_value": _json_safe(raw), "value": _json_safe(value),
                                   "caveats": caveats})
                if _present(value):
                    key = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
                    group = groups.setdefault(
                        key, {"value": _json_safe(value), "assertion_ids": [], "source_ids": []})
                    group["assertion_ids"].append(assertion_id)
                    if source_id not in group["source_ids"]:
                        group["source_ids"].append(source_id)
        values = list(groups.values())
        status = "unknown" if not values else "known"
        absence = confirmed_absence(rows, field, now) if not values else None
        if absence:
            status = "not_published"
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
                **({"absence": absence} if absence else {}),
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
            if link.get("collection") in RECORD_COLLECTIONS:
                record_keys = link.get("source_record_keys")
                if (not isinstance(record_keys, list)
                        or not all(isinstance(k, str) for k in record_keys)
                        or len(record_keys) > RECORD_COLLECTIONS[link["collection"]]
                        or not _text(link.get("source_key"))):
                    raise EvidenceUnavailable("Invalid record identity link.")
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
    for entity in entities:
        if entity["kind"] in DESK_KINDS and any(
                link.get("collection") == "contacts" and link.get("source_record_keys")
                for link in entity["links"]):
            entity["desk_kind"], entity["kind"] = entity["kind"], "office"
    return entities


def linked_records(
    snapshot: Snapshot, entities: Sequence[dict[str, Any]],
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """The rows of every extra record collection each entity links, for a derived copy."""
    found: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for entity in entities:
        held = {
            collection: rows for collection in RECORD_COLLECTIONS
            if any(link.get("collection") == collection for link in entity["links"])
            and (rows := snapshot.record_reader(entity, collection))
        }
        if held:
            found[entity["id"]] = held
    return found


def linked_rows(
    entity: dict[str, Any], rows: Sequence[dict[str, Any]], collection: str,
) -> list[dict[str, Any]]:
    """Rows that an entity's links of one collection name by exact source and record key."""
    links = [link for link in entity["links"] if link.get("collection") == collection]
    return [
        row for row in rows
        if any(
            row.get("source_key") == link["source_key"]
            and row.get("source_record_key") in link["source_record_keys"]
            for link in links
        )
    ]


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


def _schedule_claim(row: dict[str, Any]) -> dict[str, Any]:
    """Bounded publisher observations; their source statements are evidence, never instructions."""
    metadata = row.get("normalization_metadata")
    evidence = metadata.get("evidence") if isinstance(metadata, dict) else None
    claim = evidence.get("schedule") if isinstance(evidence, dict) else None
    if claim is None:
        return {}
    if (not isinstance(claim, dict)
            or not set(claim) <= {"status", "reason", "source_statements", "season",
                                  "not_published"}
            or ("season" in claim and not _bounded_observation_text(claim["season"], 300))
            or ("status" in claim and (not isinstance(claim["status"], str)
                                       or claim["status"] not in {"unknown", "conflicting"}))
            or ("status" in claim and not _bounded_observation_text(claim.get("reason"), 2_000))
            or ("reason" in claim and not _bounded_observation_text(claim["reason"], 2_000))):
        raise EvidenceUnavailable("Invalid schedule evidence metadata.")
    statements = claim.get("source_statements", [])
    if (not isinstance(statements, list) or len(statements) > 16
            or not all(isinstance(item, str) and item.strip() and len(item) <= 2_000
                       and not any(ord(char) < 32 and char not in "\r\n\t" for char in item)
                       for item in statements)):
        raise EvidenceUnavailable("Invalid schedule source statements.")
    return deepcopy(claim)


def _schedule_absences(row: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    """Read only the publisher's explicit omissions bound to this schedule's captured page.

    The publisher proves the reviewed section against its archived HTML. This reader checks
    that proof's shape, source and capture binding; null text alone never proves an absence.
    """
    claims = _schedule_claim(row).get("not_published", [])
    if not isinstance(claims, list) or len(claims) > 3:
        raise EvidenceUnavailable("Invalid reviewed schedule omissions.")
    result = []
    held: set[str] = set()
    for claim in claims:
        if not isinstance(claim, dict) or not isinstance(claim.get("field"), str):
            raise EvidenceUnavailable("Invalid reviewed schedule omission.")
        field = claim["field"]
        keys = {"field", "scope", "reason", "checks"} | (
            {"days"} if field == "weekday_hours" else set())
        if (field not in {"weekday_hours", "valid_from", "valid_until"} or field in held
                or set(claim) != keys
                or not _bounded_observation_text(claim.get("scope"), 1_000)
                or not _bounded_observation_text(claim.get("reason"), 2_000)):
            raise EvidenceUnavailable("Invalid reviewed schedule omission scope.")
        held.add(field)
        if field == "weekday_hours":
            days = claim["days"]
            if (not isinstance(days, list) or not days or len(days) > 7
                    or not all(isinstance(day, str) and day in WEEKDAYS for day in days)
                    or len(set(days)) != len(days)):
                raise EvidenceUnavailable("Invalid reviewed schedule weekdays.")
            if clean(row.get("day")) in days and _schedule_hours(row.get("schedule")) is not None:
                raise EvidenceUnavailable("A published weekday cannot also be confirmed absent.")
        elif row.get(field) is not None:
            raise EvidenceUnavailable("A published date cannot also be confirmed absent.")
        checks = claim["checks"]
        if not isinstance(checks, list) or not checks or len(checks) > MAX_ABSENCE_CHECKS:
            raise EvidenceUnavailable("A schedule omission needs reviewed source sections.")
        times = []
        for check in checks:
            if (not isinstance(check, dict)
                    or set(check) != {"url", "section", "checked_at", "html_sha256"}
                    or _check(check) is None or not _observation_hash(check["html_sha256"])):
                raise EvidenceUnavailable("Invalid schedule omission page proof.")
            times.append(_observation_time(check["checked_at"], now))
        captured = _instant(row.get("collected_at"))
        if not any(check["url"] == row.get("source_url") and stamp == captured
                   for check, stamp in zip(checks, times, strict=True)):
            raise EvidenceUnavailable("Schedule omission proof does not match its source capture.")
        sla = row.get("freshness_sla_hours")
        current = (isinstance(sla, int) and not isinstance(sla, bool) and sla > 0
                   and all(now - stamp <= timedelta(hours=sla) for stamp in times))
        oldest = min(range(len(times)), key=times.__getitem__)
        result.append({**deepcopy(claim), "status": "not_published", "current": current,
                       "checked_at": checks[oldest]["checked_at"]})
    return result


def _merge_schedule_absence(proofs: list[dict[str, Any]]) -> dict[str, Any]:
    """Preserve each supporting page and scope when identical schedule readings meet."""
    checks = [check for proof in proofs for check in proof["checks"]]
    checks = [check for index, check in enumerate(checks) if check not in checks[:index]]
    oldest = min(checks, key=lambda check: _instant(check["checked_at"])
                 or datetime.max.replace(tzinfo=UTC))
    return {"status": "not_published", "checks": checks, "checked_at": oldest["checked_at"],
            "current": all(proof["current"] for proof in proofs),
            "source_ids": list(dict.fromkeys(
                sid for proof in proofs for sid in proof["source_ids"])),
            **{key: " ".join(dict.fromkeys(proof[key] for proof in proofs))
               for key in ("scope", "reason")}}


def _schedule_hours(value: Any) -> str | None:
    """The collector's explicit missing marker is absence, never an opening time or closure."""
    value = clean(value)
    if value is None or value == "" or value == "Hours unavailable":
        return None
    if not isinstance(value, str):
        raise EvidenceUnavailable("Invalid schedule hours text.")
    return value


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
    buckets: dict[tuple[str, str | None, str | None, str | None], list[dict[str, Any]]] = {}
    for row in rows:
        name, day = clean(row.get("name")), clean(row.get("day"))
        if not all(isinstance(part, str) and part for part in (name, day)):
            raise EvidenceUnavailable("A schedule record needs a name and a day.")
        _schedule_hours(row.get("schedule"))
        claim = _schedule_claim(row)
        _schedule_absences(row, now)
        since, until = _date(row.get("valid_from")), _date(row.get("valid_until"))
        if (any(row.get(key) is not None and parsed is None
                for key, parsed in (("valid_from", since), ("valid_until", until)))
                or (since and until and since > until)):
            raise EvidenceUnavailable("Invalid published schedule validity interval.")
        buckets.setdefault((name, claim.get("season"), since.isoformat() if since else None,
                            until.isoformat() if until else None), []).append(row)
    sources: list[dict[str, Any]] = []
    assertions: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    groups: dict[str, dict[str, Any]] = {}
    ordered = sorted(
        buckets.items(), key=lambda item: tuple(part or "" for part in item[0]))
    for (name, season, _, _), bucket in ordered:
        by_day: dict[str, list[dict[str, Any]]] = {}

        def record_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
            # The seven weekday rows of one captured statement share page, capture time, sentence
            # and metadata, while row ids are unrelated random values. Ordering each weekday's rows
            # by this puts record N in position N on every weekday, so reading N is that
            # statement's own week and never a mix of two statements' days.
            return (str(row.get("source_url")), str(row.get("collected_at")),
                    str(row.get("notes")),
                    json.dumps(row.get("normalization_metadata"), sort_keys=True, default=str))

        for row in sorted(bucket, key=lambda r: (_day_order(clean(r["day"])), record_key(r),
                                                 str(r.get("id")))):
            by_day.setdefault(clean(row["day"]), []).append(row)
        # Two different texts for one day are a disagreement, so each reading gets its own value.
        readings: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        for index in range(max(len(rows_for_day) for rows_for_day in by_day.values())):
            picked = [days[min(index, len(days) - 1)] for days in by_day.values()]
            # Identical readings still keep distinct supporting records and capture boundaries.
            key = tuple(str(row["id"]) for row in picked)
            readings.setdefault(key, picked)
        for picked in readings.values():
            value: dict[str, Any] = {
                "schedule": name,
                "days": [{"day": clean(r["day"]), "hours": _schedule_hours(r.get("schedule"))}
                         for r in picked],
                "notes": list(dict.fromkeys(
                    clean(r["notes"]) for r in picked if _present(r.get("notes")))),
            }
            if season:
                value["season"] = season
            # Proof timestamps and page hashes do not make equal opening times disagree.
            value_key = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
            by_claim: dict[str, dict[str, Any]] = {}
            for row in picked:
                claim = _schedule_claim(row)
                if claim:
                    by_claim.setdefault(json.dumps(claim, sort_keys=True), claim)
            claims = list(by_claim.values())
            unavailable = [entry["day"] for entry in value["days"] if entry["hours"] is None]
            has_hours = len(unavailable) < len(value["days"])
            captured = [t for r in picked if (t := _instant(r.get("collected_at")))]
            urls = list(dict.fromkeys(u for r in picked if (u := _url(r.get("source_url")))))
            # One id per reading: two readings of one day can both start from the same first row.
            reading = hashlib.sha256(
                "|".join(str(r["id"]) for r in picked).encode()).hexdigest()[:16]
            source = _source({
                **picked[0], "id": f"{picked[0]['id']}:schedule:{reading}",
                "collected_at": min(captured) if len(captured) == len(picked) else None,
                "canonical_url": urls[0] if urls else picked[0].get("canonical_url"),
                "normalization_metadata": {"evidence": {"source_urls": urls,
                                                         "schedule_observations": claims}},
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
            row_absences = [[{**proof, "source_ids": [source["id"]]}
                             for proof in _schedule_absences(row, now)] for row in picked]
            if has_hours:
                for entry, proofs in zip(value["days"], row_absences, strict=True):
                    applicable = [proof for proof in proofs if proof["field"] == "weekday_hours"
                                  and entry["day"] in proof["days"]]
                    if entry["hours"] is None and applicable:
                        entry.update(status="not_published",
                                     absence=_merge_schedule_absence(applicable))
            validity_absence = {}
            for bound in ("valid_from", "valid_until"):
                matching = [[proof for proof in proofs if proof["field"] == bound]
                            for proofs in row_absences]
                if all(matching):
                    validity_absence[bound] = _merge_schedule_absence(
                        [proof for proofs in matching for proof in proofs])
            if validity_absence:
                value["validity_absence"] = validity_absence
                source["validity_absence"] = deepcopy(validity_absence)
            if season:
                source["season"] = season
                if not source["valid_from"] or not source["valid_until"]:
                    source["caveats"].append(
                        "This seasonal schedule has no complete published date range; "
                        "its applicability to a particular date is unverified.")
            assertion_id = f"{source['id']}:hours"
            assertions.append({
                "id": assertion_id, "source_id": source["id"], "field": "hours",
                "raw_value": _json_safe([{"day": r["day"], "schedule": r.get("schedule"),
                                           "notes": r.get("notes"),
                                           "evidence": _schedule_claim(r)} for r in picked]),
                "value": _json_safe(value) if has_hours else None,
                "caveats": (["Missing weekday hours never mean closed; "
                             "reviewed absences retain their scope."] if unavailable else []),
            })
            problems = [claim for claim in claims if claim.get("status")]
            unknown_days = [entry["day"] for entry in value["days"]
                            if entry["hours"] is None and entry.get("status") != "not_published"]
            if unknown_days or problems:
                reasons = list(dict.fromkeys(claim["reason"] for claim in problems))
                if not reasons:
                    reasons = (value["notes"] if not has_hours and value["notes"] else
                               ["No verified hours are published for the listed weekdays."])
                issues.append({
                    "schedule": name, **({"season": season} if season else {}),
                    "status": ("conflicting" if any(c.get("status") == "conflicting"
                                                    for c in problems) else "unknown"),
                    "reason": " ".join(reasons), "days": unknown_days,
                    "source_statements": list(dict.fromkeys(
                        statement for claim in problems
                        for statement in claim.get("source_statements", []))),
                    "source_ids": [source["id"]],
                    **({"validity_absence": deepcopy(validity_absence)}
                       if validity_absence else {}),
                })
            if not has_hours:
                continue
            group = groups.setdefault(value_key, {"value": _json_safe(value), "assertion_ids": [],
                                                  "source_ids": []})
            for entry, existing in zip(value["days"], group["value"]["days"], strict=True):
                if entry.get("absence"):
                    proofs = [entry["absence"]] + (
                        [existing["absence"]] if existing.get("absence") else [])
                    existing.update(status="not_published", absence=_merge_schedule_absence(proofs))
            for bound, proof in validity_absence.items():
                existing = group["value"].setdefault("validity_absence", {}).get(bound)
                group["value"]["validity_absence"][bound] = _merge_schedule_absence(
                    [proof, existing] if existing else [proof])
            group["assertion_ids"].append(assertion_id)
            group["source_ids"].append(source["id"])
    values = list(groups.values())
    status = "known" if values else "unknown"
    by_schedule: dict[tuple[str, str | None], list[dict[str, Any]]] = {}
    for found in values:
        by_schedule.setdefault((found["value"]["schedule"], found["value"].get("season")),
                               []).append(found)
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
    if any(issue["status"] == "conflicting"
           and (issue["schedule"], issue.get("season")) in by_schedule for issue in issues):
        status = "conflicting"
    return ({"key": "hours", "label": "Hours", "category": "schedule", "status": status,
             "values": values, "assertions": assertions, **({"issues": issues} if issues else {})},
            sources)


def dining_property(
    rows: Sequence[dict[str, Any]], now: datetime
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """A dining venue's `hours` property: its weekly schedule and its dated exceptions.

    The venue's rows are read exactly as an office's schedules are. The one difference is the
    venue's own rule: a row with a validity window is an exception to its weekly row for those
    dates, not a second schedule that disagrees with it. Readings of different windows are
    therefore `multiple`; two different readings of the same window are still a conflict."""
    # An exception whose dates ended before today is no part of what a student can ask about.
    today = now.astimezone(CAMPUS_TIMEZONE).date()
    rows = [row for row in rows
            if not ((until := _date(row.get("valid_until"))) is not None and until < today)]
    prop, sources = schedule_property(rows, now)
    if len(prop["values"]) > 1 and prop["status"] in {"conflicting", "multiple"}:
        windows: dict[tuple[Any, Any], set[str]] = {}
        by_id = {source["id"]: source for source in sources}
        for value in prop["values"]:
            source = by_id[value["source_ids"][0]]
            windows.setdefault((source["valid_from"], source["valid_until"]), set()).add(
                json.dumps(value["value"], sort_keys=True))
        same_window_conflict = any(len(readings) > 1 for readings in windows.values())
        issue_conflict = any(issue.get("status") == "conflicting"
                             for issue in prop.get("issues", []))
        prop["status"] = "conflicting" if same_window_conflict or issue_conflict else "multiple"
    return prop, sources


def _publishes(entity: dict[str, Any]) -> list[str]:
    """What a venue publishes, from the records linked to it: hours, a menu."""
    named = {"dining_hours": "hours", "menu": "menu"}
    return sorted({named[link["collection"]] for link in entity.get("links", [])
                   if link.get("collection") in named})


_MEAL_ORDER = ("Breakfast", "Continental", "Brunch", "Lunch", "Lite Lunch", "Dinner", "Late Night")


def _meal_rank(meal: str) -> tuple[int, str]:
    return (_MEAL_ORDER.index(meal), meal) if meal in _MEAL_ORDER else (len(_MEAL_ORDER), meal)


_ALLERGEN_WORDS = {  # What a student says -> the words the published labels use.
    "dairy": ("milk",), "lactose": ("milk",), "nut": ("tree nut", "peanut"),
    "nuts": ("tree nut", "peanut"), "treenut": ("tree nut",), "treenuts": ("tree nut",),
}
# A food word a student uses for a kind of dish -> the words its published names use.
_FOOD_WORDS = {
    "pasta": ("pasta", "macaroni", "penne", "rigatoni", "rotini", "spaghetti", "ziti", "lasagna",
              "linguine", "fettuccine", "tortellini", "ravioli", "orzo", "gnocchi"),
}
MAX_MENU_WORDS = 3
MAX_REST_OF_MENU = 40  # The rest of a filtered meal by name: "what is for dinner" is said.
# A filter that keeps more items than a reply can say lists this many per station, with the count.
MAX_MATCHES_LISTED, MATCHES_PER_STATION, MATCHES_SHOWN = 30, 3, 24
MAX_OTHER_DATES = 6  # Other published dates a food that is not on the asked date is served.


def _squash(text: Any) -> str:
    """A word for comparison: one case, no spaces or hyphens, no plural s."""
    folded = "".join(ch for ch in str(text).casefold() if ch.isalnum())
    return folded[:-1] if len(folded) > 3 and folded.endswith("s") else folded


def _allergen_terms(asked: str) -> set[str]:
    one = _squash(asked)
    return {_squash(term) for term in _ALLERGEN_WORDS.get(one, (asked,))} | {one}


def _labels_for(asked: str, labels: Sequence[Any]) -> list[str]:
    """The published allergen labels a student's word stands for ("dairy" -> Milk)."""
    terms = _allergen_terms(asked)
    return [str(a) for a in labels
            if _squash(a) in terms or any(t in _squash(a) for t in terms)]


# Words that say a food is a dish, not which food: "the chicken dish" is chicken.
_FOOD_FILLER = frozenset("dish dishes food foods meal meals item items option options plate "
                         "plates something anything stuff".split())
# What a student may ask a menu for that its rows do not carry: calories are the only nutrient.
_NUTRIENTS = frozenset("protein proteins carb carbs carbohydrate carbohydrates fat fats sugar "
                       "sugars sodium salt fiber fibre cholesterol".split())


def _main_food(asked: str) -> str:
    """The food in a phrase, without words that only say it is a dish ("chicken dish")."""
    return " ".join(w for w in asked.split() if w.casefold() not in _FOOD_FILLER)


def _all_words_in(asked: str, text: str) -> bool:
    """A food of two or more words whose every word is one of the item's, in any order: "chicken
    sandwich" is "Fried Chicken Patty Sandwich"."""
    wanted = {_squash(word) for word in re.findall(r"[a-z0-9]+", asked.casefold())}
    return len(wanted) > 1 and wanted <= {_squash(word) for word in re.findall(r"[a-z0-9]+", text)}


def _menu_kept(row: dict[str, Any], query: dict[str, Any]) -> bool:
    """Whether one menu row passes every filter the student's question set."""
    raw = row.get("label_coverage")
    coverage: dict[str, Any] = raw if isinstance(raw, dict) else {}
    if query.get("diet"):
        flag = query["diet"]
        if not (row.get(flag) is True and coverage.get(flag, "published") == "published"):
            return False
    words = [term for w in (query.get("contains") or [])
             for term in (w, *_FOOD_WORDS.get(_squash(w), ()), _main_food(w)) if term]
    if words:
        # Name, station and portion: the dining service marks main dishes "ENTREE" as a portion.
        text = (f"{clean(row.get('name'))} {clean(row.get('station'))} "
                f"{clean(row.get('portion_size')) or ''}").casefold()
        if not any(_squash(w) and _squash(w) in "".join(ch for ch in text if ch.isalnum())
                   or w.casefold() in text or _all_words_in(w, text) for w in words):
            return False
    listed = row.get("allergens") if isinstance(row.get("allergens"), list) else None
    published = coverage.get("allergens", "published") == "published" and listed is not None

    def names(asked: str) -> bool:
        return bool(_labels_for(asked, listed or []))

    # With: the item lists any of them. Without: coverage is published and it lists none.
    if query.get("with_allergens") and not (
            published and any(names(a) for a in query["with_allergens"])):
        return False
    return not (query.get("without_allergens") and (
        not published or any(names(a) for a in query["without_allergens"])))


def menu_property(
    rows: Sequence[dict[str, Any]], now: datetime, on: date | None, meal: str | None = None,
    query: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """A venue's menu for one date: its meals in service order, then stations, then items.

    A date with no rows is `unknown`: the venue published nothing for it, which is not the same
    as being closed (a closed day and a missing capture look alike here). Dietary labels are
    kept only as the source published them: a flag is given when its coverage says published."""
    empty = {"key": "menu", "label": "Menu", "category": "menu", "status": "unknown",
             "values": [], "assertions": []}
    dates = sorted({d for row in rows if (d := _date(row.get("valid_from")))})
    if on is None or on not in dates:
        return {**empty, "dates_published": [d.isoformat() for d in dates]}, []
    picked = [row for row in rows if _date(row.get("valid_from")) == on]
    not_listed: str | None = None
    served_instead: dict[str, str] | None = None
    if meal:
        # Only the meal asked for; "late night" and "Late Night" are the same meal.
        asked = " ".join(meal.casefold().split())

        def named(wanted: str) -> list[dict[str, Any]]:
            return [row for row in picked
                    if " ".join(str(clean(row.get("meal")) or "").casefold().split()) == wanted]
        chosen = named(asked)
        if not chosen and asked in {"breakfast", "lunch"} and named("brunch"):
            # A weekend serves brunch in place of breakfast and lunch: that is the meal asked about.
            chosen = named("brunch")
            served_instead = {"asked": " ".join(meal.split()),
                              "meal": str(clean(chosen[0].get("meal")))}
        if chosen:
            picked = chosen
        else:
            # The date's menu is published and has no such meal: say so, with what it does list.
            not_listed = " ".join(meal.split())
    on_menu = len(picked)
    vocabulary = sorted({str(a) for r in picked if isinstance(r.get("allergens"), list)
                         for a in r["allergens"] if isinstance(a, str)})
    day_rows = picked
    # An allergen no label on this menu names (nuts, when the labels never list nuts) cannot be
    # filtered out: every item would "pass" for want of a label. It filters nothing and is said.
    # The same holds for "which items contain nuts": no label names them, so none can be said to.
    unlabelled = [a for key in ("without_allergens", "with_allergens")
                  for a in (query or {}).get(key) or [] if not _labels_for(a, vocabulary)]
    if query and unlabelled:
        query = {**query, **{key: [a for a in query.get(key) or [] if a not in unlabelled]
                             for key in ("without_allergens", "with_allergens")}}
    # "Anything high protein": the rows carry calories and no other nutrient, so a nutrient word
    # filters nothing (every item would fail for want of a number) and is said not published.
    nutrients = [w for w in (query or {}).get("contains") or []
                 if set(w.casefold().split()) & _NUTRIENTS]
    if query and nutrients:
        query = {**query, "contains": [w for w in query["contains"] if w not in nutrients]}
    filtering = query is not None and any(query.get(key) for key in (
        "diet", "contains", "with_allergens", "without_allergens"))
    if query is not None and filtering:
        picked = [row for row in picked if _menu_kept(row, query)]
    meals: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for row in sorted(picked, key=lambda r: (_meal_rank(clean(r.get("meal")) or ""),
                                             clean(r.get("station")) or "", str(r.get("id")))):
        meal = clean(row.get("meal"))
        station = clean(row.get("station"))
        name = clean(row.get("name"))
        if not (isinstance(meal, str) and meal and isinstance(station, str) and station
                and isinstance(name, str) and name):
            raise EvidenceUnavailable("A menu record needs a meal, a station and a name.")
        raw_coverage = row.get("label_coverage")
        coverage: dict[str, Any] = raw_coverage if isinstance(raw_coverage, dict) else {}
        item: dict[str, Any] = {"name": name}
        if isinstance(row.get("calories"), int) and not isinstance(row.get("calories"), bool):
            item["calories"] = row["calories"]
        if _present(row.get("portion_size")):
            item["portion"] = clean(row["portion_size"])
        for flag in ("vegan", "vegetarian"):
            if isinstance(row.get(flag), bool) and coverage.get(flag, "published") == "published":
                item[flag] = row[flag]
        allergens = row.get("allergens")
        if (isinstance(allergens, list) and allergens
                and all(isinstance(a, str) for a in allergens)):
            item["allergens"] = list(allergens)
        meals.setdefault(meal, {}).setdefault(station, []).append(item)
    value: dict[str, Any] = {"date": on.isoformat(), "meals": [
        {"meal": meal, "stations": [{"station": station, "items": items}
                                    for station, items in stations.items()]}
        for meal, stations in meals.items()]}
    if served_instead is not None:
        value["served_instead"] = served_instead
    if not_listed is not None:
        value = {"date": on.isoformat(), "meals": [], "meal_not_listed": not_listed,
                 "meals_listed": list(meals)}
    if nutrients:
        value["nutrients_unlisted"] = nutrients
    if unlabelled:
        value["allergens_unlabelled"] = unlabelled
        value["allergens_published"] = vocabulary
    if query and filtering:
        value["filter"] = {key: query[key] for key in
                           ("diet", "contains", "with_allergens", "without_allergens")
                           if query.get(key)}
        value["matched"] = len(picked)
        value["on_menu"] = on_menu
        if len(picked) > MAX_MATCHES_LISTED:
            # 93 dairy-free items cannot all be said: a few from each station, up to a total,
            # and a flag (never a second count a writer could mistake for matched).
            left = MATCHES_SHOWN
            for listed_meal in value["meals"]:
                for station in listed_meal["stations"]:
                    station["items"] = station["items"][:min(MATCHES_PER_STATION, left)]
                    left -= len(station["items"])
                listed_meal["stations"] = [st for st in listed_meal["stations"] if st["items"]]
            value["meals"] = [m for m in value["meals"] if m["stations"]]
            value["matches_cut"] = True
        if query.get("with_allergens") or query.get("without_allergens"):
            value["allergens_published"] = vocabulary
            # The student's own word for each allergen, with the labels it stands for: "dairy"
            # is labelled Milk, so a writer never reads "dairy" as missing from the labels.
            value["allergen_words"] = {
                asked: _labels_for(asked, vocabulary)
                for asked in [*query.get("with_allergens", []),
                              *query.get("without_allergens", [])]}
        if (query.get("diet") or query.get("with_allergens") or query.get("without_allergens")):
            # The meal's other items by name, so "what's for dinner, anything vegan" says both.
            kept = {id(row) for row in picked}
            rest = list(dict.fromkeys(str(clean(r.get("name"))) for r in day_rows
                                      if id(r) not in kept and clean(r.get("name"))))
            if rest:
                value["rest_of_menu"] = rest[:MAX_REST_OF_MENU]
                value["rest_of_menu_cut"] = len(rest) > MAX_REST_OF_MENU
        if not picked:
            # Nothing passes: say what the menu does have, so "none" is never a dead end.
            value["meals_listed"] = list(dict.fromkeys(
                str(clean(r.get("meal"))) for r in sorted(
                    day_rows, key=lambda r: _meal_rank(clean(r.get("meal")) or ""))))
            value["stations_listed"] = sorted({str(clean(r.get("station"))) for r in day_rows})
            if query.get("contains"):
                # The food on the venue's other published dates from this one on, so "chicken
                # tenders" not served today says when it is.
                later = sorted((d, clean(r.get("meal")), clean(r.get("name"))) for r in rows
                               if (d := _date(r.get("valid_from"))) and d > on
                               and _menu_kept(r, query))
                if later:
                    value["on_other_dates"] = [
                        {"date": d.isoformat(), "meal": m, "name": n}
                        for d, m, n in list(dict.fromkeys(later))[:MAX_OTHER_DATES]]
    cited = picked or day_rows  # Nothing matching is still read from that date's records.
    captured = [t for r in cited if (t := _instant(r.get("collected_at")))]
    first = cited[0]
    reading = hashlib.sha256("|".join(sorted(str(r["id"]) for r in cited)).encode()).hexdigest()
    source = _source({
        **first, "id": f"{first['id']}:menu:{reading[:16]}",
        "collected_at": min(captured) if len(captured) == len(cited) else None,
        "valid_from": on, "valid_until": on,
    }, now, on)
    source.update(
        collection="menu", source_record_keys=[r["source_record_key"] for r in cited],
        record_ids=[r["id"] for r in cited],
        content_hash=hashlib.sha256(
            "|".join(sorted(str(r.get("content_hash")) for r in cited)).encode()).hexdigest())
    assertion = {"id": f"{source['id']}:menu", "source_id": source["id"], "field": "menu",
                 "raw_value": {"rows": len(cited)}, "value": _json_safe(value), "caveats": []}
    return ({"key": "menu", "label": "Menu", "category": "menu", "status": "known",
             "values": [{"value": _json_safe(value), "assertion_ids": [assertion["id"]],
                         "source_ids": [source["id"]]}],
             "assertions": [assertion], "dates_published": [d.isoformat() for d in dates]},
            [source])


def menu_listing_absence(
    rows: Sequence[dict[str, Any]], now: datetime,
) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    """The venue's own dining listing names no menu page: it publishes no dated menu.

    Each row is what the venue's listing on the dining site named as its menus when captured
    (`dated` is true when one is a menu page). Every row must say not dated; a listing that
    names a menu page leaves a missing date unknown, never not published."""
    if not rows or any(row.get("dated") is not False for row in rows):
        return None
    sources, checks = [], []
    current = True
    for row in rows:
        source = _source(row, now)
        source.update(collection="menu_listings",
                      source_record_keys=[row["source_record_key"]], record_ids=[row["id"]])
        captured = _instant(row.get("collected_at"))
        url = _url(row.get("canonical_url"))
        if captured is None or url is None or captured > now:
            return None
        current = current and source["freshness"] == "fresh"
        check = {"url": url, "section": f"{clean(row.get('name'))}: menus listed",
                 "checked_at": captured.isoformat()}
        if _observation_hash(row.get("content_hash")):
            check["text_sha256"] = row["content_hash"]
        sources.append(source)
        checks.append(check)
    listed: list[Any] = [menu for row in rows
                         for menu in (row["menus"] if isinstance(row.get("menus"), list) else [])]
    files = [str(menu.get("name") or "a menu file") for menu in listed
             if isinstance(menu, dict) and menu.get("kind") == "file"]
    reason = ("The venue's listing on the dining site names no daily menu"
              + (": it links only a menu file." if files else ", and no menu file."))
    oldest = min(checks, key=lambda check: check["checked_at"])
    return ({"source_ids": [source["id"] for source in sources], "checks": checks,
             "checked_at": oldest["checked_at"], "current": current,
             "scope": "Dated daily menu", "reason": reason}, sources)


def stops_property(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """The route's stops as its stop-descriptions page explains them: the stop name the timetables
    use, what the place is, and where the shuttle picks up and drops off there."""
    ordered = sorted(rows, key=lambda r: str(r.get("name")))
    value = [{"stop": clean(row.get("name")), "description": clean(row.get("description")),
              "pickup": clean(row.get("pickup"))} for row in ordered]
    return {"key": "stops", "label": "Shuttle stops", "category": "schedule", "status": "known",
            "values": [{"value": _json_safe(value), "assertion_ids": [], "source_ids": []}],
            "assertions": []}


def trips_property(
    rows: Sequence[dict[str, Any]], now: datetime
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """A shuttle route's published trips as one value: its timetable, exactly as listed."""
    empty = {"key": "trips", "label": "Shuttle trips", "category": "schedule",
             "status": "unknown", "values": [], "assertions": []}
    if not rows:
        return empty, []
    names = {clean(row.get("route_name")) for row in rows}
    days = {clean(row.get("service_day")) for row in rows}
    if len(names) != 1 or len(days) != 1 or not all(isinstance(n, str) and n for n in names | days):
        raise EvidenceUnavailable("A route's trips must name one route and one service day.")
    ordered = sorted(rows, key=lambda r: (r.get("sequence") if isinstance(r.get("sequence"), int)
                                          else 10**6, str(r.get("id"))))
    trips = []
    for row in ordered:
        raw_stops = row.get("stops")
        stops: list[Any] = raw_stops if isinstance(raw_stops, list) else []
        trip: dict[str, Any] = {
            "sequence": row.get("sequence"), "departure": clean(row.get("departure")),
            "arrival": clean(row.get("arrival")),
            "stops": [{"location": clean(stop.get("location")), "time": clean(stop.get("time"))}
                      for stop in stops if isinstance(stop, dict)],
        }
        # The NJ Transit trains the timetable row lists beside the trip, as published.
        trains = row.get("trains")
        if isinstance(trains, dict) and trains:
            trip["trains"] = {
                key: [clean(t) for t in trains.get(key, []) if isinstance(t, str)]
                for key in ("arrives_from_nyc", "leaves_for_nyc")
            } | ({"see_express": True} if trains.get("see_express") is True else {})
        trips.append(trip)
    value = {"route": next(iter(names)), "service_day": next(iter(days)), "trips": trips}
    captured = [t for r in ordered if (t := _instant(r.get("collected_at")))]
    first = ordered[0]
    reading = hashlib.sha256("|".join(sorted(str(r["id"]) for r in ordered)).encode()).hexdigest()
    source = _source({
        **first, "id": f"{first['id']}:trips:{reading[:16]}",
        "collected_at": min(captured) if len(captured) == len(ordered) else None,
    }, now)
    source.update(
        collection="shuttle_trips", source_record_keys=[r["source_record_key"] for r in ordered],
        record_ids=[r["id"] for r in ordered],
        content_hash=hashlib.sha256(
            "|".join(sorted(str(r.get("content_hash")) for r in ordered)).encode()).hexdigest())
    assertion = {"id": f"{source['id']}:trips", "source_id": source["id"], "field": "trips",
                 "raw_value": {"rows": len(ordered)}, "value": _json_safe(value), "caveats": []}
    return ({"key": "trips", "label": "Shuttle trips", "category": "schedule", "status": "known",
             "values": [{"value": _json_safe(value), "assertion_ids": [assertion["id"]],
                         "source_ids": [source["id"]]}], "assertions": [assertion]}, [source])


class EntityFacts:
    """Shared office search and canonical fact reader; adapters supply one snapshot."""

    backend = "unspecified"  # Which store the adapter reads, shown by the developer runtime page.

    def __init__(self, *, now: Callable[[], datetime] | None = None) -> None:
        self.now = now or (lambda: datetime.now(UTC))

    @contextmanager
    def snapshot(self) -> Iterator[Snapshot]:
        raise NotImplementedError
        yield  # pragma: no cover

    def active_release(self) -> tuple[str, str]:
        """The (dataset_version, identity_hash) of the active publication.

        A derived store asks this before reading, so it can follow a new release. Adapters
        that can answer without reading the whole registry should override it.
        """
        with self.snapshot() as snapshot:
            return snapshot.dataset_version, snapshot.identity_hash

    def evidence_fingerprint(self) -> str:
        """A digest of the active release's evidence rows; "" when the adapter cannot say."""
        with self.snapshot() as snapshot:
            return snapshot.fingerprint_reader()

    def release_inputs(self, kinds: Sequence[str]) -> ReleaseInputs:
        """The active release and the evidence of every entity of the given kinds."""
        with self.snapshot() as snapshot:
            wanted = [entity for entity in snapshot.entities if entity["kind"] in kinds]
            evidence = {
                entity["id"]: (snapshot.contact_reader(entity), snapshot.schedule_reader(entity))
                for entity in wanted
            }
            return ReleaseInputs(
                snapshot.dataset_version, snapshot.identity_hash, list(snapshot.entities),
                list(snapshot.alias_sources), evidence, snapshot.fingerprint_reader(),
                linked_records(snapshot, wanted))

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
        kinds: Sequence[str] = ("office",),
    ) -> dict[str, Any]:
        """Published office names and aliases, so callers choose real names, not guesses."""
        if not 1 <= limit <= 500:
            raise InvalidFactRequest("Office listing limit must be between 1 and 500.")
        with self.snapshot() as snapshot:
            self._pin(snapshot, dataset_version, identity_hash)
            offices = sorted(
                ({"name": entity["name"], "aliases": sorted(set(entity["aliases"])),
                  **({"entity_id": entity["id"]} if with_ids else {}),
                  **({"kind": entity["kind"]} if len(kinds) > 1 else {}),
                  **({"publishes": _publishes(entity)} if entity["kind"] == "venue" else {})}
                 for entity in snapshot.entities if entity["kind"] in kinds),
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
        kinds: Sequence[str] = ("office",),
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
                if entity["kind"] not in kinds:
                    continue
                names = [
                    " ".join(name.casefold().split())
                    for name in [entity["name"], *entity["aliases"]]
                ]
                name_tokens = [_discovery_tokens(name) for name in names]
                # Exact means the published name or alias, read by its words: the same words in
                # any order, ignoring case, punctuation, possessives and filler such as "office".
                # "public safety non-emergency" is exactly "Public Safety (Non-Emergency)" and only
                # partly "Public Safety (Emergency)", whose words are a subset of the query's.
                # names[0] is the office's own name; the rest are aliases.
                same = [text == needle or (bool(tokens) and tokens == query_tokens)
                        for text, tokens in zip(names, name_tokens, strict=True)]
                exact = any(same)
                # The question includes a whole published name and more words ("... number").
                inside = [tokens for tokens in name_tokens
                          if bool(tokens) and tokens < query_tokens]
                partial = bool(query_tokens) and any(
                    needle in name or (bool(tokens) and query_tokens <= tokens)
                    for name, tokens in zip(names, name_tokens, strict=True)
                ) or bool(inside)
                if not (exact or partial):
                    continue
                covers: list[str] = []
                if same[0]:
                    basis = "name"
                elif exact:
                    basis = "alias"
                elif inside:
                    # The office's own name when it is in the question, so an alias that is
                    # another office's name cannot hide that this office was named too; else
                    # its longest alias in the question.
                    own = bool(name_tokens[0]) and name_tokens[0] < query_tokens
                    index = 0 if own else max(
                        (len(tokens), -position)
                        for position, tokens in enumerate(name_tokens)
                        if bool(tokens) and tokens < query_tokens)[1] * -1
                    covers = sorted(name_tokens[index])
                    basis = "contains_name" if own else "contains_alias"
                else:
                    basis = "part"
                found.append(
                    {
                        "entity_id": entity["id"],
                        "name": entity["name"],
                        "aliases": list(entity["aliases"]),
                        **({"kind": entity["kind"]} if len(kinds) > 1 else {}),
                        "match": "exact" if exact else "partial",
                        # How it matched, for choosing between candidates: "name" and "alias"
                        # are the question's own words; "contains_*" is a whole published name
                        # inside a longer question (`covers` are its words); "part" is some of
                        # the words.
                        "basis": basis,
                        "covers": covers,
                    }
                )
            found.sort(key=lambda item: (
                item["match"] != "exact", -len(item["covers"]), item["name"], item["entity_id"]))
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
        on: date | None = None,
        meal: str | None = None,
        menu_query: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """The facts of one office or dining venue. `on` is the date a menu is asked for,
        `meal` the one meal of it, and `menu_query` the filters (diet, words, allergen) the
        student's question puts on it."""
        if fields is None:
            # Every field an office or venue has on record; a menu is asked for by its date.
            fields = tuple(field for field in OFFICE_FIELDS if field != "menu")
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
                    if entity["id"] == entity_id and entity["kind"] in NAMED_KINDS
                ),
                None,
            )
            if not entity:
                raise UnknownEntity("No office has that canonical identity.")
            venue = entity["kind"] == "venue"
            rows = snapshot.contact_reader(entity)
            # A venue's hours are its dining rows; an office's are its campus hours.
            schedule_rows = (snapshot.schedule_reader(entity)
                             if "hours" in selected and not venue else [])
            dining_rows = (snapshot.record_reader(entity, "dining_hours")
                           if "hours" in selected and venue else [])
            menu_rows = (snapshot.record_reader(entity, "menu")
                         if "menu" in selected and venue else [])
            # A menu the venue posts as a file (a PDF menu board) rather than as dated menus.
            menu_files = (snapshot.record_reader(entity, "menu_documents")
                          if "menu" in selected and venue else [])
            # What the venue's listing names as its menus: no menu page means no dated menu.
            menu_listings = (snapshot.record_reader(entity, "menu_listings")
                             if "menu" in selected and venue else [])
            for collection, found in (("dining_hours", dining_rows), ("menu", menu_rows),
                                      ("menu_listings", menu_listings)):
                if len(found) > RECORD_COLLECTIONS[collection]:
                    raise EvidenceUnavailable("Venue evidence exceeds the bounded read.")
                if len(linked_rows(entity, found, collection)) != len(found):
                    raise EvidenceUnavailable(
                        "A venue record has no exact canonical identity link.")
            if len(schedule_rows) > MAX_SCHEDULE_ROWS:
                raise EvidenceUnavailable("Office schedule evidence exceeds the bounded read.")
            if len(linked_schedules(entity, schedule_rows)) != len(schedule_rows):
                raise EvidenceUnavailable("A schedule record has no exact canonical identity link.")
            schedule_ids = [_text(row.get("id")) for row in schedule_rows]
            if not all(schedule_ids) or len(set(schedule_ids)) != len(schedule_ids):
                raise EvidenceUnavailable("Duplicate or missing schedule evidence identifiers.")
            if len(rows) > MAX_CONTACTS:
                raise EvidenceUnavailable("Office evidence exceeds the bounded read.")
            if len(json.dumps(_json_safe([*rows, *schedule_rows, *dining_rows]),
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
                now=now,
            )
            if "menu" in selected:
                if venue:
                    menu, menu_sources = menu_property(menu_rows, now, on, meal, menu_query)
                    if menu["status"] == "unknown" and menu_files:
                        menu = {**menu, "documents_published": [
                            {"title": clean(row.get("title")), "url": clean(row.get("url"))}
                            for row in menu_files]}
                    if (menu["status"] == "unknown" and not menu_rows
                            and (listed := menu_listing_absence(menu_listings, now))):
                        absence, listing_sources = listed
                        menu = {**menu, "status": "not_published", "absence": absence}
                        menu_sources = [*menu_sources, *listing_sources]
                else:  # Only a dining venue has a menu.
                    menu, menu_sources = menu_property([], now, None)
                sources.extend(menu_sources)
                properties = sorted(
                    [*properties, menu], key=lambda prop: list(selected).index(prop["key"]))
            if "hours" in selected and venue:
                hours, dining_sources = dining_property(dining_rows, now)
                if hours["status"] == "unknown" and not dining_rows:
                    hours = {**hours, "status": "unknown"}
                sources.extend(dining_sources)
                properties = sorted(
                    [*properties, hours], key=lambda prop: list(selected).index(prop["key"]))
            elif "hours" in selected:
                hours, schedule_sources = schedule_property(schedule_rows, now)
                # A link to a schedule says hours exist: unread rows are a gap, not an absence.
                linked_hours = any(
                    link.get("collection") == "campus_hours" for link in entity["links"])
                if (hours["status"] == "unknown" and not linked_hours
                        and (absent := confirmed_absence(rows, "hours", now))):
                    hours = {**hours, "status": "not_published", "absence": absent}
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
            if not rows and not venue:
                caveats.append("No original contact evidence is published for this office.")
            return {
                "schema_version": 4,
                "mapping_version": "entity-facts-6",
                "dataset_version": snapshot.dataset_version,
                "identity_hash": snapshot.identity_hash,
                "entity": {key: entity[key] for key in ("id", "kind", "name")},
                "properties": properties,
                "sources": sources,
                "evidence_count": (len(rows) + len(schedule_rows) + len(dining_rows)
                                   + len(menu_rows)),
                "caveats": caveats,
                "complete": not caveats,
            }


    def list_topics(
        self, *, dataset_version: str | None = None, identity_hash: str | None = None,
    ) -> dict[str, Any]:
        """Published topics (sites with no office behind them) with the other names they go by."""
        with self.snapshot() as snapshot:
            self._pin(snapshot, dataset_version, identity_hash)
            topics = sorted(
                ({"name": entity["name"], "aliases": sorted(set(entity["aliases"])),
                  "entity_id": entity["id"]}
                 for entity in snapshot.entities if entity["kind"] == "topic"),
                key=lambda topic: topic["name"])
            return {"dataset_version": snapshot.dataset_version,
                    "identity_hash": snapshot.identity_hash, "topics": topics}

    def get_topic(
        self, entity_id: str, dataset_version: str, *, identity_hash: str | None = None,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        """A topic: the offices its own page names, as this release's identities, or that it is
        retired, with the page's quoted words. A name the release has no single office for, or a
        record that is not exactly one, is unavailable evidence, never a guess."""
        with self.snapshot() as snapshot:
            self._pin(snapshot, dataset_version, identity_hash)
            entity = next((e for e in snapshot.entities
                           if e["id"] == entity_id and e["kind"] == "topic"), None)
            if entity is None:
                raise InvalidFactRequest("Unknown topic.")
            rows = snapshot.record_reader(entity, "campus_topics")
            if len(rows) != 1 or len(linked_rows(entity, rows, "campus_topics")) != 1:
                raise EvidenceUnavailable("A topic needs exactly one reviewed record.")
            row = rows[0]
            names = row.get("handled_by")
            if isinstance(names, str):
                names = json.loads(names)
            if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
                raise EvidenceUnavailable("Invalid topic offices.")
            offices = []
            for name in names:
                found = [e for e in snapshot.entities
                         if e["name"] == name and e["kind"] in NAMED_KINDS]
                if len(found) != 1:
                    raise EvidenceUnavailable("A topic names an office this release lacks.")
                offices.append({"id": found[0]["id"], "name": name, "kind": found[0]["kind"]})
            status = row.get("status")
            if status not in ("current", "retired") or (status == "current") != bool(offices):
                raise EvidenceUnavailable("Invalid topic status.")
            page = _url(row.get("url"))
            quote = clean(row.get("quote"))
            if page is None or not quote:
                raise EvidenceUnavailable("A topic needs its page and quoted words.")
            now = as_of if as_of is not None else self.now()
            source = _source({**row, "canonical_url": page}, now)
            source.update(collection="campus_topics",
                          source_record_keys=[row["source_record_key"]], record_ids=[row["id"]])
            return {
                "dataset_version": snapshot.dataset_version,
                "identity_hash": snapshot.identity_hash,
                "entity": {"id": entity["id"], "name": entity["name"], "kind": "topic"},
                "status": status, "offices": offices, "quote": quote, "page": page,
                "section": clean(row.get("section")), "refer_url": _url(row.get("refer_url")),
                "checked_at": source["collected_at"], "source": source,
            }

    def list_routes(
        self, *, dataset_version: str | None = None, identity_hash: str | None = None,
    ) -> dict[str, Any]:
        """The published shuttle routes (one per timetable), by name."""
        with self.snapshot() as snapshot:
            self._pin(snapshot, dataset_version, identity_hash)
            routes = sorted(
                ({"name": entity["name"], "entity_id": entity["id"]}
                 for entity in snapshot.entities if entity["kind"] == "route"),
                key=lambda route: route["name"])
            return {"dataset_version": snapshot.dataset_version,
                    "identity_hash": snapshot.identity_hash, "routes": routes}

    def get_route_facts(
        self,
        entity_id: str,
        dataset_version: str,
        *,
        identity_hash: str | None = None,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        """One shuttle route's published trips, with their source and its freshness."""
        if (not isinstance(entity_id, str) or not entity_id or len(entity_id) > 100
                or not isinstance(dataset_version, str) or not dataset_version):
            raise InvalidFactRequest("A canonical entity and dataset pin are required.")
        with self.snapshot() as snapshot:
            self._pin(snapshot, dataset_version, identity_hash)
            entity = next((e for e in snapshot.entities
                           if e["id"] == entity_id and e["kind"] == "route"), None)
            if not entity:
                raise UnknownEntity("No shuttle route has that canonical identity.")
            rows = snapshot.record_reader(entity, "shuttle_trips")
            # What each stop on the route is and where to wait there (the stop-descriptions page).
            stop_rows = snapshot.record_reader(entity, "shuttle_stops")
            if len(rows) > MAX_TRIP_ROWS:
                raise EvidenceUnavailable("Shuttle evidence exceeds the bounded read.")
            if len(linked_rows(entity, rows, "shuttle_trips")) != len(rows):
                raise EvidenceUnavailable("A shuttle trip has no exact canonical identity link.")
            ids = [_text(row.get("id")) for row in rows]
            if not all(ids) or len(set(ids)) != len(ids):
                raise EvidenceUnavailable("Duplicate or missing shuttle evidence identifiers.")
            now = as_of if as_of is not None else self.now()
            if now.tzinfo is None:
                raise InvalidFactRequest("The fact clock must include its timezone.")
            prop, sources = trips_property(rows, now)
            caveats = []
            for link in entity["links"]:
                if link.get("collection") != "shuttle_trips":
                    continue
                held = {row.get("source_record_key") for row in rows}
                missing = [key for key in link["source_record_keys"] if key not in held]
                if missing:
                    caveats.append(f"{len(missing)} linked shuttle trips are missing.")
            return {
                "schema_version": 4,
                "mapping_version": "entity-facts-6",
                "dataset_version": snapshot.dataset_version,
                "identity_hash": snapshot.identity_hash,
                "entity": {key: entity[key] for key in ("id", "kind", "name")},
                "properties": [prop, *([stops_property(stop_rows)] if stop_rows else [])],
                "sources": sources,
                "evidence_count": len(rows),
                "caveats": caveats,
                "complete": not caveats,
            }


class MemoryEntityFacts(EntityFacts):
    """Same resolver with explicit published fixtures; never a production fallback."""

    backend = "memory"

    def __init__(
        self,
        *,
        dataset_version: str,
        identity_hash: str,
        entities: list[dict[str, Any]],
        contacts: list[dict[str, Any]],
        alias_sources: list[dict[str, Any]] | None = None,
        schedules: list[dict[str, Any]] | None = None,
        records: dict[str, list[dict[str, Any]]] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(now=now)
        self.dataset_version = dataset_version
        self.identity_hash = identity_hash
        self.entities = deepcopy(validate_entities(entities))
        self.contacts = deepcopy(contacts)
        self.alias_sources = deepcopy(alias_sources or [])
        self.schedules = deepcopy(schedules or [])
        # collection -> rows, for the dining_hours, menu and shuttle_trips collections.
        self.records = deepcopy(records or {})

    @contextmanager
    def snapshot(self) -> Iterator[Snapshot]:
        entities, contacts = deepcopy(self.entities), deepcopy(self.contacts)
        schedules = deepcopy(self.schedules)
        records = deepcopy(self.records)
        yield Snapshot(
            self.dataset_version,
            self.identity_hash,
            validate_entities(entities),
            lambda entity: linked_contacts(entity, contacts),
            deepcopy(self.alias_sources),
            lambda entity: linked_schedules(entity, schedules),
            record_reader=lambda entity, collection: linked_rows(
                entity, records.get(collection, []), collection),
        )
