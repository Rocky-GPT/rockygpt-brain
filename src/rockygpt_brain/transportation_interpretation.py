"""Model interpretation for the bounded Step 5A shuttle contract."""

from collections.abc import Sequence
from datetime import date, datetime, time
from typing import Annotated, Literal, Self, TypedDict, cast

from openai import OpenAI, pydantic_function_tool
from openai.types.responses import FunctionToolParam, ResponseInputParam
from pydantic import Field, StringConstraints, ValidationError, model_validator
from word2number import w2n  # type: ignore[import-untyped]

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

NEXT_TRIP_TOOL_NAME = "shuttle_next_trip"
NEXT_TRIPS_TOOL_NAME = "shuttle_next_trips"
SCHEDULE_TOOL_NAME = "shuttle_schedule"
AVAILABILITY_TOOL_NAME = "shuttle_availability"
DATED_AVAILABILITY_TOOL_NAME = "shuttle_availability_on_day"
COMPARISON_TOOL_NAME = "shuttle_comparison"
CLARIFICATION_TOOL_NAME = "shuttle_clarification"
UNSUPPORTED_TOOL_NAME = "unsupported_shuttle_request"
INTERPRETATION_FAILURE_ANSWER = (
    "I couldn't reliably interpret that shuttle request. Please rephrase it."
)
INTERPRETATION_INSTRUCTIONS = """Interpret the complete ordered conversation. For every campus
shuttle request, call exactly one available shuttle tool; do not answer or ask a shuttle
clarifying question in text. For a non-shuttle request, call no tool and respond normally. Keep
arrival and departure intent exact. Classify each user-authored filter by its semantic role: a
route is the explicitly named or numbered service itself, an origin is where the rider leaves,
and a destination is where the rider wants to arrive. Do not treat a place or generic transport
category as a route identity. Supply only requested operation arguments, never shuttle facts."""
RETRY_INSTRUCTIONS = """
A previous structured call was rejected by deterministic validation. Retry exactly once.
Availability requires an explicit user-authored clock value and verbatim clock evidence. An open
request asking for a clock value is not itself a clock constraint; select the chronologically
earliest trip instead. Every day, count, clock, offset, and filter evidence value must be copied
verbatim from user-authored text. Do not invent a value to satisfy a tool shape."""
SCOPE_DESCRIPTION = """Use only for RockyGPT campus shuttle transportation, understood from the
latest request and ordered conversation. Do not call any shuttle tool for a non-shuttle request.
For every campus shuttle request, call exactly one shuttle tool; never answer or ask a shuttle
clarifying question in plain text. Route, origin, and destination are optional filters, so their
absence does not make an otherwise complete request ambiguous.
Arguments are interpretation only: never invent route IDs, canonical route or stop names, trip
records, schedule facts, sources, or calculated dates. Mentions must be copied verbatim from
user-authored text or be null. Classify a mention by its role in the rider's request, even when
the place is unfamiliar: a place the rider wants to reach is a destination, and a place they
want to leave is an origin. A route mention must be a proper or numbered identity that
distinguishes one shuttle service from another; a generic transportation category is not a
route mention."""
ClockText = Annotated[
    str,
    StringConstraints(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$"),
]
EvidenceText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=120),
]


class ConversationMessage(TypedDict):
    """One original ordered chat message."""

    role: Literal["user", "assistant"]
    content: str


