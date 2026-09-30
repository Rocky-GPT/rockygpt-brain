"""The shuttle questions Jev is asked, and whether its answers add up to one code can answer.

Jev reads; it never writes a day, a stop or a plan. So each part of a shuttle question is a
fixed question in the student's own words, and the options that change per turn are written by
code: the days from the campus clock, and the stops from the timetable. Jev decides only what
code could get wrong without anyone noticing: what is being asked, whether a clock time is
part of it, which day and which stop. Code does the rest.

These questions go in the same one call as the nine of decisions.py, which stay as they are.
Code follows Jev's top pick on each one and lists the shaky picks (under 0.90) in the
diagnostics, except that an answer is never built on a near coin flip: if any pick that
shapes it is under FLOOR, the turn is "not ready" (Dan, 09-29, after a blind run answered on
a 0.50 pick). Everything code can't answer leaves the turn "not ready", never guessed at.
Once the questions are measured they are not reworded to fit the results.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from rockygpt_brain.campus import Timetable
from rockygpt_brain.context import Context
from rockygpt_brain.decisions import SURE, Decisions, Handler, said_yes, top
from rockygpt_brain.jev import Answer, Question, choice, noul
from rockygpt_brain.shuttle_answer import DAY_NAMES, Operation, ShuttlePlan

MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August",
               "September", "October", "November", "December")
# Today and the six days after it: each weekday name is in exactly one option.
DAYS_AHEAD = 7
# Jev's least sure pick may not be under this for code to answer. Fixed by Dan on 09-29
# before the fourth blind set was written, and not tuned to any run.
FLOOR = 0.6

TIMES = noul(
    "Does `latest_request` ask when a campus shuttle leaves, or where a shuttle trip stops? "
    "Prior messages may explain what it refers to.",
    "Asks about the times or stops of Ramapo's own shuttle (the Roadrunner Express, the train "
    "shuttle, 'the bus to the mall'), such as when the next, first or last one leaves or which "
    "stops a trip makes",
    "Asks about anything else, such as the shuttle's price, rules or riders, parking, other "
    "buses or trains, when a place is open, or mentions the shuttle only in passing",
)
TRIP = choice(
    "Which shuttle trip does `latest_request` ask about? Prior messages may explain what it "
    "refers to.",
    {
        "next": "The next trip to leave from now, as in 'the next shuttle' or 'when can I "
                "catch one'",
        "first": "The first trip of the day, the earliest, as in 'how early does it start'",
        "last": "The last trip of the day, the latest, as in 'the final one' or 'how late does "
                "it run'",
        "all": "Every trip of a day, as in 'the whole schedule' or 'what times does it run'",
    },
)
WANTS = choice(
    "What does `latest_request` want to know about the shuttle? Prior messages may explain "
    "what it refers to.",
    {
        "leaves": "When it leaves Ramapo, or when one can be caught. A place named as where "
                  "the trip goes or stops only narrows which trips count",
        "stops": "Where it stops, which stops it makes, when it reaches a stop, or whether it "
                 "goes to a place",
        "elsewhere": "A ride that starts at a stop such as the train station or the mall, or "
                     "the trip back to campus",
    },
)
CLOCK = noul(
    "Does `latest_request` tie the shuttle to a clock time or a part of the day?",
    "Names a time or part of the day the trip must fit, such as 'after 3', 'before noon', "
    "'around 5', 'this afternoon', 'by 9 pm' or 'to be there by 8'",
    "Names no time or part of the day. A day alone (today, tonight, tomorrow, Friday) is not "
    "one, and neither is asking for the next, first or last trip",
)
FIXED: dict[str, Question] = {
    "shuttle_times": TIMES, "shuttle_wants": WANTS, "shuttle_trip": TRIP, "shuttle_clock": CLOCK,
}
# The trips code can answer. "all" is a real question, so Jev has a place to put it and it
# is not mistaken for one of these three, but code doesn't answer it yet.
OPERATIONS: dict[str, Operation] = {"next": "next", "first": "first", "last": "last"}


@dataclass(frozen=True)
class Asking:
    """The questions for one turn, and what their per-turn option keys mean. When the campus
    data could not be read there is no stop question, and `stops` is empty."""

    questions: dict[str, Question]
    days: dict[str, date] = field(repr=False)  # "d0" is today, "d1" tomorrow
    stops: dict[str, str] = field(repr=False)  # "s1" is a key of Timetable.stop_menu


def day_options(now: datetime) -> tuple[dict[str, str], dict[str, date]]:
    options: dict[str, str] = {}
    days: dict[str, date] = {}
    for n in range(DAYS_AHEAD):
        day = now.date() + timedelta(days=n)
        name, month = DAY_NAMES[day.weekday()], MONTH_NAMES[day.month - 1]
        if n == 0:
            said = (f"Today, {name}, {month} {day.day}: says today, tonight, now, this "
                    f"morning, this afternoon or this evening, names {name}, or says no day "
                    "at all")
        elif n == 1:
            said = f"Tomorrow, {name}, {month} {day.day}"
        elif n == 2:
            said = f"{name}, {month} {day.day}, the day after tomorrow"
        else:
            said = f"{name}, {month} {day.day}"
        options[f"d{n}"] = said
        days[f"d{n}"] = day
    options["other"] = ("A day or date that is none of the days above, or two or more days, "
                        "such as 'weekdays', 'the weekend' or 'Saturday and Sunday'")
    return options, days


def stop_options(table: Timetable) -> tuple[dict[str, str], dict[str, str]]:
    options: dict[str, str] = {}
    stops: dict[str, str] = {}
    for number, place in enumerate(table.stop_menu, 1):
        options[f"s{number}"] = f"Going to or stopping at {place.place}"
        stops[f"s{number}"] = place.key
    options["none"] = "Names no place to go to"
    options["other"] = ("Names one place that is none of the places above, or two or more "
                        "places")
    return options, stops


def shuttle_questions(now: datetime, table: Timetable | None) -> Asking:
    """The three fixed questions plus the day and, if the timetable was read, the stop,
    written for this turn."""
    days, day_keys = day_options(now)
    questions = {
        **FIXED,
        "shuttle_day": choice(
            "Which day does `latest_request` ask about? Prior messages may explain what it "
            "refers to.", days),
    }
    stop_keys: dict[str, str] = {}
    if table is not None:
        stops, stop_keys = stop_options(table)
        questions["shuttle_stop"] = choice(
            "Which shuttle stop does `latest_request` ask about, counting a place from "
            "earlier messages it points back to with 'there' or 'that one'? The shuttle "
            "leaves Ramapo and stops only at the places listed.", stops)
    return Asking(questions, day_keys, stop_keys)


@dataclass(frozen=True)
class Dispatch:
    """Whether Jev's answers make a plan, and if not, the first gate that said no. `picks`
    is for the dev metrics: codes, dates and places from the data, never the student's words."""

    plan: ShuttlePlan | None
    refused: str | None
    picks: dict[str, Any]


