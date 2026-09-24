"""Data models and table schemas for campus retrieval."""

from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

CAMPUS_ZONE = ZoneInfo("America/New_York")
COLLECTIONS = (
    "documents",
    "critical_facts",
    "contacts",
    "campus_hours",
    "dining_hours",
    "menu",
    "calendar",
    "events",
    "clubs",
    "programs",
    "program_requirements",
    "courses",
    "faculty",
    "shuttle",
)
Collection = Literal[
    "documents",
    "critical_facts",
    "contacts",
    "campus_hours",
    "dining_hours",
    "menu",
    "calendar",
    "events",
    "clubs",
    "programs",
    "program_requirements",
    "courses",
    "faculty",
    "shuttle",
]
# Only public field values enter keyword ranking, never database IDs or ingestion metadata.
TABLES: dict[str, tuple[str, tuple[str, ...]]] = {
    "critical_facts": ("critical_facts", ("fact_key", "fact_value", "verified_at")),
    "contacts": (
        "campus_contacts",
        (
            "name",
            "type",
            "title",
            "status",
            "offices",
            "department",
            "phone",
            "email",
            "office",
            "prefers_email",
            "preferred_contact",
            "contact_note",
            "phones",
            "raw_phone",
            "phone_normalization_status",
        ),
    ),
    "campus_hours": ("campus_hours", ("name", "day", "schedule", "hours", "notes")),
    "dining_hours": ("dining_hours", ("name", "day", "schedule")),
    "menu": (
        "menu_items",
        ("meal", "station", "name", "calories", "portion_size", "vegan", "vegetarian", "allergens"),
    ),
    "calendar": (
        "academic_dates",
        ("term", "session", "family", "kind", "date_label", "title", "description", "starts_at"),
    ),
    "events": (
        "campus_events",
        (
            "title",
            "date_label",
            "starts_at",
            "start_time",
            "end_time",
            "organizer",
            "description",
            "event_url",
        ),
    ),
    "clubs": ("clubs", ("name", "category", "website_url")),
    "programs": (
        "programs",
        ("name", "degree", "program_kind", "school", "description", "program_url"),
    ),
    "shuttle": ("shuttle_trips", ("sequence", "departure", "arrival", "stops")),
}


class SearchFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=160,
        description=(
            "Exact published name. For menu this is a DISH name, never a venue. "
            "Use query to discover an unknown name; do not guess an exact filter."
        ),
    )
    meal: str | None = Field(default=None, min_length=1, max_length=80)
    vegan: bool | None = None
    vegetarian: bool | None = None
    term: str | None = Field(default=None, min_length=1, max_length=120)
    session: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
        description=(
            "Exact single-session label. Shared dates such as Full and Session I may have "
            "no single-session field. If a requested date is missing, search the same term "
            "with session null and verify applicability from the published title."
        ),
    )
    route: str | None = Field(default=None, min_length=1, max_length=160)


class SearchQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    collection: Collection = Field(
        description=(
            "Choose by record contents. critical_facts: concise verified campus facts and "
            "official service/action links, selected dates, charges, and emergency contacts. "
            "documents: campus policy, process and program page passages. "
            "contacts: directory phone, "
            "email, department, and office. campus_hours and dining_hours: dated opening "
            "schedules and exceptions. menu: dated items, meal, dietary flags, and allergens. "
            "calendar: academic dates by term and session. events: dated campus activities. "
            "clubs: organization directory, including departments; "
            "inspect each published category. "
            "programs: degrees and programs. "
            "program_requirements: detailed published curriculum. courses: catalog "
            "descriptions, not live registration. faculty: published faculty information. "
            "shuttle: scheduled routes, service days, and ordered stops, not live vehicles."
        )
    )
    query: str = Field(default="", max_length=500)
    date_from: date | None = Field(
        default=None,
        description=(
            "Requested service date for menus/hours/shuttles/events. For academic calendar "
            "queries by term, leave null unless the user explicitly restricts dates: "
            "past deadlines in that term are still relevant."
        ),
    )
    date_to: date | None = Field(
        default=None,
        description=(
            "Last requested date, or null. Do not restrict a term's academic dates to "
            "the current/future portion of the term."
        ),
    )
    limit: int = Field(default=12, ge=1, le=100)
    filters: SearchFilters | None = None

    @model_validator(mode="after")
    def check_range(self) -> SearchQuery:
        if self.collection in ("menu", "campus_hours", "dining_hours", "shuttle", "events"):
            if self.date_from is None:
                raise ValueError(
                    f"date_from is required for {self.collection}; supply the requested campus "
                    "calendar date explicitly instead of placing a day or date only in query"
                )
        if self.date_from and self.date_to and self.date_to < self.date_from:
            raise ValueError("date_to must be on or after date_from")
        allowed = {
            "contacts": {"name"},
            "clubs": {"name"},
            "programs": {"name"},
            "menu": {"name", "meal", "vegan", "vegetarian"},
            "calendar": {"term", "session"},
            "shuttle": {"route"},
            "campus_hours": {"name"},
            "dining_hours": {"name"},
        }.get(self.collection, set())
        if self.filters and not set(self.filters.model_dump(exclude_none=True)) <= allowed:
            raise ValueError("Filter is not supported for this collection")
        return self


class ReadQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[str] = Field(min_length=1, max_length=12)


class EntityQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_id: UUID = Field(description="Canonical entity ID returned by discovery or a profile.")
    properties: list[str] | None = Field(
        default=None, min_length=1, max_length=32,
        description=("Requested shared property keys (email, phones, offices, credits, etc.); "
                     "null for all."),
    )
