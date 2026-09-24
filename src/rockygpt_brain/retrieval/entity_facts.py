"""The shared entity fact read model for chat, APIs and user interfaces.

Identity bindings select evidence; this module resolves its representations. Raw
assertions are immutable provenance. Equality never establishes independence,
current applicability, or campus authority. Repeated records retain their scope.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from rockygpt_brain.retrieval.projection import Projection, stable_id
from rockygpt_brain.retrieval.projection_models import (
    Assertion,
    Contract,
    CoverageIssue,
    Entity,
    Property,
    RecordSection,
    Relationship,
    SourceRecord,
)

FACT_VERSION: Literal["entity-facts-1"] = "entity-facts-1"
RETIREMENT_SUFFIX = re.compile(r"(?:\s*[-–—]\s*Retired|\s*\(Retired\)|^Retired)$", re.I)


class FactValue(Contract):
    id: str
    value: Any
    assertion_ids: list[str]
    supporting_evidence_ids: list[str]
    evidence_count: int = Field(ge=1)
    valid_from: str | None
    valid_until: str | None


class FactProperty(Property):
    status: Literal["known", "unknown", "conflicting", "multiple"]
    category: Literal["contact", "academic", "links", "details"]
    values: list[FactValue]

    @model_validator(mode="after")
    def evidence_resolves(self) -> FactProperty:
        assertions = {a.id: a for a in self.assertions}
        assigned = [identifier for value in self.values for identifier in value.assertion_ids]
        if (
            len(assertions) != len(self.assertions)
            or len(assigned) != len(set(assigned))
            or set(assigned) != set(assertions)
        ):
            raise ValueError("Every assertion must belong to exactly one fact value")
        if len({v.id for v in self.values}) != len(self.values):
            raise ValueError("Fact value IDs must be unique")
        for value in self.values:
            expected = {assertions[a].source_id for a in value.assertion_ids}
            if (
                set(value.supporting_evidence_ids) != expected
                or len(value.supporting_evidence_ids) != len(expected)
                or value.evidence_count != len(expected)
            ):
                raise ValueError("Fact support must match original assertion evidence")
        return self


class FactRecord(Contract):
    id: str
    label: str
    record_type: str
    source_id: str
    context: list[FactProperty]
    properties: list[FactProperty]
    relationships: list[Relationship]
    sections: list[RecordSection] = Field(default_factory=list)


class FactRecordGroup(Contract):
    key: str
    label: str
    record_type: str
    records: list[FactRecord]
    total: int
    returned: int
    next_cursor: str | None
    filters: dict[str, str | None]
    filter_fields: list[str]
    ordering: str
    title_field: str | None = None
    section_fields: list[str] = Field(default_factory=list)
    summary_field: str | None = None


class EntityFactProjection(Contract):
    schema_version: Literal[3] = 3
    projection_version: Literal["entity-facts-1"] = FACT_VERSION
    dataset_version: str
    identity_hash: str
    entity: Entity
    selected_record_group: str | None = None
    properties_complete: bool
    properties: list[FactProperty]
    record_groups: list[FactRecordGroup]
    relationships: list[Relationship]
    sources: list[SourceRecord]
    coverage: list[CoverageIssue]

    @model_validator(mode="after")
    def sources_resolve(self) -> EntityFactProjection:
        listed = {s.id for s in self.sources}
        if len(listed) != len(self.sources):
            raise ValueError("Source records must be listed once")
        records = [r for g in self.record_groups for r in g.records]
        properties = [*self.properties, *(p for r in records for p in [*r.context, *r.properties])]
        referenced = {a.source_id for p in properties for a in p.assertions}
        referenced.update(r.source_id for r in records)
        referenced.update(
            s.derived_from_source_id for s in self.sources if s.derived_from_source_id is not None
        )
        if not referenced <= listed:
            raise ValueError("Facts must reference a listed source record")
        return self


def _key(value: Any) -> str:
    # JSON type matters: false != 0, strings are not case-folded, arrays keep order.
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _category(key: str) -> Literal["contact", "academic", "links", "details"]:
    if key in {"email", "phones", "offices", "preferred_contact", "prefers_email", "contact_note"}:
        return "contact"
    if key.endswith("_url"):
        return "links"
    if key in {
        "school",
        "department",
        "education",
        "profile_courses",
        "teaching_interests",
        "research_interests",
        "published_research",
        "degree",
        "program_kind",
        "credits",
        "learning_goals_and_outcomes",
        "sample_graduation_plan",
        "concentrations",
        "program_level",
        "degree_designations",
        "convening_groups",
        "prerequisites",
        "requirements",
        "total_credits",
        "gpa",
        "semesters",
        "general_education",
    }:
        return "academic"
    return "details"


def _phone_number(value: str) -> str:
    digits = re.sub(r"[ ()+.\-]", "", value.strip())
    if digits.isdigit() and len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if digits.isdigit() and len(digits) == 10:
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    return value.strip()


def _phone(value: Any) -> Any:
    if _empty(value):
        return None
    if isinstance(value, str):
        if not value.strip():
            return None
        original = value.strip()
        extension = re.fullmatch(r"(?:ext\.?|extension|x)\s*(\d+)", value.strip(), re.I)
        if extension:
            return [{"extension": extension[1]}]
        # Split only explicit separators, never infer missing area codes or digits.
        parts = re.split(r"\s+(?:/|or)\s+", value.strip(), flags=re.I)
        if len(parts) > 1:
            return [item for part in parts for item in _phone(part)]
        label = re.search(r"\s*\((office|cell|mobile|fax|home|direct)\)\s*$", value, re.I)
        kind = label[1].lower() if label else None
        if label:
            value = value[: label.start()].strip()
        # Only unambiguous single-number display forms. Keep unparsed text intact.
        matched = re.fullmatch(r"(.+?)(?:\s+(?:ext\.?|extension|x)\s*(\d+))?", value.strip(), re.I)
        if matched is None:
            return [{"number": original}]
        item: dict[str, Any] = {"number": _phone_number(matched[1])}
        if matched[2]:
            item["extension"] = matched[2]
        if kind:
            item["type"] = "cell" if kind == "mobile" else kind
        return [item]
    if isinstance(value, list):
        return [
            {**item, **({"number": _phone_number(item["number"])} if "number" in item else {})}
            for item in value
        ]
    return value


def _office(value: Any) -> Any:
    if _empty(value):
        return None
    room = r"([A-Z]{1,4})[ -]*(\d{3})(?:[ -]*([A-Z]))?"

    def normalize(text: str) -> str:
        cleaned = " ".join(text.split())
        code = re.fullmatch(room, cleaned)
        return f"{code[1]}-{code[2]}{code[3] or ''}" if code else cleaned

    output: list[str] = []
    for text in [value] if isinstance(value, str) else value:
        parts = [" ".join(part.split()) for part in text.split("/")]
        # A slash can be part of a location name. Split only explicit room codes.
        if len(parts) > 1 and all(re.fullmatch(room, p) for p in parts):
            output.extend(normalize(p) for p in parts)
        else:
            output.append(normalize(text))
    return output or None


def _normalized(
    key: str, assertion: Assertion, source: SourceRecord, record: dict[str, Any]
) -> Any:
    field = assertion.field_path[0]
    cleaned = record["fields"].get(field, assertion.value)
    if key == "name" and isinstance(cleaned, str):
        return " ".join(cleaned.split())
    if source.collection in {"contacts", "faculty"} and isinstance(cleaned, str):
        if key in {"title", "department"} and RETIREMENT_SUFFIX.search(cleaned):
            assertion.limitations.append("The source explicitly marks this person retired.")
            return RETIREMENT_SUFFIX.sub("", cleaned).strip() or None
        if key == "status" and field in {"title", "department"}:
            return "retired" if RETIREMENT_SUFFIX.search(cleaned) else None
    if key == "starts_at" and record["coverage"]["fields"].get(field) == "date_only":
        assertion.limitations.append(
            "An event date is published, but its start time is unavailable."
        )
        return str(cleaned)[:10]
    if key == "phones":
        value = cleaned
        if isinstance(value, str):
            # Faculty profiles sometimes attach an email instruction to the phone.
            # It qualifies the number, rather than being part of a dialable number.
            notes = re.findall(r"\([^)]*\be-?mail\b[^)]*\)", value, flags=re.I)
            for note in notes:
                assertion.limitations.append(f"Source phone note: {note[1:-1]}")
                value = value.replace(note, "").strip()
        return _phone(value)
    if key == "offices":
        return _office(cleaned)
    if source.collection == "contacts" and key in {"prefers_email", "preferred_contact"}:
        # Historical parser flags included any email mention. Only explicit supported
        # wording establishes preference; preserve everything else as raw provenance.
        note = record["fields"].get("contact_note")
        explicit = isinstance(note, str) and re.fullmatch(
            r"(?:best to use e-?mail|use e-?mail instead|e-?mail preferred|prefers? e-?mail)[.!]?",
            note.strip(),
            re.I,
        )
        if assertion.value is False or (assertion.value in (True, "email") and not explicit):
            return None
    if source.collection == "menu" and key in {"vegan", "vegetarian", "allergens"}:
        if assertion.publication_status != "published":
            return None
    return cleaned


def _overlap(a: FactValue, b: FactValue) -> bool:
    return not (
        (a.valid_until and b.valid_from and a.valid_until < b.valid_from)
        or (b.valid_until and a.valid_from and b.valid_until < a.valid_from)
    )


def canonical_properties(
    properties: list[Property], sources: list[SourceRecord]
) -> list[FactProperty]:
    """Resolve already entity-scoped assertions; callers must not pass unrelated rows.

    Only declared representation aliases are combined. Unknown observations are
    retained as provenance and do not contradict published values. Date intervals
    remain separate; differing overlapping values are an explicit disagreement.
    """
    source_map = {s.id: s for s in sources}
    # Reuse the existing conservative source cleanup for all consumers. It operates
    # on copies; original assertions remain available for source inspection.
    from rockygpt_brain.retrieval.normalization import normalize_record

    normalized: dict[str, dict[str, Any]] = {
        s.id: {
            "collection": s.collection,
            "fields": {},
            "coverage": {"fields": {}},
            "limitations": [],
        }
        for s in sources
    }
    for prop in properties:
        for assertion in prop.assertions:
            if len(assertion.field_path) == 1:
                normalized[assertion.source_id]["fields"][assertion.field_path[0]] = deepcopy(
                    assertion.value
                )
    for record in normalized.values():
        normalize_record(record)
    derived_status = [
        a
        for p in properties
        if p.key in {"title", "department"}
        for a in p.assertions
        if source_map[a.source_id].collection in {"contacts", "faculty"}
        and isinstance(value := normalized[a.source_id]["fields"].get(a.field_path[0]), str)
        and RETIREMENT_SUFFIX.search(value.strip())
    ]
    if derived_status:
        properties = [
            *properties,
            Property(key="status", label="status", value_type="text", assertions=derived_status),
        ]
    merged: dict[str, Property] = {}
    for prop in properties:
        key = {"phone": "phones", "office": "offices"}.get(prop.key, prop.key)
        if key not in merged:
            merged[key] = Property(
                key=key,
                label={"phones": "phone", "offices": "office", "type": "source record type"}.get(
                    key, prop.label
                ),
                value_type={"phones": "phone_list", "offices": "text_list"}.get(
                    key, prop.value_type
                ),
                assertions=[],
            )
        merged[key].assertions.extend(a.model_copy(deep=True) for a in prop.assertions)
    result = []
    for key, prop in merged.items():
        groups: dict[str, FactValue] = {}
        for assertion in prop.assertions:
            source = source_map[assertion.source_id]
            value = _normalized(key, assertion, source, normalized[source.id])
            if key == "starts_at" and source.collection == "events":
                prop.value_type = "date_or_datetime"
            if key == "prefers_email" and source.collection == "contacts":
                assertion.limitations.append(
                    "Legacy preference flags are parser observations; absence does not mean "
                    "email is refused, and an email mention alone does not establish preference."
                )
            group_key = _key([value, source.valid_from, source.valid_until])
            if group_key not in groups:
                groups[group_key] = FactValue(
                    id=stable_id(FACT_VERSION, key, group_key),
                    value=value,
                    assertion_ids=[],
                    supporting_evidence_ids=[],
                    evidence_count=1,
                    valid_from=source.valid_from,
                    valid_until=source.valid_until,
                )
            group = groups[group_key]
            group.assertion_ids.append(assertion.id)
            if source.id not in group.supporting_evidence_ids:
                group.supporting_evidence_ids.append(source.id)
            group.evidence_count = len(group.supporting_evidence_ids)
        for group_key, group in groups.items():
            group.id = stable_id(FACT_VERSION, key, group_key, sorted(group.assertion_ids))
        known = [g for g in groups.values() if not _empty(g.value)]
        conflicting = any(
            _key(a.value) != _key(b.value) and _overlap(a, b)
            for i, a in enumerate(known)
            for b in known[i + 1 :]
        )
        status: Literal["known", "unknown", "conflicting", "multiple"] = (
            "unknown"
            if not known
            else "conflicting"
            if conflicting
            else "multiple"
            if len({_key(g.value) for g in known}) > 1
            else "known"
        )
        result.append(
            FactProperty(
                **prop.model_dump(),
                values=list(groups.values()),
                status=status,
                category=_category(key),
            )
        )
    return result


def _lineage(properties: list[Property], sources: list[SourceRecord]) -> list[SourceRecord]:
    """Link only exact publisher derivations already scoped to this canonical entity."""
    raw: dict[str, dict[str, Any]] = {}
    for prop in properties:
        for a in prop.assertions:
            raw.setdefault(a.source_id, {})[prop.key] = a.value
    faculty = [s for s in sources if s.collection == "faculty"]

    def slug(value: Any) -> str:
        return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")

    output = []
    for source in sources:
        source = source.model_copy(deep=True)
        if source.collection == "contacts" and source.source_key == "faculty":
            contact = raw.get(source.id, {})
            matches = []
            for candidate in faculty:
                row = raw.get(candidate.id, {})
                key = (
                    f"faculty:{slug(row.get('name'))}:{slug(row.get('school')) or 'unknown-school'}"
                )
                contact_email, faculty_email = contact.get("email"), row.get("email")
                if source.source_record_key == key and (
                    not contact_email
                    or not faculty_email
                    or contact_email.strip().lower() == faculty_email.strip().lower()
                ):
                    matches.append(candidate)
            if len(matches) == 1:
                source.derived_from_source_id = matches[0].id
                source.limitations.append(
                    "Derived from the linked faculty profile; these records "
                    "are not independent corroboration."
                )
        output.append(source)
    return output


class EntityFacts:
    def __init__(self, data: Any, snapshot: dict[str, Any] | None = None) -> None:
        self.data = data
        self.source_records: dict[str, dict[str, Any]] = {}
        if snapshot is None:
            data._ensure_loaded()
            snapshot = {
                "dataset_version": data.dataset["version"],
                "identity_hash": data.identity_readiness().get("artifact_hash"),
            }
        self.snapshot = snapshot

    def build(
        self,
        entity_id: UUID | str,
        *,
        include_records: bool = False,
        group: str | None = None,
        filters: dict[str, Any] | None = None,
        limit: int = 8,
        cursor: str | None = None,
    ) -> EntityFactProjection:
        reader = Projection(self.data, self.snapshot, version=FACT_VERSION)
        projection = reader.build(
            UUID(str(entity_id)),
            group,
            filters or {},
            limit,
            cursor,
            include_records=include_records,
        )
        self.source_records = reader.source_records
        sources = _lineage(projection.properties, projection.sources)
        payload = projection.model_dump(
            exclude={
                "schema_version",
                "projection_version",
                "properties",
                "record_groups",
                "sources",
            }
        )
        groups = []
        for record_group in projection.record_groups:
            records = [
                FactRecord(
                    **r.model_dump(exclude={"context", "properties"}),
                    context=canonical_properties(r.context, sources),
                    properties=canonical_properties(r.properties, sources),
                )
                for r in record_group.records
            ]
            groups.append(
                FactRecordGroup(**record_group.model_dump(exclude={"records"}), records=records)
            )
        return EntityFactProjection(
            **payload,
            properties=canonical_properties(projection.properties, sources),
            record_groups=groups,
            sources=sources,
        )
