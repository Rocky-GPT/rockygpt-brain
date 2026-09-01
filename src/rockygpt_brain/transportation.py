"""Typed contract for RockyGPT's bounded shuttle capability.

This module defines data shapes only. It does not interpret language, read the
database, calculate schedules, or generate answers.
"""

from datetime import date, datetime, time
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, RootModel, StringConstraints, model_validator

AROUND_WINDOW_MINUTES = 15
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
HashText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
ServiceDay = Literal["weekday", "saturday", "sunday"]


class ContractModel(BaseModel):
    """Strict base for every shuttle contract object."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class UpcomingDay(ContractModel):
    """The first service date with a matching trip at or after evaluation time."""

    kind: Literal["upcoming"]


class RelativeDay(ContractModel):
    """Today or tomorrow, resolved in campus time by deterministic code."""

    kind: Literal["relative"]
    days_from_today: Literal[0, 1]


class NamedWeekday(ContractModel):
    """The next occurrence of a named weekday, including today."""

    kind: Literal["named_weekday"]
    weekday: Literal["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


class ServiceDayTemplate(ContractModel):
    """One recurring schedule template stored by the trusted database."""

    kind: Literal["service_day"]
    service_day: ServiceDay


class CalendarDay(ContractModel):
    """A calendar date mapped to a service-day template by deterministic code."""

    kind: Literal["calendar_date"]
    date: date


ShuttleDay = Annotated[
    UpcomingDay | RelativeDay | NamedWeekday | ServiceDayTemplate | CalendarDay,
    Field(discriminator="kind"),
]


class ShuttleTimeConstraint(ContractModel):
    """An exact or fixed-window clock-time availability check."""

    relation: Literal["at", "around"]
    clock: time
    basis: Literal["departure", "arrival"]

    @model_validator(mode="after")
    def require_minute_precision(self) -> Self:
        if self.clock.second or self.clock.microsecond:
            raise ValueError("clock must have minute precision")
        return self


class ShuttleQuery(ContractModel):
    """One deterministic schedule lookup, before any database values are attached."""

    day: ShuttleDay
    selection: Literal["next", "all"]
    count: int | None = Field(default=None, ge=1, le=10)
    offset: int = Field(default=0, ge=0, le=10)
    route_mention: ShortText | None = None
    origin_mention: ShortText | None = None
    destination_mention: ShortText | None = None
    time: ShuttleTimeConstraint | None = None

    @model_validator(mode="after")
    def validate_selection(self) -> Self:
        if self.selection == "next" and self.count is None:
            raise ValueError("next selection requires count")
        if self.selection == "all":
            if self.count is not None or self.offset != 0:
                raise ValueError("all selection cannot use count or offset")
            if isinstance(self.day, UpcomingDay):
                raise ValueError("all selection requires a bounded day")
        return self


class ShuttleQueryRequest(ContractModel):
    """A trip listing or an exact/around-time availability question."""

    kind: Literal["query"]
    answer_kind: Literal["trips", "availability"]
    query: ShuttleQuery
    show: Literal["departure", "arrival", "both"]

    @model_validator(mode="after")
    def validate_answer_kind(self) -> Self:
        if self.answer_kind == "availability":
            if self.query.time is None or self.query.selection != "all":
                raise ValueError("availability requires a timed all-trips query")
        elif self.query.time is not None:
            raise ValueError("trip listings cannot carry an availability time")
        return self


class ShuttleComparisonRequest(ContractModel):
    """A comparison of exactly two bounded schedule queries."""

    kind: Literal["comparison"]
    queries: tuple[ShuttleQuery, ShuttleQuery]
    show: Literal["departure", "arrival", "both"]

    @model_validator(mode="after")
    def require_full_schedules(self) -> Self:
        if any(query.selection != "all" or query.time is not None for query in self.queries):
            raise ValueError("comparisons require two untimed all-trips queries")
        return self


class ShuttleClarificationRequest(ContractModel):
    """A shuttle request that cannot yet be resolved to one deterministic query."""

    kind: Literal["clarification"]
    reason: Literal[
        "ambiguous_request",
        "ambiguous_reference",
        "interpretation_failure",
    ]


class UnsupportedShuttleRequest(ContractModel):
    """A shuttle request whose facts do not exist in the trusted schedule data."""

    kind: Literal["unsupported"]
    reason: Literal[
        "live_status",
        "service_exception",
        "capacity",
        "fare",
        "accessibility",
        "booking",
        "unpublished_schedule",
        "other_missing_data",
    ]


ShuttleRequestValue = Annotated[
    ShuttleQueryRequest
    | ShuttleComparisonRequest
    | ShuttleClarificationRequest
    | UnsupportedShuttleRequest,
    Field(discriminator="kind"),
]


class ShuttleRequest(RootModel[ShuttleRequestValue]):
    """The one transportation-specific request selected from a conversation."""


class ShuttleTimedFact(ContractModel):
    """A trusted clock label and its calculated campus-time instant, when parseable."""

    label: ShortText
    at: datetime | None


class ShuttleStopFact(ContractModel):
    """One trusted stop occurrence on a trip."""

    location: ShortText
    time: ShuttleTimedFact


class ShuttleTripFact(ContractModel):
    """One ordered trusted trip selected by deterministic code."""

    trip_id: Identifier
    source_record_key: Identifier
    route: ShortText
    service_date: date
    service_day: ServiceDay
    departure: ShuttleTimedFact
    stops: list[ShuttleStopFact]
    arrival: ShuttleTimedFact
    matched_origin: ShuttleStopFact | None = None
    matched_destination: ShuttleStopFact | None = None
    minutes_until: int | None = Field(default=None, ge=0)
    source_id: Identifier
    content_hash: HashText


class ResolvedShuttleDay(ContractModel):
    """The date/template that deterministic date handling actually used."""

    label: ShortText
    service_date: date | None
    service_day: ServiceDay


class ShuttleQueryResult(ContractModel):
    """The ordered records and completeness for one resolved query."""

    resolved_day: ResolvedShuttleDay
    records: list[ShuttleTripFact]
    matched_count: int = Field(ge=0)
    truncated: bool
    around_window_minutes: Literal[15] | None = None

    @model_validator(mode="after")
    def validate_completeness(self) -> Self:
        if self.matched_count < len(self.records):
            raise ValueError("matched_count cannot be smaller than returned records")
        return self


class ShuttleScheduleSummary(ContractModel):
    """Deterministic facts used to compare one side of two schedules."""

    label: ShortText
    trip_count: int = Field(ge=0)
    first_departure_at: datetime | None
    last_departure_at: datetime | None


class ShuttleComparisonFact(ContractModel):
    """The deterministic comparison of two resolved schedules."""

    left: ShuttleScheduleSummary
    right: ShuttleScheduleSummary
    right_minus_left_trip_count: int


class ShuttleSource(ContractModel):
    """Trusted source metadata retained from the selected database rows."""

    source_id: Identifier
    title: ShortText
    url: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]
    trust_tier: Literal["official_primary", "official_secondary", "community"]
    freshness_sla_hours: int = Field(gt=0)
    collected_at: datetime


class ShuttleProvenance(ContractModel):
    """The active database release and sources behind a result."""

    dataset_version: Identifier
    dataset_activated_at: datetime
    source_commit_sha: Identifier | None = None
    sources: list[ShuttleSource] = Field(min_length=1)


class ShuttleResult(ContractModel):
    """A deterministic shuttle result or an explicit non-answer state."""

    outcome: Literal["success", "empty", "no_match", "needs_clarification", "unsupported"]
    request: ShuttleRequestValue
    evaluated_at: datetime
    query_results: list[ShuttleQueryResult] = Field(default_factory=list)
    comparison: ShuttleComparisonFact | None = None
    candidates: list[ShortText] = Field(default_factory=list)
    provenance: ShuttleProvenance | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        executed = self.outcome in {"success", "empty", "no_match"}
        if executed and (not self.query_results or self.provenance is None):
            raise ValueError("executed results require query results and provenance")
        if self.outcome == "unsupported" and not isinstance(
            self.request, UnsupportedShuttleRequest
        ):
            raise ValueError("unsupported outcome requires an unsupported request")
        if self.outcome == "needs_clarification" and not (
            isinstance(self.request, ShuttleClarificationRequest) or self.candidates
        ):
            raise ValueError("clarification requires a clarification request or candidates")
        if isinstance(self.request, ShuttleComparisonRequest):
            if self.outcome == "success" and (
                len(self.query_results) != 2 or self.comparison is None
            ):
                raise ValueError("successful comparison requires two results and a comparison")
        elif self.comparison is not None:
            raise ValueError("comparison facts require a comparison request")
        return self
