"""What a writer receives: the Fact Packet cut down to what it needs to say, and nothing else.

The full Fact Packet is the Brain's record: it carries the ids, hashes and checks that let the
Brain prove every fact and let a developer replay a turn. A writer (a template, or a model with no
other context) needs none of that. It needs the values to say, when they were read, which are not
current or in conflict, what is confirmed not published, what is unknown, what to ask, the fixed
wording to repeat, and the sources to cite. `writer_view` derives exactly that from a validated
packet. It adds, computes and infers nothing, and it never drops a value a student could be told:

  dropped      ids (entities, facts, sources, candidates), the dataset and its hash, `kind`, `match`
               (how a choice was ranked), `collection`, a source's own `current` (each fact says),
               the section of a page that was read, a check time repeated per page, any source that
               no fact, derived fact or confirmed absence cites (it backs nothing a writer is told),
               and a full-week schedule when the Brain worked out the one day that was asked: the
               derived fact replaces it, so a writer is handed that day and picks nothing
  left out     for a day that was asked, a schedule whose own season label plainly is not the
               date's season (a Summer schedule beside an October date, `season.season_fit`), when
               another schedule for that office and day remains. It is the one case where a
               listed value is not handed over, because it cannot be the answer and a writer
               given it hedges the answer that can be. The packet keeps it. Nothing is ever left
               out when no other schedule remains
  omitted when they hold the usual value: `status` "known", `current` true, `freshness`
               "fresh", `validity` "unspecified" and its stock limitation, `truncated` false, a
               reason that is the only one there is, and a list of rows with no rows (`facts`,
               `missing`, `not_published`, `ambiguities`, `unresolved`, `notices`, `sources`)
  renamed      a subject is its name; a source is its number `n` (numbered once the uncited ones
               are left out; records read from one page on one date that say nothing else
               differently share one), and `sources` on a fact or an absence lists those numbers;
               `candidates` are `choices` (names only)
  proofs       inside a schedule, the proof that a weekday or a validity date is not published
               (`absence`, `validity_absence`) is cut to when it was checked and which pages were
               read; the scope, the reason sentence, the section and the page hash stay in the
               packet. `validity_absence` is said as `dates_not_published`. A source's own copy of
               it, and its `season` (each fact says its own), are dropped. An unverified seasonal
               date keeps `applicability` and a plain reason (the page does not say which dates
               it covers) but not `applies`, and an empty `window` is left out. A weekday the
               office's pages were checked for and publish nothing about, on a schedule with no
               dates, is said as not published alone: whether the schedule's dates are known
               changes nothing about a day it lists nothing for, so that day carries no
               `applicability`, `dates_not_published` or `current` flag
  first        when the packet holds an emergency, its fixed wording comes before everything else

A field a row in one of those lists gains later and this function does not know is carried through
unchanged, and never overwrites a key the view sets. A field added anywhere else (the packet's top
level, the request, a subject) is not carried; the test that compares every packet value with the
view fails until it is handled.
"""

import json
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from rockygpt_brain.season import season_fit

CAMPUS_TZ = ZoneInfo("America/New_York")


def _campus_instant(value: Any) -> Any:
    """A timestamp in campus time, so its date is the one a student on campus has (a check made at
    9 PM Tuesday is Tuesday, not the next UTC day). Anything that is not a timestamp is returned as
    it came."""
    if not isinstance(value, str):
        return value
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return value
    return moment.astimezone(CAMPUS_TZ).isoformat() if moment.tzinfo else value


# What a writer is told about a schedule that lists hours but publishes no dates: the fact, not
# the packet's technical reason (which says "applicability").
DATES_NOT_PUBLISHED_NOTE = "The page does not say which dates this schedule covers."
# When the page gives one end of its dates (a Fall term starting August 31), say which is missing.
_ONE_DATE_NOTE = {
    "end": "The page gives when this schedule starts but not when it ends.",
    "start": "The page gives when this schedule ends but not when it starts.",
}

