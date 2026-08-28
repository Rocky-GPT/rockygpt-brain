from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

RESOURCES_DIR = Path(__file__).parent.parent / "resources"


def as_trimmed_string(val: Any) -> str | None:
    if not isinstance(val, str):
        return None
    s = val.strip()
    return s if s else None


def normalize_url(val: Any) -> str | None:
    s = as_trimmed_string(val)
    if not s or not re.match(r"^https?://", s, re.IGNORECASE):
        return None
    return s


def normalize_whitespace(val: str) -> str:
    return re.sub(r"\s+", " ", val).strip()


def normalize_email(value: str | None, title: str | None) -> str | None:
    from_field = as_trimmed_string(value)
    if from_field and not re.match(r"^email us$", from_field, re.IGNORECASE):
        if "@" in from_field:
            return from_field.lower()
        return f"{from_field.lower()}@ramapo.edu"

    if not title:
        return None

    match = re.search(
        r"\bE-?mail:\s*([A-Za-z0-9._%+-]+(?:@[A-Za-z0-9.-]+\.[A-Za-z]{2,})?)\b",
        title,
        re.IGNORECASE,
    )
    from_title = as_trimmed_string(match.group(1)) if match else None
    if not from_title or re.match(r"^email us$", from_title, re.IGNORECASE):
        return None
    if "@" in from_title:
        return from_title.lower()
    return f"{from_title.lower()}@ramapo.edu"


def extract_extension(title: str | None) -> str | None:
    if not title:
        return None
    match = re.search(r"Ext[:.\s]*([0-9]{4})", title, re.IGNORECASE)
    return as_trimmed_string(match.group(1)) if match else None


def normalize_phone(value: str | None, title: str | None) -> str | None:
    from_field = as_trimmed_string(value)
    if from_field:
        digits = re.sub(r"\D", "", from_field)
        if len(digits) == 4:
            return f"(201) 684-{digits}"
        if len(digits) == 10:
            return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
        if len(digits) == 11 and digits.startswith("1"):
            ten = digits[1:]
            return f"({ten[:3]}) {ten[3:6]}-{ten[6:]}"
        return from_field

    ext = extract_extension(title)
    if not ext:
        return None
    return f"(201) 684-{ext}"


def extract_office(title: str | None) -> str | None:
    if not title:
        return None
    match = re.search(r"([A-Z]{1,4}-\d{2,4}[A-Z]?|[A-Z]\d{3}[A-Z]?)(?=Ext|$|[^A-Za-z0-9])", title)
    return as_trimmed_string(match.group(1)) if match else None