def dispatch(answers: Mapping[str, Answer], asking: Asking, decisions: Decisions,
             chosen: Handler, context: Context) -> Dispatch:
    """The shuttle plan for this turn, or the reason there isn't one. Every gate that says
    no leaves the turn as it is today ("not ready"): a shuttle answer is never a fallback.
    The one exception is `data_unavailable`: Jev read a plain shuttle question, and code
    couldn't read the timetable to answer it."""
    if not answers.keys() >= asking.questions.keys():
        return Dispatch(None, "not_asked", {})
    times, times_sure = said_yes(answers["shuttle_times"])
    clock, clock_sure = said_yes(answers["shuttle_clock"])
    wants, wants_sure = top(answers["shuttle_wants"])
    trip, trip_sure = top(answers["shuttle_trip"])
    day, day_sure = top(answers["shuttle_day"])
    sureness = {"times": times_sure, "wants": wants_sure, "trip": trip_sure,
                "clock": clock_sure, "day": day_sure}
    stop = None
    if "shuttle_stop" in answers:
        stop, stop_sure = top(answers["shuttle_stop"])
        sureness["stop"] = stop_sure
    picked_day = asking.days.get(day)  # None for "other": no one day
    picks: dict[str, Any] = {
        "times": times, "wants": wants, "trip": trip, "clock": clock,
        "day": picked_day.isoformat() if picked_day is not None else day,
        "stop": None if stop is None else asking.stops.get(stop, stop),
        "lowConfidence": {name: round(value, 3) for name, value in sureness.items()
                          if value < SURE},
    }

    def refuse(gate: str) -> Dispatch:
        return Dispatch(None, gate, picks)

    if chosen.name not in {"exact", "campus_fact"}:
        return refuse("route")  # danger, account and multi-part are ahead of a lookup
    if decisions.needs_earlier and not context.first_question:
        return refuse("follow_up")
    if not times or wants != "leaves":
        return refuse("not_shuttle")  # not a shuttle question, or not about a departure
    if clock:
        return refuse("clock")
    operation = OPERATIONS.get(trip)
    if operation is None:
        return refuse("trip")
    if picked_day is None:
        return refuse("day")
    unsure = min(sureness.values()) < FLOOR  # every gate said yes, but on a coin flip
    if stop is None:
        # A plain shuttle question, and no timetable. Only a firm reading is an outage; a coin
        # flip is "not ready", as it is when the timetable can be read.
        return refuse("unsure" if unsure else "data_unavailable")
    if stop == "other":
        return refuse("stop")
    if unsure:
        return refuse("unsure")
    return Dispatch(ShuttlePlan(operation, picked_day, asking.stops.get(stop)), None, picks)