# The limitation the reader adds to every source with no validity interval. It says nothing a writer
# can use once `validity` is "unspecified", which is what it comes from.
NO_VALIDITY_NOTE = "The source publishes no validity interval."

_FACT = frozenset({"id", "subject", "predicate", "value", "status", "current", "source_ids",
                   "purpose"})
_DERIVED = frozenset({"id", "subject", "predicate", "day", "date", "value", "applies", "current",
                      "from", "source_ids"})
_ABSENCE = frozenset({"subject", "predicate", "checked_at", "checks", "current", "source_ids",
                      "purpose"})
_MISSING = frozenset({"subject", "predicate", "reason", "source_ids"})
_AMBIGUITY = frozenset({"query", "candidates", "truncated"})
_UNRESOLVED = frozenset({"query", "reason"})
_NOTICE_DROPPED = frozenset({"type", "subject", "contacts"})
_SOURCE = frozenset({"id", "title", "collection", "urls", "captured_at", "freshness", "validity",
                     "valid_from", "valid_until", "current", "limitations", "original_record_id",
                     "season", "validity_absence"})
# Which end of a validity range a writer is told is not published.
_BOUND = {"valid_from": "start", "valid_until": "end"}


def _checked(proof: dict[str, Any]) -> dict[str, Any]:
    """What a writer needs of a proof that something is not published: when, and where it looked."""
    row: dict[str, Any] = {"checked_at": _campus_instant(proof["checked_at"]),
                           "pages": list(dict.fromkeys(check["url"] for check in proof["checks"]))}
    if proof.get("current") is False:
        row["current"] = False
    return row


