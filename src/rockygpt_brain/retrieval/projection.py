"""Explicit contact field mappings; no inferred values or identity joins."""

import html
import re
from datetime import datetime
from typing import Any

OFFICE_FIELDS = (
    "name",
    "department",
    "email",
    "phones",
    "offices",
    "prefers_email",
    "preferred_contact",
    "contact_note",
    "website",
    "hours",
)
# Contact fields come from contact records. Hours come from linked schedule records instead.
CONTACT_FIELDS = tuple(field for field in OFFICE_FIELDS if field != "hours")
FIELD_ALIASES = {"phone": "phones", "office": "offices"}
_ROOM = re.compile(r"^([A-Z]{1,4})[ -]*(\d{3})(?:[ -]*([A-Z]))?$")
_PHONE = re.compile(r"^(?:\+?1[ .-]*)?\(?(\d{3})\)?[ .-]*(\d{3})[ .-]*(\d{4})$")
_EXTENSION = re.compile(r"^(?:ext(?:ension)?\.?|x)\s*:?\s*(\d+)$", re.IGNORECASE)


def clean(value: Any) -> Any:
    """Only the documented HTML/whitespace cleanup; text case stays significant."""
    if isinstance(value, str):
        return " ".join(html.unescape(value).split())
    if isinstance(value, list):
        return [clean(item) for item in value]
    if isinstance(value, dict):
        return {key: clean(item) for key, item in value.items()}
    return value


def _phone(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    match = _PHONE.fullmatch(value)
    return f"+1{''.join(match.groups())}" if match else value


def _room(value: str) -> str:
    match = _ROOM.fullmatch(value)
    return f"{match[1]}-{match[2]}{match[3] or ''}" if match else value


# Ordinary office websites stay on the college's own host. Athletics has one reviewed exception.
_OWN_SITE = re.compile(r"https://(?:www\.)?ramapo\.edu/[A-Za-z0-9._~/-]*")
_ATHLETICS_SITE = re.compile(r"https://(?:www\.)?ramapoathletics\.com/")
_ATHLETICS_DIRECTORY = "https://catalog.ramapo.edu/quicklinks/studentservices"


def _checked_at(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _website(row: dict[str, Any]) -> tuple[Any, Any]:
    """A reviewed office website, with one narrowly supported external Athletics site."""
    metadata = row.get("normalization_metadata")
    evidence = metadata.get("evidence") if isinstance(metadata, dict) else None
    claim = evidence.get("website") if isinstance(evidence, dict) else None
    if not isinstance(claim, dict):
        return None, None
    url = claim.get("url")
    if isinstance(url, str) and _OWN_SITE.fullmatch(url):
        return url, claim
    official = claim.get("official_link")
    if (row.get("source_record_key") == "office:athletics"
            and isinstance(url, str) and _ATHLETICS_SITE.fullmatch(url)
            and _checked_at(claim.get("checked_at")) and isinstance(official, dict)
            and isinstance(official.get("url"), str)
            and official.get("url") in {_ATHLETICS_DIRECTORY, _ATHLETICS_DIRECTORY + "/"}
            and _checked_at(official.get("checked_at"))
            and isinstance(official.get("section"), str) and official["section"].strip()
            and len(official["section"]) <= 300
            and not any(ord(char) < 32 for char in official["section"])):
        return url, claim
    return None, claim


def project_contact(row: dict[str, Any], field: str) -> tuple[Any, Any, list[str]]:
    """Return canonical value, untouched published observation, and caveats."""
    raw = row.get(field)
    caveats: list[str] = []
    if field == "website":
        # A provenance URL is not an assertion of the office's website; a reviewed claim is.
        url, claim = _website(row)
        return url, claim, caveats
    if field == "phones":
        raw = {"phones": row.get("phones"), "phone": row.get("phone")}
        phones = clean(row.get("phones"))
        if isinstance(phones, list) and phones:
            return (
                [
                    {**entry, "number": _phone(entry["number"])}
                    if isinstance(entry, dict) and "number" in entry
                    else entry
                    for entry in phones
                ],
                raw,
                caveats,
            )
        phone = clean(row.get("phone"))
        if phone:
            extension = _EXTENSION.fullmatch(phone) if isinstance(phone, str) else None
            if extension:
                return [{"extension": extension[1]}], raw, caveats
            normalized = _phone(phone)
            if normalized != phone or re.fullmatch(r"\+1\d{10}", str(phone)):
                return [{"number": normalized}], raw, caveats
            caveats.append("Unparsed phone text is retained; no missing digits are inferred.")
            return [{"text": phone}], raw, caveats
        return None, raw, caveats
    if field == "offices":
        raw = {"offices": row.get("offices"), "office": row.get("office")}
        values = clean(row.get("offices")) or [clean(row.get("office"))]
        if not isinstance(values, list):
            values = [values]
        result: list[Any] = []
        for value in values:
            if not value:
                continue
            if (isinstance(value, dict) and set(value) <= {"location", "label"}
                    and isinstance(value.get("location"), str)):
                # A service or person label is part of the observation, not a second office.
                result.append({**value, "location": _room(value["location"])})
                continue
            if not isinstance(value, str):
                result.append(value)
                continue
            parts = value.split("/")
            if all(_ROOM.fullmatch(part.strip()) for part in parts):
                result.extend(_room(part.strip()) for part in parts)
            else:
                result.append(_room(value))
        return result or None, raw, caveats
    if field == "prefers_email" and raw is False:
        caveats.append("Historical false means no observed contact preference.")
        return None, raw, caveats
    return clean(raw), raw, caveats
