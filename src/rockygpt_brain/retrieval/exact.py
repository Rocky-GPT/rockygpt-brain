"""Conservative, server-rendered contact answers after the model requests evidence.

Eligibility is a complete question shape, never a pre-model intent classifier.
Unrecognized wording, follow-ups and mixed tasks stay in the reviewed prose path.
"""

import re
from datetime import date
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import Field

from rockygpt_brain.contracts import Answer, ChatMessage, StrictModel

ContactField = Literal["phone", "email", "office", "department", "fax", "hours", "website"]
CONTACT_FIELDS = ("phone", "email", "office", "department")


class ContactQuery(StrictModel):
    entity: str = Field(min_length=1, max_length=160, description="Published name or alias.")
    fields: list[ContactField] = Field(min_length=1, max_length=7)


def requested_fields(question: str, entity: str) -> set[str] | None:
    """Recognize only full contact question templates with an explicit entity slot."""
    question = " ".join(question.casefold().replace("’", "'").split()).rstrip("?.!")
    name = re.escape(" ".join(entity.casefold().replace("’", "'").split()))
    slot = rf"(?:the )?{name}"
    if re.fullmatch(
        rf"(?:please )?how (?:can|do) i (?:contact|reach|get in touch with) {slot}", question
    ):
        return set(CONTACT_FIELDS)
    if re.fullmatch(rf"where is {slot}(?:'s)?(?: office)?", question):
        return {"office"}
    prefix = r"(?:(?:what (?:is|are)|give me|show me|can you give me) (?:the )?)?"
    suffix = r"(?: please)?"
    patterns = [
        rf"{prefix}{slot}(?:'s)? (?P<fields>.+){suffix}",
        rf"{prefix}(?P<fields>.+) (?:for|of) {slot}{suffix}",
    ]
    vocabulary = {
        "phone": "phone",
        "phone number": "phone",
        "telephone": "phone",
        "email": "email",
        "email address": "email",
        "office": "office",
        "office location": "office",
        "location": "office",
        "department": "department",
        "fax": "fax",
        "fax number": "fax",
        "hours": "hours",
        "office hours": "hours",
        "website": "website",
    }
    for pattern in patterns:
        matched = re.fullmatch(pattern, question)
        if not matched:
            continue
        words = matched["fields"].removesuffix(" please")
        if words in {"contact information", "contact details"}:
            return set(CONTACT_FIELDS)
        parts = re.split(r"\s*(?:,\s*(?:and )?| and | & )\s*", words)
        if parts and all(part in vocabulary for part in parts):
            return {vocabulary[part] for part in parts}
    return None


def plain(value: str) -> str:
    """Published strings are text, not Markdown instructions or links."""
    return re.sub(r"([\\`*_{}\[\]<>#])", r"\\\1", " ".join(value.split()))


def contact_answer(
    messages: list[ChatMessage],
    query: ContactQuery,
    output: dict[str, Any],
    today: date,
) -> Answer | None:
    if len(messages) != 1:
        return None
    requested = requested_fields(messages[0].content, query.entity)
    if requested is None or not requested <= set(query.fields):
        return None

    def limitation(text: str, status: str = "unavailable") -> Answer:
        return Answer.model_validate(
            {
                "status": status,
                "parts": [
                    {
                        "kind": "clarification" if status == "clarification" else "limitation",
                        "text": text,
                        "evidence_ids": [],
                    }
                ],
            }
        )

    if output.get("status") == "unavailable":
        return limitation(
            "The campus directory could not be read just now. Please try again later."
        )
    if output.get("status") != "ok":
        return None
    if output.get("truncated"):
        return limitation(
            "The directory lookup is incomplete. Please specify the full office name.",
            "clarification",
        )
    records = output.get("records", [])
    if not records:
        return limitation(
            "I couldn't find that exact name in the published directory. "
            "Please provide its full name.",
            "clarification",
        )
    if len({r.get("entity_id") for r in records}) != 1:
        return limitation(
            "That name identifies more than one directory entry. "
            "Please specify the full name and department.",
            "clarification",
        )
    for record in records:
        url = urlparse(record.get("url", ""))
        if (
            not record.get("entity_id")
            or not record.get("source_key")
            or record.get("collection") != "contacts"
            or record.get("trust_tier") not in {"official_primary", "official_secondary"}
            or url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or record.get("freshness") not in {"fresh", "static"}
            or record.get("content_truncated")
            or record.get("limitations")
            or output.get("match") != "exact"
        ):
            return limitation(
                "I couldn't verify current contact details for that exact directory entry."
            )
        for key in ("valid_from", "valid_until"):
            try:
                applicable_date = date.fromisoformat(record[key]) if record.get(key) else None
                if applicable_date and (
                    (key == "valid_from" and applicable_date > today)
                    or (key == "valid_until" and applicable_date < today)
                ):
                    return limitation("The published contact record doesn't apply to today's date.")
            except (ValueError, TypeError):
                return limitation("The contact record's applicability could not be verified.")
        if not requested <= set(record.get("coverage", {}).get("fields", {})):
            return limitation(
                "The directory does not establish coverage for all the requested details."
            )
    values: dict[str, str] = {}
    missing = []
    for field in sorted(requested):
        candidates = {r.get("fields", {}).get(field) for r in records}
        if len(candidates) > 1:
            return limitation(
                "The published directory contains conflicting contact details. "
                "I can't choose a reliable value."
            )
        value = candidates.pop()
        if not isinstance(value, str) or not value.strip():
            missing.append(field)
        elif any(r["coverage"]["fields"][field] != "published" for r in records):
            return limitation(
                "The requested contact details are not verified by the directory's field coverage."
            )
        else:
            values[field] = value
    parts = []
    if values:
        lines = [plain(records[0]["title"])]
        if any(r.get("fields", {}).get("status") == "retired" for r in records):
            lines.append("Status: Retired (as listed in the directory).")
        lines.extend(f"{field.capitalize()}: {plain(value)}" for field, value in values.items())
        parts.append(
            {
                "kind": "campus_fact",
                "text": "\n\n".join(lines),
                "evidence_ids": [r["id"] for r in records],
            }
        )
    if missing:
        parts.append(
            {
                "kind": "limitation",
                "text": "The published directory does not provide: " + ", ".join(missing) + ".",
                "evidence_ids": [],
            }
        )
    return Answer.model_validate(
        {
            "status": "partial" if values and missing else "answered" if values else "unavailable",
            "parts": parts,
        }
    )