def _said(node: Any) -> Any:
    """A fact value with its audit proofs cut down to `_checked` and empty windows left out."""
    if isinstance(node, list):
        return [_said(item) for item in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for key, item in node.items():
        if key == "validity_absence" and isinstance(item, dict):
            proofs = {bound: _checked(proof) for bound, proof in item.items()}
            checks = list(proofs.values())
            out["dates_not_published"] = {
                **checks[0], "which": [_BOUND.get(bound, bound) for bound in proofs],
                **({"current": False} if any(c.get("current") is False for c in checks) else {})}
        elif key == "absence" and isinstance(item, dict) and "checks" in item:
            out[key] = _checked(item)
        elif key == "window" and item == {}:
            continue
        else:
            out[key] = _said(item)
    return out


def _rest(item: dict[str, Any], handled: frozenset[str]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key not in handled}


def _another_season(entries: list[dict[str, Any]]) -> set[str]:
    """Ids of derived facts whose schedule's season label plainly is not the date's season, in a
    group (one office, one date) where another schedule remains to be said."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for entry in entries:
        groups.setdefault((entry["subject"]["id"], entry["date"]), []).append(entry)
    gone: set[str] = set()
    for group in groups.values():
        out = [e for e in group
               if isinstance(e["value"], dict)
               and season_fit(e["value"].get("season"), date.fromisoformat(e["date"])) == "out"]
        if out and len(out) < len(group):
            gone.update(e["id"] for e in out)
    return gone


def writer_view(packet: dict[str, Any]) -> dict[str, Any]:
    """The part of a validated packet a writer needs, as plain JSON."""
    cited = {sid for kind in ("facts", "not_published", "derived_facts", "missing")
             for entry in packet[kind]
             for sid in entry.get("source_ids", [])}
    kept = [source for source in packet["sources"] if source["id"] in cited]
    number, sources = _numbered(kept)
    request = packet["request"]
    view: dict[str, Any] = {
        "asked": {"intent": request["intent"],
                  "offices": [entity["name"] for entity in request["entities"]],
                  "fields": list(request["fields"]), "asOf": request["asOf"]},
        "status": packet["status"],
    }

    derived, replaced = [], set()
    another_season = _another_season(packet["derived_facts"])
    for entry in packet["derived_facts"]:
        replaced.update(entry["from"])  # Shown or left out, its whole week is not said again.
        if entry["id"] in another_season:
            continue
        value = _said(entry["value"])
        nothing_listed = (entry.get("applicability") == "unverified" and isinstance(value, dict)
                          and value.get("status") == "not_published"
                          and value.get("hours") is None and value.get("listed_hours") is None)
        if nothing_listed:
            # Its dates change nothing about a day it lists nothing for: say only that nothing is
            # published for that day.
            value = {k: v for k, v in value.items()
                     if k not in {"dates_not_published", "listed_hours"}}
        row: dict[str, Any] = {"subject": entry["subject"]["name"],
                               "predicate": entry["predicate"], "day": entry["day"],
                               "date": entry["date"], "value": value}
        if nothing_listed:
            pass
        elif not entry["applies"] and "applicability" not in entry:
            row["applies"] = False  # Then it is not current either: the date is outside it.
        elif not entry["current"]:
            row["current"] = False
        row["sources"] = _cited(number, entry["source_ids"])
        merged = {**_rest(entry, _DERIVED), **row}
        if nothing_listed:
            merged.pop("applicability", None)
            merged.pop("applicability_reason", None)
        elif "applicability_reason" in merged:
            missing = value.get("dates_not_published", {}).get("which") \
                if isinstance(value, dict) else None
            only = missing[0] if isinstance(missing, list) and len(missing) == 1 else ""
            merged["applicability_reason"] = _ONE_DATE_NOTE.get(only, DATES_NOT_PUBLISHED_NOTE)
        derived.append(merged)
    if derived:
        view["derived_facts"] = derived

    # "What's for lunch, and anything vegan": the whole menu and the vegan list of the same meal.
    # The writer gets the filtered list, told the whole menu was asked (its other items are in
    # rest_of_menu), instead of two overlapping lists of which it says one.
    def menu_key(fact: dict[str, Any]) -> tuple[Any, ...] | None:
        value = fact["value"]
        if fact["predicate"] != "menu" or not isinstance(value, dict) or not value.get("meals"):
            return None
        return (fact["subject"].get("id"), value.get("date"),
                tuple(m.get("meal") for m in value["meals"]))
    filtered = {key for fact in packet["facts"] if (key := menu_key(fact))
                and fact["value"].get("filter") and fact["value"].get("rest_of_menu")}
    whole = {fact["id"] for fact in packet["facts"] if (key := menu_key(fact)) in filtered
             and not fact["value"].get("filter")}
    facts = []
    for fact in packet["facts"]:
        if fact["id"] in replaced or fact["id"] in whole:
            continue
        row = {"subject": fact["subject"]["name"], "predicate": fact["predicate"],
               "value": _said(fact["value"])}
        if whole and menu_key(fact) in filtered and fact["value"].get("filter"):
            row["value"] = {**row["value"], "whole_menu_asked": True}
        if fact["status"] != "known":
            row["status"] = fact["status"]
        if not fact["current"]:
            row["current"] = False
        if "purpose" in fact:
            row["purpose"] = fact["purpose"]
        row["sources"] = _cited(number, fact["source_ids"])
        facts.append({**_rest(fact, _FACT), **row})
    if facts:
        view["facts"] = facts

    # Weekdays a schedule leaves unknown are the packet's to keep, and no answer to a question
    # about another day: the writer is shown them only when it has no hours for the day asked.
    answered_days: dict[str, set[str]] = {}
    for entry in packet["derived_facts"]:
        if entry["predicate"] == "hours_on" and entry.get("applies"):
            answered_days.setdefault(entry["subject"]["id"], set()).add(entry["day"])
    missing = []
    for entry in packet["missing"]:
        days = entry.get("days")
        if (entry["predicate"] == "hours" and days and answered_days.get(entry["subject"]["id"])
                and not answered_days[entry["subject"]["id"]] & set(days)):
            continue
        row = {"subject": entry["subject"]["name"], "predicate": entry["predicate"]}
        if entry["reason"] != "unknown":
            row["reason"] = entry["reason"]
        if entry.get("source_ids"):
            row["sources"] = _cited(number, entry["source_ids"])
        missing.append({**_rest(entry, _MISSING), **row})
    if missing:
        view["missing"] = missing

    absent = []
    for entry in packet["not_published"]:
        row = {"subject": entry["subject"]["name"], "predicate": entry["predicate"],
               "checked_at": _campus_instant(entry["checked_at"]),
               "pages": list(dict.fromkeys(check["url"] for check in entry["checks"]))}
        if not entry["current"]:
            row["current"] = False
        if "purpose" in entry:
            row["purpose"] = entry["purpose"]
        row["sources"] = _cited(number, entry["source_ids"])
        absent.append({**_rest(entry, _ABSENCE), **row})
    if absent:
        view["not_published"] = absent

    ambiguities = []
    for entry in packet["ambiguities"]:
        row = {"query": entry["query"],
               "choices": [candidate["name"] for candidate in entry["candidates"]]}
        if entry.get("truncated"):
            row["truncated"] = True
        ambiguities.append({**_rest(entry, _AMBIGUITY), **row})
    if ambiguities:
        view["ambiguities"] = ambiguities

    unresolved = []
    for entry in packet["unresolved"]:
        row = {"query": entry["query"]}
        if entry["reason"] != "no_matching_office":
            row["reason"] = entry["reason"]
        unresolved.append({**_rest(entry, _UNRESOLVED), **row})
    if unresolved:
        view["unresolved"] = unresolved

    notices = []
    for notice in packet["notices"]:
        row = {"type": notice["type"]}
        if isinstance(notice.get("subject"), dict):
            row["subject"] = notice["subject"]["name"]
        notices.append({**_rest(notice, _NOTICE_DROPPED), **row})
    if notices:
        view["notices"] = notices
        if any(notice["type"] == "safety" for notice in notices):
            # Dicts keep their order, so the emergency wording is the first thing a writer reads.
            view = {"asked": view["asked"], "status": view["status"], "notices": notices,
                    **{k: v for k, v in view.items() if k not in {"asked", "status", "notices"}}}

    if sources:
        view["sources"] = sources
    return view


def _source_row(source: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {"title": source["title"]}
    if source["urls"]:
        row["urls"] = list(source["urls"])
    if source["captured_at"]:
        row["captured_at"] = _campus_instant(source["captured_at"])
    if source["freshness"] != "fresh":
        row["freshness"] = source["freshness"]
    if source["validity"] != "unspecified":
        row["validity"] = source["validity"]
    for key in ("valid_from", "valid_until"):
        if source.get(key):
            row[key] = source[key]
    limits = [text for text in source["limitations"]
              if not (text == NO_VALIDITY_NOTE and source["validity"] == "unspecified")]
    if limits:
        row["limitations"] = limits
    return {**_rest(source, _SOURCE), **row}


def _numbered(
    kept: list[dict[str, Any]],
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    """Each cited source's number, and the sources a writer is shown under those numbers.

    Records read from one page on one campus date (a venue's hours and its menu, each shuttle
    route's timetable) are one source to a writer, so one link is not cited once per record. A
    record whose page says anything else differently (its freshness, dates or limitations) keeps
    a number of its own; the earliest capture time of a shared one is the one said."""
    number: dict[str, int] = {}
    rows: dict[str, dict[str, Any]] = {}
    for source in kept:
        row = _source_row(source)
        captured = row.get("captured_at")
        day = captured[:10] if isinstance(captured, str) else captured
        key = json.dumps({**{k: v for k, v in row.items() if k != "captured_at"}, "day": day},
                         sort_keys=True, default=str)
        if key not in rows:
            # The view's own number, never a field of that name the packet gained.
            rows[key] = {"n": len(rows) + 1, **{k: v for k, v in row.items() if k != "n"}}
        elif isinstance(captured, str) and captured < rows[key].get("captured_at", captured):
            rows[key]["captured_at"] = captured
        number[source["id"]] = rows[key]["n"]
    return number, list(rows.values())


def _cited(number: dict[str, int], source_ids: list[str]) -> list[int]:
    return list(dict.fromkeys(number[sid] for sid in source_ids))
