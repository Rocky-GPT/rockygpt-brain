"""Conservative presentation cleanup; source artifacts and occurrence IDs stay intact."""

from __future__ import annotations

import html
import math
import re
from datetime import datetime
from typing import Any, TypeGuard

COLLECTIONS = {
    "critical_facts",
    "calendar",
    "events",
    "clubs",
    "programs",
    "program_requirements",
    "courses",
    "faculty",
    "shuttle",
}
TEXT_FIELDS = {
    "name",
    "title",
    "description",
    "bio",
    "school",
    "office",
    "section",
    "note",
    "program",
    "category",
    "careers",
    "program_page_excerpt",
    "organizer",
    "location",
}


def course_credits(value: Any) -> Any:
    """Retain explicit ranges, including zero; do not infer an absent upper bound."""
    if isinstance(value, str) and re.fullmatch(r"\d+(?:\.\d+)?", value.strip()):
        value = float(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value if math.isfinite(value) and value >= 0 else None
    if not isinstance(value, dict):
        return None
    if value.get("operator") not in (None, "", "TO"):
        return value  # Unknown operators retain their source meaning.
    minimum, maximum = value.get("min"), value.get("max")

    def valid(n: Any) -> TypeGuard[int | float]:
        return (
            isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n) and n >= 0
        )

    # The catalog uses an empty operator with max for a fixed credit value.
    if value.get("operator") == "" and valid(maximum) and minimum in (None, 0, maximum):
        return maximum
    if valid(minimum) and valid(maximum) and minimum <= maximum:
        return {"min": minimum, "max": maximum}
    return None


def normalize_record(record: dict[str, Any]) -> None:
    collection = record["collection"]
    if collection not in COLLECTIONS:
        return
    fields = record["fields"]
    coverage = record["coverage"]["fields"]
    for key, value in list(fields.items()):
        if key in TEXT_FIELDS and isinstance(value, str):
            fields[key] = html.unescape(value).replace("\xa0", " ").strip() or None
        elif isinstance(value, str) and not value.strip():
            fields[key] = None
        if fields[key] is None or fields[key] == []:
            coverage[key] = "not_published"
        elif key in ("starts_at", "verified_at"):
            try:
                fields[key] = datetime.fromisoformat(str(fields[key])).isoformat()
            except ValueError:
                pass
    if collection == "courses" and "credits" in fields:
        fields["credits"] = course_credits(fields["credits"])
        if fields["credits"] is None:
            coverage["credits"] = "not_published"
    if collection == "events":
        location = fields.get("location")
        access = {
            "Private Location (sign in to display)": "sign_in_required",
            "Private Location (register to display)": "registration_required",
        }
        if location in access:
            fields["location_access"] = access[location]
            fields["location"] = None
            coverage["location_access"] = "published"
            coverage["location"] = "not_published"
        elif location in ("-", "—", ""):
            fields["location"] = None
            coverage["location"] = "not_published"
    if collection == "programs" and "careers" in fields:
        # Legacy enrichment sliced text after a keyword anywhere in a page,
        # including mid-sentence. That does not establish a Careers section.
        fields["program_page_excerpt"] = fields.pop("careers")
        coverage["program_page_excerpt"] = coverage.pop("careers", "published")
        note = "Program page excerpts are partial source text, not a verified careers section."
        if note not in record["limitations"]:
            record["limitations"].append(note)


def faculty_phone_display(phone: Any) -> Any:
    """Format only whole US numbers; source evidence and annotations stay intact."""
    if isinstance(phone, str) and re.fullmatch(r"\+?[0-9().\s-]+", phone):
        digits = re.sub(r"\D", "", phone)
        if phone.startswith("+") and not (len(digits) == 11 and digits.startswith("1")):
            return phone
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        if len(digits) == 10:
            return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return phone
