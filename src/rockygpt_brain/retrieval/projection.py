"""Explicit contact field mappings; no inferred values or identity joins."""

import html
import re
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
)
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


def project_contact(row: dict[str, Any], field: str) -> tuple[Any, Any, list[str]]:
    """Return canonical value, untouched published observation, and caveats."""
    raw = row.get(field)
    caveats: list[str] = []
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
    # A provenance URL is not an assertion of the office's website.
    return clean(raw), raw, caveats
