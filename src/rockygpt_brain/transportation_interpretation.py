"""Model interpretation for the bounded Step 5A shuttle contract."""

from collections.abc import Sequence
from datetime import date, time
from typing import Annotated, Literal, Self, TypedDict, cast

from openai import OpenAI, pydantic_function_tool
from openai.types.responses import FunctionToolParam, ResponseInputParam
from pydantic import Field, StringConstraints, ValidationError, model_validator

from rockygpt_brain.transportation import (
    CalendarDay,
    ContractModel,
    NamedWeekday,
    RelativeDay,
    ServiceDayTemplate,
    ShuttleClarificationRequest,
    ShuttleComparisonRequest,
    ShuttleDay,
    ShuttleQuery,
    ShuttleQueryRequest,
    ShuttleRequestValue,
    ShuttleTimeConstraint,
    UnsupportedShuttleRequest,
    UpcomingDay,
)

NEXT_TRIPS_TOOL_NAME = "shuttle_next_trips"
SCHEDULE_TOOL_NAME = "shuttle_schedule"
AVAILABILITY_TOOL_NAME = "shuttle_availability"
COMPARISON_TOOL_NAME = "shuttle_comparison"
CLARIFICATION_TOOL_NAME = "shuttle_clarification"
UNSUPPORTED_TOOL_NAME = "unsupported_shuttle_request"
INTERPRETATION_ONLY_ANSWER = (
    "Shuttle request interpreted. Trusted schedule execution is not implemented in Step 5B."
)
INTERPRETATION_INSTRUCTIONS = """Interpret the complete ordered conversation. For every campus
shuttle request, call exactly one available shuttle tool; do not answer or ask a shuttle
clarifying question in text. For a non-shuttle request, call no tool and respond normally. Keep
arrival and departure intent exact. Supply only requested operation arguments, never shuttle
facts."""
SCOPE_DESCRIPTION = """Use only for RockyGPT campus shuttle transportation, understood from the
latest request and ordered conversation. Do not call any shuttle tool for a non-shuttle request.
For every campus shuttle request, call exactly one shuttle tool; never answer or ask a shuttle
clarifying question in plain text. Route, origin, and destination are optional filters, so their
absence does not make an otherwise complete request ambiguous.
Arguments are interpretation only: never invent route IDs, canonical route or stop names, trip
records, schedule facts, sources, or calculated dates. Mentions must be copied verbatim from
user-authored text or be null."""
ClockText = Annotated[
    str,
    StringConstraints(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$"),
]


class ConversationMessage(TypedDict):
    """One original ordered chat message."""

    role: Literal["user", "assistant"]
    content: str


class ShuttleDayToolArguments(ContractModel):
    """Strict-schema-compatible model wire shape for a Step 5A day."""

    day_kind: Literal[
        "upcoming", "relative", "named_weekday", "service_day", "calendar_date"
    ] = Field(
        description=(
            "upcoming for an unbounded next request; relative for today/tomorrow; "
            "named_weekday for a named day; service_day for a recurring template; "
            "calendar_date only when the user supplies a date"
        )
    )
    days_from_today: Literal[0, 1] | None = Field(
        description="0 for today or 1 for tomorrow when day_kind is relative; otherwise null"
    )
    weekday: Literal[
        "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"
    ] | None = Field(
        description="Named weekday only when day_kind is named_weekday; otherwise null"
    )
    service_day: Literal["weekday", "saturday", "sunday"] | None = Field(
        description="Recurring template only when day_kind is service_day; otherwise null"
    )
    calendar_date: date | None = Field(
        description=(
            "User-supplied calendar date only when day_kind is calendar_date; otherwise null"
        )
    )

    def to_contract(self) -> ShuttleDay:
        if self.day_kind == "upcoming":
            return UpcomingDay(kind="upcoming")
        if self.day_kind == "relative":
            if self.days_from_today is None:
                raise ValueError("relative day requires days_from_today")
            return RelativeDay(kind="relative", days_from_today=self.days_from_today)
        if self.day_kind == "named_weekday":
            if self.weekday is None:
                raise ValueError("named weekday requires weekday")
            return NamedWeekday(kind="named_weekday", weekday=self.weekday)
        if self.day_kind == "service_day":
            if self.service_day is None:
                raise ValueError("service day requires service_day")
            return ServiceDayTemplate(kind="service_day", service_day=self.service_day)
        if self.calendar_date is None:
            raise ValueError("calendar date requires calendar_date")
        return CalendarDay(kind="calendar_date", date=self.calendar_date)

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        self.to_contract()
        return self


class ShuttleFilters(ContractModel):
    """Optional user-authored shuttle filters."""

    route_mention: str | None = Field(
        description=(
            "Explicit identifying route label copied from user text; never the generic "
            "transportation mode; otherwise null"
        )
    )
    origin_mention: str | None = Field(
        description="Exact origin wording copied from user text, or null"
    )
    destination_mention: str | None = Field(
        description="Exact destination wording copied from user text, or null"
    )


class ShuttleNextTripsCall(ShuttleFilters):
    """One or more ordered trips starting with the immediate next match."""

    day: ShuttleDayToolArguments
    count: int = Field(
        ge=1,
        le=10,
        description="Exact requested quantity; use 1 when no quantity is specified",
    )
    offset: int = Field(
        ge=0,
        le=10,
        description="Trips to skip for a contextual follow-up; zero unless explicitly implied",
    )
    show: Literal["departure", "arrival", "both"] = Field(
        description="The requested clock values: departure, arrival, or both"
    )

    def to_contract(self) -> ShuttleQueryRequest:
        return ShuttleQueryRequest(
            kind="query",
            answer_kind="trips",
            query=ShuttleQuery(
                day=self.day.to_contract(),
                selection="next",
                count=self.count,
                offset=self.offset,
                route_mention=self.route_mention,
                origin_mention=self.origin_mention,
                destination_mention=self.destination_mention,
            ),
            show=self.show,
        )

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        self.to_contract()
        return self


class ShuttleScheduleLookup(ShuttleFilters):
    """One bounded full-schedule lookup."""

    day: ShuttleDayToolArguments

    def to_query(self) -> ShuttleQuery:
        return ShuttleQuery(
            day=self.day.to_contract(),
            selection="all",
            route_mention=self.route_mention,
            origin_mention=self.origin_mention,
            destination_mention=self.destination_mention,
        )

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        self.to_query()
        return self


class ShuttleScheduleCall(ShuttleScheduleLookup):
    """One bounded full shuttle schedule."""

    show: Literal["departure", "arrival", "both"]

    def to_contract(self) -> ShuttleQueryRequest:
        return ShuttleQueryRequest(
            kind="query",
            answer_kind="trips",
            query=self.to_query(),
            show=self.show,
        )

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        self.to_contract()
        return self


class ShuttleAvailabilityCall(ShuttleFilters):
    """One bounded clock-time shuttle availability check."""

    day: ShuttleDayToolArguments
    relation: Literal["at", "around"]
    clock: ClockText = Field(description="Campus clock time as 24-hour HH:MM with no timezone")
    basis: Literal["departure", "arrival"] = Field(
        description="Preserve whether the user asked about departure or arrival"
    )

    def to_contract(self) -> ShuttleQueryRequest:
        return ShuttleQueryRequest(
            kind="query",
            answer_kind="availability",
            query=ShuttleQuery(
                day=self.day.to_contract(),
                selection="all",
                route_mention=self.route_mention,
                origin_mention=self.origin_mention,
                destination_mention=self.destination_mention,
                time=ShuttleTimeConstraint(
                    relation=self.relation,
                    clock=time.fromisoformat(self.clock),
                    basis=self.basis,
                ),
            ),
            show=self.basis,
        )

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        self.to_contract()
        return self


class ShuttleComparisonCall(ContractModel):
    """Exactly two bounded full shuttle schedules to compare."""

    queries: list[ShuttleScheduleLookup] = Field(min_length=2, max_length=2)
    show: Literal["departure", "arrival", "both"]

    def to_contract(self) -> ShuttleRequestValue:
        left, right = self.queries
        return ShuttleComparisonRequest(
            kind="comparison",
            queries=(left.to_query(), right.to_query()),
            show=self.show,
        )

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        self.to_contract()
        return self


class ShuttleClarificationCall(ContractModel):
    """An ambiguous shuttle request or unresolved conversational reference."""

    reason: Literal["ambiguous_request", "ambiguous_reference"]

    def to_contract(self) -> ShuttleRequestValue:
        return ShuttleClarificationRequest(kind="clarification", reason=self.reason)


class UnsupportedShuttleCall(ContractModel):
    """A shuttle request requiring facts absent from the trusted schedule."""

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

    def to_contract(self) -> ShuttleRequestValue:
        return UnsupportedShuttleRequest(kind="unsupported", reason=self.reason)


class TransportationInterpretation(ContractModel):
    """Inspectable selection result returned without executing transportation."""

    selected: bool
    request: ShuttleRequestValue | None
    model: str

    @model_validator(mode="after")
    def selection_matches_request(self) -> Self:
        if self.selected != (self.request is not None):
            raise ValueError("selected must match request presence")
        return self


def _tool(model: type[ContractModel], name: str, description: str) -> FunctionToolParam:
    pydantic_tool = pydantic_function_tool(model, name=name, description=description)
    function = pydantic_tool["function"]
    return cast(
        FunctionToolParam,
        {
            "type": "function",
            "name": function["name"],
            "description": function.get("description"),
            "parameters": function["parameters"],
            "strict": True,
        },
    )


SHUTTLE_TOOLS = [
    _tool(
        ShuttleNextTripsCall,
        NEXT_TRIPS_TOOL_NAME,
        (
            f"{SCOPE_DESCRIPTION}\n"
            "Select whenever the requested result is the chronologically earliest future trip "
            "or trips, including a request asking whether a future trip is coming. This operation "
            "is complete without route or stop filters. Use offset only for a resolved contextual "
            "request to skip forward through upcoming trips."
        ),
    ),
    _tool(
        ShuttleScheduleCall,
        SCHEDULE_TOOL_NAME,
        f"{SCOPE_DESCRIPTION}\nSelect for one bounded full schedule or list of trips.",
    ),
    _tool(
        ShuttleAvailabilityCall,
        AVAILABILITY_TOOL_NAME,
        (
            f"{SCOPE_DESCRIPTION}\n"
            "Select only to check whether a shuttle exists at or around an explicit clock time. "
            "Preserve whether that clock describes arrival or departure."
        ),
    ),
    _tool(
        ShuttleComparisonCall,
        COMPARISON_TOOL_NAME,
        f"{SCOPE_DESCRIPTION}\nSelect only to compare exactly two bounded full schedules.",
    ),
    _tool(
        ShuttleClarificationCall,
        CLARIFICATION_TOOL_NAME,
        (
            f"{SCOPE_DESCRIPTION}\n"
            "Select only when no supported operation can be determined or a conversational "
            "reference cannot be resolved. Never select merely because optional filters are absent."
        ),
    ),
    _tool(
        UnsupportedShuttleCall,
        UNSUPPORTED_TOOL_NAME,
        (
            f"{SCOPE_DESCRIPTION}\n"
            "Select when trusted schedule data cannot support the shuttle request."
        ),
    ),
]


class InvalidTransportationInterpretation(RuntimeError):
    """The model selected transportation but did not satisfy its exact contract."""


def _queries(request: ShuttleRequestValue) -> tuple[ShuttleQuery, ...]:
    if isinstance(request, ShuttleQueryRequest):
        return (request.query,)
    if isinstance(request, ShuttleComparisonRequest):
        return request.queries
    return ()


def _validate_mentions(
    request: ShuttleRequestValue, messages: Sequence[ConversationMessage]
) -> None:
    user_text = "\n".join(
        message["content"] for message in messages if message["role"] == "user"
    ).casefold()
    for query in _queries(request):
        for mention in (
            query.route_mention,
            query.origin_mention,
            query.destination_mention,
        ):
            if mention is not None and mention.casefold() not in user_text:
                raise ValueError(f"model-produced mention was not present in user text: {mention}")


def validate_tool_arguments(
    name: str, arguments: str, messages: Sequence[ConversationMessage]
) -> ShuttleRequestValue:
    """Validate exact structured output and user-text provenance."""
    try:
        request: ShuttleRequestValue
        if name == NEXT_TRIPS_TOOL_NAME:
            request = ShuttleNextTripsCall.model_validate_json(arguments).to_contract()
        elif name == SCHEDULE_TOOL_NAME:
            request = ShuttleScheduleCall.model_validate_json(arguments).to_contract()
        elif name == AVAILABILITY_TOOL_NAME:
            request = ShuttleAvailabilityCall.model_validate_json(arguments).to_contract()
        elif name == COMPARISON_TOOL_NAME:
            request = ShuttleComparisonCall.model_validate_json(arguments).to_contract()
        elif name == CLARIFICATION_TOOL_NAME:
            request = ShuttleClarificationCall.model_validate_json(arguments).to_contract()
        elif name == UNSUPPORTED_TOOL_NAME:
            request = UnsupportedShuttleCall.model_validate_json(arguments).to_contract()
        else:
            raise ValueError(f"unexpected transportation tool: {name}")
        _validate_mentions(request, messages)
    except (ValidationError, ValueError) as error:
        raise InvalidTransportationInterpretation(str(error)) from error
    return request


def interpret_transportation(
    messages: Sequence[ConversationMessage], model: str
) -> tuple[str, TransportationInterpretation]:
    """Run one transportation-specific interpretation without executing it."""
    response = OpenAI().responses.create(
        model=model,
        input=cast(ResponseInputParam, list(messages)),
        instructions=INTERPRETATION_INSTRUCTIONS,
        tools=SHUTTLE_TOOLS,
        tool_choice="auto",
        parallel_tool_calls=False,
        store=False,
        temperature=0,
    )
    calls = [item for item in response.output if item.type == "function_call"]
    if not calls:
        return response.output_text, TransportationInterpretation(
            selected=False,
            request=None,
            model=response.model,
        )
    if len(calls) != 1:
        raise InvalidTransportationInterpretation(
            "the model must return exactly one transportation interpretation call"
        )

    request = validate_tool_arguments(calls[0].name, calls[0].arguments, messages)
    return INTERPRETATION_ONLY_ANSWER, TransportationInterpretation(
        selected=True,
        request=request,
        model=response.model,
    )
