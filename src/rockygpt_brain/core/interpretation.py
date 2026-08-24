"""The Listener's output.

Interpreted meaning only. Nothing here is executable: no resolved dates, no sort
fields, no limits, no endpoints, no query predicates. See `spec/brain-contract.md`
sections 1 and 3.

Two shapes appear repeatedly and mean different things:

* A **required nullable** field (`Reference | None` with no default) forces the
  Listener to state that the user referenced nothing. That is an interpretation.
* A field with a **default** would let the Listener stay silent and let the
  Worker guess. That is the widening defect, and it appears nowhere in this
  module.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """Structured model output with no undeclared escape hatches."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Scope(StrEnum):
    INSTITUTIONAL = "institutional"
    WORLD = "world"


class Operation(StrEnum):
    READ = "read"
    WRITE = "write"


class Access(StrEnum):
    PUBLIC = "public"
    INSTITUTIONAL = "institutional"
    PERSONAL = "personal"
    SECRET = "secret"  # noqa: S105 — an access class, not a credential


class DangerClass(StrEnum):
    NONE = "none"
    MEDICAL = "medical"
    FIRE = "fire"
    WEAPON = "weapon"
    VIOLENCE = "violence"
    SELF_HARM = "self_harm"
    OTHER = "other"


class Relation(StrEnum):
    """What the user asked of a set of records, independent of any field name."""

    EARLIEST = "earliest"
    LATEST = "latest"
    NEXT = "next"
    CURRENT = "current"
    ALL = "all"
    COUNT = "count"
    EXISTS = "exists"
    DESCRIBE = "describe"


class OrderSemantic(StrEnum):
    """Which order the user asked about, named by meaning.

    Distinct from the field that carries it: "the last one to leave" and "the
    last one to arrive" are different questions over the same records, and the
    capability owns which field answers each.
    """

    DEPARTURE_TIME = "departure_time"
    ARRIVAL_TIME = "arrival_time"
    START_TIME = "start_time"
    DATE = "date"
    NAME = "name"


class Cardinality(StrEnum):
    ONE = "one"
    MANY = "many"


#: Contract section 3.2. The Worker derives cardinality; the Listener's stated
#: value exists only so that an incoherent interpretation is visible.
CARDINALITY_OF_RELATION: dict[Relation, Cardinality] = {
    Relation.EARLIEST: Cardinality.ONE,
    Relation.LATEST: Cardinality.ONE,
    Relation.NEXT: Cardinality.ONE,
    Relation.CURRENT: Cardinality.ONE,
    Relation.COUNT: Cardinality.ONE,
    Relation.EXISTS: Cardinality.ONE,
    Relation.ALL: Cardinality.MANY,
    Relation.DESCRIBE: Cardinality.MANY,
}

#: Relations whose success may legitimately carry zero records. Contract 7.1.
MEASUREMENT_RELATIONS: frozenset[Relation] = frozenset({Relation.COUNT, Relation.EXISTS})


class Domain(StrEnum):
    CAMPUS_HOURS = "campus_hours"
    DINING_HOURS = "dining_hours"
    MENU = "menu"
    CONTACTS = "contacts"
    CLUBS = "clubs"
    EVENTS = "events"
    PROGRAMS = "programs"
    ACADEMIC_DATES = "academic_dates"
    MAP = "map"
    SHUTTLE = "shuttle"
    DOCUMENTS = "documents"
    CONVERSATION = "conversation"
    WORLD = "world"
    UNKNOWN = "unknown"


# --- references ---------------------------------------------------------------


class Mention(StrictModel):
    """Verbatim user text. Never a query predicate; the Worker resolves it."""

    kind: Literal["mention"]
    text: str = Field(min_length=1, max_length=200)


class Anaphor(StrictModel):
    """A pointer into turn state. The Worker substitutes the value."""

    kind: Literal["anaphor"]
    target: Literal["prior_subject", "prior_selection", "pending_slot"]
    slot: str | None = Field(default=None, max_length=64)


Reference: TypeAlias = Mention | Anaphor