def clean_title(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value
    cleaned = re.sub(r"Liaison:[^|]+", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"Ext[:.\s]*\d{3,5}", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\|?\s*E-?mail:\s*[^|]+", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"([A-Z]{1,4}-\d{2,4}[A-Z]?|[A-Z]\d{3}[A-Z]?)(?=Ext|$|[^A-Za-z0-9])", " ", cleaned
    )
    cleaned = normalize_whitespace(cleaned)
    cleaned = re.sub(r"[|,:\-]\s*$", "", cleaned).strip()
    return cleaned if cleaned else None


def to_faculty_staff_contact(raw: dict[str, Any]) -> dict[str, Any] | None:
    name = as_trimmed_string(raw.get("name"))
    if not name:
        return None

    raw_title = as_trimmed_string(raw.get("title"))
    school = as_trimmed_string(raw.get("school"))
    email = normalize_email(as_trimmed_string(raw.get("email")), raw_title)
    phone = normalize_phone(as_trimmed_string(raw.get("phone")), raw_title)
    office = as_trimmed_string(raw.get("office")) or extract_office(raw_title)
    profile_url = normalize_url(raw.get("profileUrl"))
    image_url = normalize_url(raw.get("imageUrl"))
    title = clean_title(raw_title)

    res: dict[str, Any] = {"name": name}
    if title:
        res["title"] = title
    if school:
        res["school"] = school
    if email:
        res["email"] = email
    if phone:
        res["phone"] = phone
    if office:
        res["office"] = office
    if profile_url:
        res["profileUrl"] = profile_url
    if image_url:
        res["imageUrl"] = image_url
    return res


def merge_contacts(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    a_title = a.get("title")
    b_title = b.get("title")
    choose_title: str | None = None
    if not a_title:
        choose_title = b_title
    elif not b_title:
        choose_title = a_title
    else:
        choose_title = a_title if len(a_title) < len(b_title) else b_title

    res: dict[str, Any] = {"name": a["name"]}
    if choose_title:
        res["title"] = choose_title
    if a.get("school") or b.get("school"):
        res["school"] = a.get("school") or b.get("school")
    if a.get("email") or b.get("email"):
        res["email"] = a.get("email") or b.get("email")
    if a.get("phone") or b.get("phone"):
        res["phone"] = a.get("phone") or b.get("phone")
    if a.get("office") or b.get("office"):
        res["office"] = a.get("office") or b.get("office")
    if a.get("profileUrl") or b.get("profileUrl"):
        res["profileUrl"] = a.get("profileUrl") or b.get("profileUrl")
    if a.get("imageUrl") or b.get("imageUrl"):
        res["imageUrl"] = a.get("imageUrl") or b.get("imageUrl")
    return res


def to_search_text(values: list[str | None]) -> str:
    return " ".join(v.strip().lower() for v in values if v and v.strip())


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.lower())
    return re.sub(r"^-+|-+$", "", cleaned)


def build_directory_all_contacts(faculty_payload: Any) -> list[dict[str, Any]]:
    contacts_file = RESOURCES_DIR / "directory-contacts.json"
    with open(contacts_file) as f:
        static_data = json.load(f)

    offices = static_data.get("office", [])
    others = static_data.get("other", [])

    by_key: dict[str, dict[str, Any]] = {}
    if isinstance(faculty_payload, list):
        for row in faculty_payload:
            if not isinstance(row, dict):
                continue
            contact = to_faculty_staff_contact(row)
            if not contact:
                continue
            key = f"{contact['name'].lower()}|{(contact.get('school') or '').lower()}"
            existing = by_key.get(key)
            by_key[key] = merge_contacts(existing, contact) if existing else contact

    faculty_staff = sorted(by_key.values(), key=lambda c: c["name"].lower())

    normalized_offices: list[dict[str, Any]] = []
    for idx, entry in enumerate(offices):
        name = entry["name"]
        normalized_offices.append(
            {
                "id": f"office-{idx + 1}-{slugify(name)}",
                "bucket": "Offices",
                "kind": "office",
                "source": "office-static",
                "name": name,
                "category": entry["category"],
                "department": entry["department"],
                "email": entry.get("email"),
                "phone": entry.get("phone"),
                "office": entry.get("office"),
                "helpsWith": entry.get("helpsWith", []),
                "searchText": to_search_text(
                    [
                        name,
                        entry.get("phone"),
                        entry.get("category"),
                        entry.get("email"),
                        entry.get("department"),
                        entry.get("office"),
                        " ".join(entry.get("helpsWith", [])),
                    ]
                ),
            }
        )

    normalized_faculty: list[dict[str, Any]] = []
    for idx, entry in enumerate(faculty_staff):
        name = entry["name"]
        normalized_faculty.append(
            {
                "id": f"faculty-{idx + 1}-{slugify(name)}",
                "bucket": "Staff & Faculty",
                "kind": "person",
                "source": "faculty-dataset",
                "name": name,
                **({"title": entry["title"]} if "title" in entry else {}),
                **({"school": entry["school"]} if "school" in entry else {}),
                **({"email": entry["email"]} if "email" in entry else {}),
                **({"phone": entry["phone"]} if "phone" in entry else {}),
                **({"office": entry["office"]} if "office" in entry else {}),
                **({"profileUrl": entry["profileUrl"]} if "profileUrl" in entry else {}),
                **({"imageUrl": entry["imageUrl"]} if "imageUrl" in entry else {}),
                "searchText": to_search_text(
                    [
                        name,
                        entry.get("title"),
                        entry.get("school"),
                        entry.get("email"),
                        entry.get("phone"),
                        entry.get("office"),
                    ]
                ),
            }
        )

    normalized_others: list[dict[str, Any]] = []
    for idx, entry in enumerate(others):
        name = entry["name"]
        normalized_others.append(
            {
                "id": f"other-{idx + 1}-{slugify(name)}",
                "bucket": "Others",
                "kind": "person",
                "source": "other-static",
                "name": name,
                "title": entry["title"],
                "unit": entry["unit"],
                **({"email": entry["email"]} if "email" in entry else {}),
                **({"phone": entry["phone"]} if "phone" in entry else {}),
                **({"office": entry["office"]} if "office" in entry else {}),
                **({"profileUrl": entry["profileUrl"]} if "profileUrl" in entry else {}),
                "searchText": to_search_text(
                    [
                        name,
                        entry.get("title"),
                        entry.get("unit"),
                        entry.get("email"),
                        entry.get("phone"),
                        entry.get("office"),
                    ]
                ),
            }
        )

    return [*normalized_offices, *normalized_faculty, *normalized_others]


def load_map_locations() -> list[dict[str, Any]]:
    map_file = RESOURCES_DIR / "campus-map-data.json"
    with open(map_file) as f:
        data = json.load(f)

    locations: list[dict[str, Any]] = []
    for b in data.get("buildings", []):
        locations.append(
            {
                "key": b["key"],
                "name": b["name"],
                "type": "building",
                "mapUrl": b["mapUrl"],
                "aliases": b.get("aliases", []),
                "roomPrefixes": b.get("roomPrefixes", []),
                **({"category": b["category"]} if "category" in b else {}),
            }
        )
    for o in data.get("offices", []):
        locations.append(
            {
                "key": o["key"],
                "name": o["name"],
                "type": "office",
                "mapUrl": o["mapUrl"],
                "aliases": o.get("aliases", []),
                "roomPrefixes": [],
                **({"category": o["category"]} if "category" in o else {}),
                **({"buildingKey": o["buildingKey"]} if "buildingKey" in o else {}),
                **({"buildingName": o["buildingName"]} if "buildingName" in o else {}),
                **({"officeUrl": o["officeUrl"]} if "officeUrl" in o else {}),
                "room": o.get("room"),
            }
        )
    for p in data.get("parking", []):
        locations.append(
            {
                "key": p["key"],
                "name": p["name"],
                "type": "parking",
                "mapUrl": p["mapUrl"],
                "aliases": p.get("aliases", []),
                "roomPrefixes": [],
            }
        )
    for layer in data.get("layers", []):
        locations.append(
            {
                "key": layer["key"],
                "name": layer["name"],
                "type": "layer",
                "mapUrl": layer["mapUrl"],
                "aliases": layer.get("aliases", []),
                "roomPrefixes": [],
                **({"description": layer["description"]} if "description" in layer else {}),
            }
        )
    return locations


def course_credits(value: Any) -> str | None:
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        trimmed = value.strip()
        if not trimmed:
            return None
        if trimmed.startswith("{"):
            try:
                return course_credits(json.loads(trimmed))
            except Exception:
                return trimmed
        return trimmed
    if not isinstance(value, dict):
        return None
    min_v = value.get("min")
    max_v = value.get("max")
    try:
        min_n = float(min_v) if min_v is not None else None
    except (ValueError, TypeError):
        min_n = None
    try:
        max_n = float(max_v) if max_v is not None else None
    except (ValueError, TypeError):
        max_n = None

    if min_n is not None and min_n > 0 and max_n is not None and max_n > min_n:
        min_int = int(min_n) if min_n.is_integer() else min_n
        max_int = int(max_n) if max_n.is_integer() else max_n
        return f"{min_int}-{max_int}"
    if max_n is not None and max_n > 0:
        max_int = int(max_n) if max_n.is_integer() else max_n
        return str(max_int)
    if min_n is not None and min_n > 0:
        min_int = int(min_n) if min_n.is_integer() else min_n
        return str(min_int)
    return None


def load_course_subjects() -> list[dict[str, Any]]:
    path = RESOURCES_DIR / "course-subjects.json"
    with open(path) as f:
        data = json.load(f)
    return data if isinstance(data, list) else []