class ShuttleNextDayToolArguments(ContractModel):
    """A day shape that permits an unbounded upcoming lookup."""

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
    day_mention: EvidenceText | None = Field(
        description=(
            "Exact user-authored words that identify the requested day; null only for an "
            "unbounded upcoming request"
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
        if (self.day_kind == "upcoming") != (self.day_mention is None):
            raise ValueError("day_mention must be null only for an upcoming day")
        return self


class ShuttleBoundedDayToolArguments(ContractModel):
    """A day shape that always resolves to one bounded schedule."""

    day_kind: Literal[
        "relative", "named_weekday", "service_day", "calendar_date"
    ] = Field(
        description=(
            "relative for today/tomorrow; named_weekday for a named day; "
            "service_day for a recurring template; calendar_date only when the user "
            "supplies a date"
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
    day_mention: EvidenceText = Field(
        description="Exact user-authored words that identify this requested day"
    )

    def to_contract(self) -> ShuttleDay:
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


class ShuttleMentionToolArguments(ContractModel):
    """One user-authored filter with its requested travel role."""

    text: str = Field(description="Exact mention copied from user-authored text")
    role: Literal["route", "origin", "destination"] = Field(
        description=(
            "route only for a proper or numbered service identity; origin for the place the "
            "rider leaves; destination for the place the rider wants to reach, even if unfamiliar"
        )
    )


class ShuttleFilters(ContractModel):
    """Optional user-authored shuttle filters classified by semantic role."""

    mentions: list[ShuttleMentionToolArguments] = Field(
        max_length=3,
        description=(
            "User-authored route, origin, or destination filters; empty when none are requested"
        ),
    )

    def mention(self, role: Literal["route", "origin", "destination"]) -> str | None:
        return next((mention.text for mention in self.mentions if mention.role == role), None)

    @model_validator(mode="after")
    def require_unique_roles(self) -> Self:
        roles = [mention.role for mention in self.mentions]
        if len(roles) != len(set(roles)):
            raise ValueError("each filter role may appear at most once")
        return self


class ShuttleNextCallBase(ShuttleFilters):
    """Shared arguments for ordered trips starting with the immediate next match."""

    day: ShuttleNextDayToolArguments
    offset: int | None = Field(
        ge=1,
        le=10,
        description=(
            "Trips to skip for an explicit contextual follow-up, or null when none are skipped"
        ),
    )
    offset_mention: EvidenceText | None = Field(
        description=(
            "Exact user-authored follow-up words that require skipping trips, or null with offset"
        )
    )
    show: Literal["departure", "arrival", "both", "relative"] = Field(
        description=(
            "departure or arrival for that requested clock, both only when both are requested, "
            "or relative when the user asks how long until the trip"
        )
    )

    def to_contract_with_count(self, count: int) -> ShuttleQueryRequest:
        return ShuttleQueryRequest(
            kind="query",
            answer_kind="trips",
            query=ShuttleQuery(
                day=self.day.to_contract(),
                selection="next",
                count=count,
                offset=self.offset or 0,
                route_mention=self.mention("route"),
                origin_mention=self.mention("origin"),
                destination_mention=self.mention("destination"),
            ),
            show=self.show,
        )

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        if (self.offset is None) != (self.offset_mention is None):
            raise ValueError("offset and offset_mention must either both be set or both be null")
        return self


class ShuttleNextTripCall(ShuttleNextCallBase):
    """The immediate next trip; quantity is deterministic and not model-supplied."""

    def to_contract(self) -> ShuttleQueryRequest:
        return self.to_contract_with_count(1)


class ShuttleNextTripsCall(ShuttleNextCallBase):
    """An explicitly requested quantity of upcoming trips."""

    count: int = Field(ge=2, le=10, description="Exact explicitly requested quantity")
    count_mention: EvidenceText = Field(
        description="Exact user-authored quantity words corresponding to count"
    )

    def to_contract(self) -> ShuttleQueryRequest:
        return self.to_contract_with_count(self.count)


class ShuttleScheduleLookup(ShuttleFilters):
    """One bounded full-schedule lookup."""

    day: ShuttleBoundedDayToolArguments

    def to_query(self) -> ShuttleQuery:
        return ShuttleQuery(
            day=self.day.to_contract(),
            selection="all",
            route_mention=self.mention("route"),
            origin_mention=self.mention("origin"),
            destination_mention=self.mention("destination"),
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


class ShuttleAvailabilityBase(ShuttleFilters):
    """Shared arguments for one bounded clock-time availability check."""

    relation: Literal["at", "around"]
    clock: ClockText = Field(description="Campus clock time as 24-hour HH:MM with no timezone")
    clock_mention: EvidenceText = Field(
        description="Exact user-authored clock-time words normalized into clock"
    )
    basis: Literal["departure", "arrival"] = Field(
        description="Preserve whether the user asked about departure or arrival"
    )

    def to_contract_with_day(self, day: ShuttleDay) -> ShuttleQueryRequest:
        return ShuttleQueryRequest(
            kind="query",
            answer_kind="availability",
            query=ShuttleQuery(
                day=day,
                selection="all",
                route_mention=self.mention("route"),
                origin_mention=self.mention("origin"),
                destination_mention=self.mention("destination"),
                time=ShuttleTimeConstraint(
                    relation=self.relation,
                    clock=time.fromisoformat(self.clock),
                    basis=self.basis,
                ),
            ),
            show=self.basis,
        )



class ShuttleAvailabilityCall(ShuttleAvailabilityBase):
    """A clock-time availability check defaulted deterministically to today."""

    def to_contract(self) -> ShuttleQueryRequest:
        return self.to_contract_with_day(RelativeDay(kind="relative", days_from_today=0))


class ShuttleDatedAvailabilityCall(ShuttleAvailabilityBase):
    """A clock-time availability check for an explicitly requested bounded day."""

    day: ShuttleBoundedDayToolArguments

    def to_contract(self) -> ShuttleQueryRequest:
        return self.to_contract_with_day(self.day.to_contract())


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
        ShuttleNextTripCall,
        NEXT_TRIP_TOOL_NAME,
        (
            f"{SCOPE_DESCRIPTION}\n"
            "Select whenever the requested result is exactly one chronologically earliest future "
            "trip, including when no quantity is stated and when the user asks when a shuttle "
            "departs or arrives without supplying an explicit clock time."
        ),
    ),
    _tool(
        ShuttleNextTripsCall,
        NEXT_TRIPS_TOOL_NAME,
        (
            f"{SCOPE_DESCRIPTION}\n"
            "Select only when the user explicitly asks for a quantity greater than one of the "
            "chronologically earliest future trips. The exact user-authored quantity words are "
            "required as count_mention."
        ),
    ),
    _tool(
        ShuttleScheduleCall,
        SCHEDULE_TOOL_NAME,
        (
            f"{SCOPE_DESCRIPTION}\n"
            "Select only for a complete bounded schedule or an explicitly requested full list. "
            "A singular request asking when a shuttle runs selects the next-trip operation."
        ),
    ),
    _tool(
        ShuttleAvailabilityCall,
        AVAILABILITY_TOOL_NAME,
        (
            f"{SCOPE_DESCRIPTION}\n"
            "Select only to check whether a shuttle exists at or around an explicit clock time "
            "when the user does not state a day. Deterministic code defaults this operation to "
            "today. Preserve whether that clock describes arrival or departure."
        ),
    ),
    _tool(
        ShuttleDatedAvailabilityCall,
        DATED_AVAILABILITY_TOOL_NAME,
        (
            f"{SCOPE_DESCRIPTION}\n"
            "Select only to check whether a shuttle exists at or around an explicit clock time "
            "on an explicitly stated bounded day. Preserve the verbatim day evidence and whether "
            "the clock describes arrival or departure."
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
    """Internal validation failure for one model-produced interpretation call."""


def _interpretation_failure(model: str) -> tuple[str, TransportationInterpretation]:
    return INTERPRETATION_FAILURE_ANSWER, TransportationInterpretation(
        selected=True,
        request=ShuttleClarificationRequest(
            kind="clarification",
            reason="interpretation_failure",
        ),
        model=model,
    )


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


def _require_user_evidence(
    label: str,
    value: str | None,
    messages: Sequence[ConversationMessage],
) -> None:
    if value is None:
        return
    user_text = "\n".join(
        message["content"] for message in messages if message["role"] == "user"
    ).casefold()
    if value.casefold() not in user_text:
        raise ValueError(f"model-produced {label} evidence was not present in user text: {value}")


def _clock_from_evidence(value: str) -> time:
    normalized = " ".join(value.upper().replace(".", "").split())
    for clock_format in ("%I %p", "%I:%M %p", "%H:%M"):
        try:
            return datetime.strptime(normalized, clock_format).time()
        except ValueError:
            continue
    raise ValueError(f"clock evidence could not be normalized: {value}")


def _count_from_evidence(value: str) -> int:
    try:
        parsed = w2n.word_to_num(value)
    except ValueError as error:
        raise ValueError(f"count evidence could not be normalized: {value}") from error
    if not isinstance(parsed, int):
        raise ValueError(f"count evidence did not resolve to a whole number: {value}")
    return parsed


def _validate_model_evidence(
    call: ContractModel, messages: Sequence[ConversationMessage]
) -> None:
    days: list[ShuttleNextDayToolArguments | ShuttleBoundedDayToolArguments] = []
    if isinstance(call, (ShuttleNextCallBase, ShuttleScheduleLookup)):
        days.append(call.day)
    elif isinstance(call, ShuttleDatedAvailabilityCall):
        days.append(call.day)
    elif isinstance(call, ShuttleComparisonCall):
        days.extend(query.day for query in call.queries)
    for day in days:
        _require_user_evidence("day", day.day_mention, messages)

    if isinstance(call, ShuttleNextCallBase) and call.offset is not None:
        assert call.offset_mention is not None
        _require_user_evidence("offset", call.offset_mention, messages)
    if isinstance(call, ShuttleNextTripsCall):
        _require_user_evidence("count", call.count_mention, messages)
        if _count_from_evidence(call.count_mention) != call.count:
            raise ValueError("normalized count contradicts the user-authored count evidence")
    if isinstance(call, ShuttleAvailabilityBase):
        _require_user_evidence("clock", call.clock_mention, messages)
        if _clock_from_evidence(call.clock_mention) != time.fromisoformat(call.clock):
            raise ValueError("normalized clock contradicts the user-authored clock evidence")


def validate_tool_arguments(
    name: str, arguments: str, messages: Sequence[ConversationMessage]
) -> ShuttleRequestValue:
    """Validate exact structured output and user-text provenance."""
    try:
        request: ShuttleRequestValue
        call: ContractModel
        if name == NEXT_TRIP_TOOL_NAME:
            call = ShuttleNextTripCall.model_validate_json(arguments)
            request = call.to_contract()
        elif name == NEXT_TRIPS_TOOL_NAME:
            call = ShuttleNextTripsCall.model_validate_json(arguments)
            request = call.to_contract()
        elif name == SCHEDULE_TOOL_NAME:
            call = ShuttleScheduleCall.model_validate_json(arguments)
            request = call.to_contract()
        elif name == AVAILABILITY_TOOL_NAME:
            call = ShuttleAvailabilityCall.model_validate_json(arguments)
            request = call.to_contract()
        elif name == DATED_AVAILABILITY_TOOL_NAME:
            call = ShuttleDatedAvailabilityCall.model_validate_json(arguments)
            request = call.to_contract()
        elif name == COMPARISON_TOOL_NAME:
            call = ShuttleComparisonCall.model_validate_json(arguments)
            request = call.to_contract()
        elif name == CLARIFICATION_TOOL_NAME:
            call = ShuttleClarificationCall.model_validate_json(arguments)
            request = call.to_contract()
        elif name == UNSUPPORTED_TOOL_NAME:
            call = UnsupportedShuttleCall.model_validate_json(arguments)
            request = call.to_contract()
        else:
            raise ValueError(f"unexpected transportation tool: {name}")
        _validate_model_evidence(call, messages)
        _validate_mentions(request, messages)
    except (ValidationError, ValueError) as error:
        raise InvalidTransportationInterpretation(str(error)) from error
    return request


def interpret_transportation(
    messages: Sequence[ConversationMessage], model: str
) -> tuple[str, TransportationInterpretation]:
    """Interpret one conversation as normal chat or a typed shuttle request."""
    client = OpenAI()
    retry_tools = SHUTTLE_TOOLS
    for attempt in range(2):
        response = client.responses.create(
            model=model,
            input=cast(ResponseInputParam, list(messages)),
            instructions=(
                INTERPRETATION_INSTRUCTIONS
                if attempt == 0
                else INTERPRETATION_INSTRUCTIONS + RETRY_INSTRUCTIONS
            ),
            tools=SHUTTLE_TOOLS if attempt == 0 else retry_tools,
            tool_choice="auto",
            parallel_tool_calls=False,
            store=False,
            temperature=0,
        )
        calls = [item for item in response.output if item.type == "function_call"]
        if not calls:
            if attempt == 1:
                return _interpretation_failure(response.model)
            return response.output_text, TransportationInterpretation(
                selected=False,
                request=None,
                model=response.model,
            )
        if len(calls) == 1:
            try:
                request = validate_tool_arguments(calls[0].name, calls[0].arguments, messages)
            except InvalidTransportationInterpretation:
                if calls[0].name in {
                    AVAILABILITY_TOOL_NAME,
                    DATED_AVAILABILITY_TOOL_NAME,
                }:
                    retry_tools = [
                        tool
                        for tool in SHUTTLE_TOOLS
                        if tool["name"]
                        not in {AVAILABILITY_TOOL_NAME, DATED_AVAILABILITY_TOOL_NAME}
                    ]
            else:
                return "", TransportationInterpretation(
                    selected=True,
                    request=request,
                    model=response.model,
                )
        if attempt == 1:
            return _interpretation_failure(response.model)
    raise AssertionError("transportation interpretation retry loop did not return")