# --- time ---------------------------------------------------------------------

NamedTime = Literal[
    "today",
    "tomorrow",
    "yesterday",
    "this_week",
    "next_week",
    "weekend",
]


class TimeNow(StrictModel):
    kind: Literal["now"]


class TimeNamed(StrictModel):
    """A name the user used. The Worker, not the Listener, knows what date it is."""

    kind: Literal["named"]
    name: NamedTime


class TimeOffset(StrictModel):
    """A quantity the user stated, in minutes. Signed; not derived from a clock."""

    kind: Literal["offset"]
    minutes: int = Field(ge=-20160, le=20160)


class TimeAbsolute(StrictModel):
    """A date the user stated literally."""

    kind: Literal["absolute"]
    date: date


TimeReference: TypeAlias = TimeNow | TimeNamed | TimeOffset | TimeAbsolute


# --- tasks --------------------------------------------------------------------


class TaskBase(StrictModel):
    operation: Operation
    access: Access
    relation: Relation
    cardinality: Cardinality
    #: Which declared order the relation runs against. Null takes the
    #: capability's default. A semantic the capability does not declare is
    #: `no_capability`, never silently the default.
    order_by: OrderSemantic | None = None


class ScheduleTask(TaskBase):
    """Domains whose records are time-bound and therefore require a time reference.

    `time` is required and non-nullable. A question about a time-bound domain
    always concerns some time, so the Listener must name which one; that is the
    field whose optionality previously let the Worker widen to the whole dataset.
    """

    domain: Literal[Domain.CAMPUS_HOURS, Domain.DINING_HOURS, Domain.EVENTS]
    subject: Reference | None
    time: TimeReference


class MenuTask(TaskBase):
    """Menus are time-bound and additionally divided by meal period.

    `meal` is what the user said, or null. It is never inferred from the clock:
    which meal is being served at a given instant is a fact about dining
    schedules that lives in DATA, not a guess the Listener may make.
    """

    domain: Literal[Domain.MENU]
    subject: Reference | None
    meal: str | None = Field(default=None, max_length=64)
    time: TimeReference


class ShuttleTask(TaskBase):
    domain: Literal[Domain.SHUTTLE]
    route: Reference | None
    origin: Reference | None
    destination: Reference | None
    time: TimeReference


class DirectoryTask(TaskBase):
    """Domains keyed by an entity rather than by time."""

    domain: Literal[
        Domain.CONTACTS,
        Domain.CLUBS,
        Domain.PROGRAMS,
        Domain.MAP,
    ]
    subject: Reference | None


class AcademicDatesTask(TaskBase):
    domain: Literal[Domain.ACADEMIC_DATES]
    subject: Reference | None
    time: TimeReference


class DocumentsTask(TaskBase):
    """Prose and policy. The Worker applies the relevance floor."""

    domain: Literal[Domain.DOCUMENTS]
    question: str = Field(min_length=1, max_length=500)


class ConversationTask(TaskBase):
    """Reads conversation truth. Never campus truth. Contract section 8."""

    domain: Literal[Domain.CONVERSATION]
    subject: Reference | None
    claim_relation: str | None = Field(default=None, max_length=64)


class WorldTask(TaskBase):
    """Non-institutional knowledge. The only task the Writer may answer from."""

    domain: Literal[Domain.WORLD]
    question: str = Field(min_length=1, max_length=500)


class UnknownTask(TaskBase):
    """The Listener understood a request it has no domain for."""

    domain: Literal[Domain.UNKNOWN]
    question: str = Field(min_length=1, max_length=500)


Task: TypeAlias = (
    ScheduleTask
    | MenuTask
    | ShuttleTask
    | DirectoryTask
    | AcademicDatesTask
    | DocumentsTask
    | ConversationTask
    | WorldTask
    | UnknownTask
)


class Interpretation(StrictModel):
    """Exactly one per turn. Contract section 3."""

    scope: Scope
    danger: DangerClass
    tasks: list[Task] = Field(min_length=1, max_length=6)
