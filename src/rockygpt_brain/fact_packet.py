"""The Fact Packet: the one JSON contract the Brain's findings leave in.

The Brain understands the question, resolves entities, walks the graph, applies dates and rules,
and validates evidence. It ends there. What it hands on is this packet, and nothing that writes
words (a template or a model) may discover, infer, calculate or retrieve a fact: it only expresses
what the packet holds.

Version 1.0:

    version        "1.0"
    request        intent, entities [{id, name, kind, query}], fields, asOf, dataset
    status         complete | partial | insufficient | ambiguous | not_found | emergency
                   | no_facts_needed
    facts          [{id, subject {id, name, kind}, predicate, value, status, current, source_ids}]
    derived_facts  [{id, subject, predicate "hours_on", day, date, value, applies, current, from,
                     source_ids}]   what the Brain worked out so a writer never has to: the hours
                   of the one weekday the student asked about (see hours_on.py). `from` names the
                     full-week fact it was read from; `applies` is false outside its published
                     window, or with `applicability: unverified` when a seasonal schedule has no
                     complete interval. Both have `hours: null`; the unverified one also carries
                     `listed_hours`, what the schedule lists for that weekday, to be said as
                     listed and unconfirmed for the date. Optional `status` preserves conflicting
                     or multiple schedule readings. For today's hours that apply, are not
                     unverified and are a plain time range (or 24 hours, or closed), `open_now`
                     {state "open" or "closed", now} says which it is at the turn's campus time,
                     so a writer never compares hours with a clock itself.
    missing        [{subject, predicate, reason}]   a requested fact we cannot state: reason
                   "unknown" (nobody checked, or the page could not be read), "no_citable_source"
                   (a value exists but nothing it comes from has a usable secure link: withheld)
                   or "no_fax_line" (a fax was asked for; the office's phone lines, cited by
                   source_ids, include none published as a fax).
                   Schedule issues also preserve schedule, status, details, days, source statements,
                   and source_ids, with unverified_schedule or partially_unknown_schedule reason.
    not_published  [{subject, predicate, checked_at, current, checks, source_ids}]
                   a requested fact the office's own pages were read for and do not publish: an
                   answer, not a gap (the pages and date are recorded)
    ambiguities    [{query, candidates [{id, name, match}], truncated}]
    unresolved     [{query, reason, approved_text?}] a name that matched no office
    notices        [{type, ...}]                    replies with no facts (greeting, safety...)
    sources        [{id, title, collection, urls, captured_at, freshness, validity, valid_from,
                     valid_until, current, limitations}]

A fact's `value` is exactly what the shared reader returned for that property; `status` is the
reader's (known, conflicting or multiple) and `current` says whether any of its sources is fresh
and inside its published validity. Conflicts are listed side by side; nothing is chosen.

A fact or absence with a `purpose` was shown beside the answer, not asked for. It is never a
requested field and never changes `status`. `emergency_contact` is a campus number sent with a
safety reply. `also_published` is the office's own contact note, read by the code because a
requested email or phone is not published: that note is where an office lists its role and
service contacts.

The packet is all a writer sees, so it must stand alone. A notice that stands for words the Brain
used to write itself (the emergency guidance, "I can't see your student record", the greeting)
carries those words as `approved_text`: the writer states them, it never composes them. A
`safety` notice always has it, and `emergency_reminder` marks the standing reminder that goes
with a reply cut short; only a real `safety` notice makes the status `emergency`.
"""

import json
import re
from datetime import date, datetime
from typing import Any

from rockygpt_brain import dining
from rockygpt_brain.answers import _current as source_is_current
from rockygpt_brain.answers import citation_url
from rockygpt_brain.hours_on import WEEKDAYS, hours_on
from rockygpt_brain.shuttle import SCOPE_TEXT, SPECIAL_TEXT

PACKET_VERSION = "1.0"
STATUSES = frozenset({"complete", "partial", "insufficient", "ambiguous", "not_found",
                      "emergency", "no_facts_needed"})
CONTACT_FIELDS = frozenset({"name", "department", "email", "phones", "offices", "prefers_email",
                            "preferred_contact", "contact_note", "website"})
# Replies that cannot carry facts: the question is outside what the Brain can look up.
CANNOT_ANSWER = frozenset({"unsupported", "account_limit", "clarification"})
ABSENCE_KEYS = frozenset({"subject", "predicate", "checked_at", "checks", "current", "source_ids"})
FACT_KEYS = frozenset({"id", "subject", "predicate", "value", "status", "current", "source_ids"})


class PacketInvalid(ValueError):
    """The packet does not meet the contract, so nothing may be written from it."""


def _source(raw: dict[str, Any]) -> dict[str, Any]:
    source = {
        "id": raw["id"], "title": str(raw["source_key"]), "collection": raw["collection"],
        "urls": list(dict.fromkeys(
            url for candidate in raw["citation_urls"] if (url := citation_url(candidate)))),
        "captured_at": raw.get("collected_at"), "freshness": raw["freshness"],
        "validity": raw["validity"], "valid_from": raw.get("valid_from"),
        "valid_until": raw.get("valid_until"), "current": source_is_current(raw),
        "limitations": list(raw.get("caveats", [])),
    }
    for key in ("original_record_id", "observation_field", "original_collected_at", "season",
                "validity_absence"):
        if key in raw:
            source[key] = raw[key]
    return source


_WINDOW = re.compile(
    r"(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?\s*(?:-|\u2013|\u2014|to|until)\s*"
    r"(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?")


def _minutes(hour: str, minute: str | None, half: str) -> int:
    return (int(hour) % 12 + (12 if half == "p" else 0)) * 60 + int(minute or 0)


def _open_now_periods(hours: Any, at: datetime) -> dict[str, str] | None:
    """A dining venue's labelled periods: open or closed now, which meal, and what is next."""
    periods = dining.parse(hours)
    if not periods:
        return None
    clock = at.hour * 60 + at.minute
    shown = f"{at.hour % 12 or 12}:{at.minute:02d}{'pm' if at.hour >= 12 else 'am'}"
    current = dining.at(periods, clock)
    if current is not None:
        # Periods that meet (lite lunch to 5:00pm, dinner from 5:00pm) are one stretch of service:
        # the venue closes at the stretch's end, not when the current meal ends.
        end, following = current.end, None
        for period in sorted(periods, key=lambda p: p.start):
            if period.start == end and period.end > end:
                following = following or period
                end = period.end
        found: dict[str, str] = {"state": "open", "now": shown, "closes": dining.clock_text(end)}
        if current.label:
            found["meal"] = current.label
        if following is not None:
            found["meal_ends"] = dining.clock_text(current.end)
            if following.label:
                found["then_meal"] = following.label
        later = [p for p in periods if p.start > end]
        if later:
            # Service stops and starts again (dinner to 8:00pm, late night from 9:00pm): "closes"
            # is only this stretch, so the reopening and the day's last close go with it.
            again = min(later, key=lambda p: p.start)
            found["reopens"] = dining.clock_text(again.start)
            if again.label:
                found["reopens_meal"] = again.label
            found["last_closes"] = dining.clock_text(max(p.end for p in periods))
        return found
    found = {"state": "closed", "now": shown}
    coming = dining.after(periods, clock)
    if coming is not None:
        found["next_opens"] = dining.clock_text(coming.start)
        if coming.label:
            found["next_meal"] = coming.label
    return found


def open_now(hours: Any, at: datetime) -> dict[str, str] | None:
    """Whether a day's listed hours are open at `at`, when they are plain enough to say so.

    Only a plain time range ("8:30am-4:30pm", several joined by commas), "24 hours" or "closed" is
    read. Anything else, a range that runs past midnight, or words beside the times is left alone:
    a wrong "open" or "closed" is worse than none. `now` is written the way a person would say the
    time, so the writer can say it as it is.
    """
    if not isinstance(hours, str):
        return None
    text = " ".join(hours.casefold().split())
    clock = at.hour * 60 + at.minute
    shown = f"{at.hour % 12 or 12}:{at.minute:02d}{'pm' if at.hour >= 12 else 'am'}"
    if text == "closed":
        return {"state": "closed", "now": shown}
    if text in {"24 hours", "open 24 hours"}:
        return {"state": "open", "now": shown}
    windows: list[tuple[int, int]] = []
    for found in _WINDOW.finditer(text):
        start = _minutes(found[1], found[2], found[3])
        end = _minutes(found[4], found[5], found[6]) or 24 * 60  # 12:00am closes at midnight
        if end <= start:
            return None
        windows.append((start, end))
    leftover = re.sub(r"[\s,;&]|\band\b", "", _WINDOW.sub("", text))
    if not windows or leftover:
        # Labelled meal periods ("Lunch: 11:00 AM - 02:00 PM; Dinner: ...") read period by period.
        return _open_now_periods(hours, at)
    return {"state": "open" if any(a <= clock < b for a, b in windows) else "closed",
            "now": shown}


def fresh_for_date(fact: dict[str, Any], sources: dict[str, dict[str, Any]]) -> bool:
    """Whether a schedule fact's evidence is fresh and its reviewed absences are current. Whether
    it covers a particular date is the derived fact's `applies`, not this."""
    fresh = any(sources[sid]["freshness"] == "fresh" for sid in fact["source_ids"])
    if fact["predicate"] == "hours" and isinstance(fact["value"], dict):
        fresh = fresh and all(day["absence"]["current"] for day in fact["value"]["days"]
                              if day.get("status") == "not_published")
    return fresh


_FAX = re.compile(r"\bfax\b", re.IGNORECASE)


def _fax_lines(phones: Any) -> list[dict[str, Any]]:
    """The phone lines an office publishes as a fax (by their own type or label)."""
    if not isinstance(phones, list):
        return []
    return [line for line in phones if isinstance(line, dict) and any(
        isinstance(line.get(key), str) and _FAX.search(line[key])
        for key in ("type", "label", "text"))]


def appointment_sentences(facts: dict[str, Any]) -> str | None:
    """The sentences of an office's contact note that say to make an appointment, as written."""
    notes = [str(value["value"]) for prop in facts.get("properties", [])
             if prop.get("key") == "contact_note" and prop.get("status") == "known"
             for value in prop.get("values", [])]
    said = [s.strip() for note in notes for s in re.split(r"(?<=[.!?])\s+", note)
            if re.search(r"\bappointments?\b", s, re.IGNORECASE)]
    return " ".join(said) or None


def unlabelled_text(asked: list[str], labels: list[str]) -> str:
    """What a menu's labels say about an allergen none of them names ("nut free?")."""
    words = " or ".join(asked)
    named = (f"name only {', '.join(labels)}" if labels else "name no allergens")
    return (f"None of these items' published allergen labels list {words}. The labels on this "
            f"menu {named}, so they cannot confirm that an item is free of {words}. For an "
            f"allergy, check with Dining Services before eating.")


class _Builder:
    def __init__(self) -> None:
        self.as_of: datetime | None = None
        self.facts: list[dict[str, Any]] = []
        self.derived: list[dict[str, Any]] = []
        self.missing: list[dict[str, Any]] = []
        self.not_published: list[dict[str, Any]] = []
        self.ambiguities: list[dict[str, Any]] = []
        self.unresolved: list[dict[str, Any]] = []
        self.notices: list[dict[str, Any]] = []
        self.sources: dict[str, dict[str, Any]] = {}
        self.entities: dict[str, dict[str, Any]] = {}
        self.fields: list[str] = []
        # What the student wants to do or know, as the reading of the question put it for a
        # how-to lookup, so a writer leaves out a rule for another situation.
        self.wants: list[str] = []
        self.dataset: dict[str, Any] | None = None

    def _open_now(self, picked: dict[str, Any], asked_on: date) -> dict[str, Any]:
        """Open or closed at this moment, for today's hours that apply and are a plain range.

        Hours a schedule lists for today without published dates are not known to apply, so they
        never give open_now; listed_open_now is the same reading of the listed hours, to be said
        as "by its listed hours" beside the dates the page does not publish."""
        if self.as_of is None or asked_on != self.as_of.date() or "status" in picked:
            return {}
        if picked.get("applicability") == "unverified":
            listed = open_now(picked["value"].get("listed_hours"), self.as_of)
            return {"listed_open_now": listed} if listed else {}
        if not picked["applies"] or picked.get("applicability"):
            return {}
        state = open_now(picked["value"].get("hours"), self.as_of)
        return {"open_now": state} if state else {}

    def office(self, part: dict[str, Any], *, purpose: str | None = None) -> None:
        reader = part["facts"]
        entity = {"id": part["office"]["id"], "name": part["office"]["name"],
                  "kind": reader["entity"]["kind"]}
        if purpose is None:
            self.entities.setdefault(entity["id"], {**entity, "query": part["query"]})
            self.fields.extend(f for f in part["fields"] if f not in self.fields)
            if isinstance(part.get("about"), str) and part["about"] not in self.wants:
                self.wants.append(part["about"])
        self.dataset = self.dataset or {"version": reader.get("dataset_version"),
                                        "identityHash": reader.get("identity_hash")}
        first_new = len(self.facts)
        local: dict[str, dict[str, Any]] = {}
        for raw in reader["sources"]:
            source = _source(raw)
            if self.sources.setdefault(source["id"], source) != source:
                raise PacketInvalid("Two different sources share one id.")
            local[source["id"]] = source
        for prop in reader["properties"]:
            for issue in prop.get("issues", []):
                ids = list(issue["source_ids"])
                if not ids or any(sid not in local for sid in ids):
                    raise PacketInvalid("A schedule issue names a source the reader lacks.")
                if not any(local[sid]["urls"] for sid in ids):
                    self.missing.append({"subject": entity, "predicate": prop["key"],
                                         "reason": "no_citable_source"})
                    continue
                entry = {"subject": entity, "predicate": prop["key"],
                         "reason": ("unverified_schedule" if not prop["values"] else
                                    "partially_unknown_schedule"),
                         "schedule": issue["schedule"], "status": issue["status"],
                         "details": issue["reason"], "days": list(issue["days"]),
                         "source_statements": list(issue["source_statements"]),
                         "source_ids": ids}
                if issue.get("season"):
                    entry["season"] = issue["season"]
                if issue.get("validity_absence"):
                    entry["validity_absence"] = issue["validity_absence"]
                if purpose:
                    entry["purpose"] = purpose
                self.missing.append(entry)
            if prop["status"] == "not_published":
                absence = prop["absence"]
                ids = list(absence["source_ids"])
                if not ids or any(sid not in local for sid in ids) or not absence["checks"]:
                    raise PacketInvalid("A confirmed absence names a source the reader lacks.")
                entry = {"subject": entity, "predicate": prop["key"],
                         "checked_at": absence["checked_at"], "checks": list(absence["checks"]),
                         "current": (absence.get("current") is True
                                     and any(local[sid]["current"] for sid in ids)),
                         "source_ids": ids,
                         **{key: absence[key] for key in ("scope", "reason") if key in absence}}
                if prop.get("documents_published"):
                    # A venue with no daily menu may still post its menu as a file.
                    entry["documents_published"] = list(prop["documents_published"])
                if purpose:
                    entry["purpose"] = purpose
                self.not_published.append(entry)
                continue
            if prop["status"] == "unknown" or not prop["values"]:
                if not prop.get("issues"):
                    entry = {"subject": entity, "predicate": prop["key"], "reason": "unknown"}
                    if prop.get("dates_published"):
                        # A menu for a date the venue has not published: which dates it has.
                        entry["dates_published"] = list(prop["dates_published"])
                    if prop.get("documents_published"):
                        # The venue posts its menu as a file (a PDF menu board) instead.
                        entry["documents_published"] = list(prop["documents_published"])
                    self.missing.append(entry)
                continue
            values = prop["values"]
            if prop["key"] == "phones" and purpose is None and part.get("phone_kind") == "fax":
                # The student asked for the fax: only the lines published as one are facts here.
                lines = values
                values = [{**v, "value": fax} for v in values if (fax := _fax_lines(v["value"]))]
                if not values:
                    # The office publishes phone lines and none of them is a fax: that is what
                    # was read, not an unread gap. Its listing is cited, not its numbers.
                    ids = list(dict.fromkeys(sid for v in lines for sid in v["source_ids"]
                                             if sid in local and local[sid]["urls"]))
                    self.missing.append({"subject": entity, "predicate": "phones",
                                         "reason": "no_fax_line" if ids else "unknown",
                                         **({"source_ids": ids} if ids else {})})
                    continue
            for value in values:
                ids = list(value["source_ids"])
                if not ids or any(sid not in local for sid in ids):
                    raise PacketInvalid("A fact names a source the reader did not return.")
                if not any(local[sid]["urls"] for sid in ids):
                    # Nothing to cite it by: a student is told it cannot be stated, not the value.
                    withheld = {"subject": entity, "predicate": prop["key"],
                                "reason": "no_citable_source"}
                    if withheld not in self.missing:
                        self.missing.append(withheld)
                    continue
                # A venue's weekly row and its dated exceptions are one schedule, not a dispute.
                status = ("known" if prop["status"] == "multiple" and prop["key"] == "hours"
                          and entity["kind"] == "venue" else prop["status"])
                fact = {"id": f"f{len(self.facts) + 1}", "subject": entity,
                        "predicate": prop["key"], "value": value["value"],
                        "status": status,
                        "current": any(local[sid]["current"] for sid in ids), "source_ids": ids}
                if prop["key"] == "hours":
                    fact["current"] = fact["current"] and all(
                        day["absence"]["current"] for day in value["value"]["days"]
                        if day.get("status") == "not_published")
                if purpose:
                    fact["purpose"] = purpose
                self.facts.append(fact)
        when = part.get("when")
        if when and purpose is None:
            asked_on = date.fromisoformat(when["date"])
            venue = entity["kind"] == "venue"
            superseded: list[str] = []
            picks: list[tuple[dict[str, Any], dict[str, Any]]] = []
            for fact in self.facts[first_new:]:
                if fact["predicate"] != "hours" or not isinstance(fact["value"], dict):
                    continue
                picked = hours_on(fact["value"], [local[sid] for sid in fact["source_ids"]],
                                  asked_on)
                picks.append((fact, picked))
            if venue:
                # A venue's dated row is an exception to its weekly row for those dates: when one
                # covers the date, the weekly row is not also an answer for it.
                def dated(fact: dict[str, Any]) -> bool:
                    return any(local[sid]["valid_from"] or local[sid]["valid_until"]
                               for sid in fact["source_ids"])
                if any(picked["applies"] and dated(fact) for fact, picked in picks):
                    kept = [(fact, picked) for fact, picked in picks if dated(fact)]
                elif any(picked["applies"] for _, picked in picks):
                    # An exception for other dates says nothing about this one.
                    kept = [(fact, picked) for fact, picked in picks if picked["applies"]]
                else:
                    kept = picks
                kept_ids = {fact["id"] for fact, _ in kept}
                superseded = [fact["id"] for fact, _ in picks if fact["id"] not in kept_ids]
                picks = kept
            for fact, picked in picks:
                if venue and isinstance(picked["value"].get("hours"), str):
                    periods = dining.parse(picked["value"]["hours"])
                    if periods:
                        shown = dining.named(periods)
                        asked_meal = " ".join(str(when.get("meal") or "").casefold().split())
                        if len(periods) > 1:
                            # Where the day's service stops and starts again: said as it is, so
                            # "does it close between lunch and dinner" is never worked out.
                            picked["value"]["closed_gaps"] = dining.gaps(periods)
                        if asked_meal:
                            # The one period named, when the venue serves it that day.
                            picked["value"]["meal_asked"] = when["meal"]
                            only = [p for p in shown if " ".join(
                                p.get("label", "").casefold().split()) == asked_meal]
                            if not any(p.get("label") for p in shown):
                                # One set of hours with no meal names: no meal is served or not.
                                picked["value"]["meals_not_labelled"] = True
                            elif only:
                                shown = only
                            else:
                                picked["value"]["meal_not_served"] = True
                        picked["value"]["periods"] = shown
                same = next((d for d in self.derived if d["subject"]["id"] == entity["id"]
                             and d["date"] == picked["date"] and d["value"] == picked["value"]),
                            None)
                if same is not None:
                    # The office was looked up twice: the day is worked out once, from both.
                    if fact["id"] not in same["from"]:
                        same["from"].append(fact["id"])
                    same["source_ids"] = list(dict.fromkeys([*same["source_ids"],
                                                             *fact["source_ids"]]))
                    continue
                self.derived.append({
                    "id": f"d{len(self.derived) + 1}", "subject": entity,
                    "predicate": "hours_on", "day": picked["day"], "date": picked["date"],
                    "value": picked["value"], "applies": picked["applies"],
                    # Current on the date asked: fresh evidence whose window covers that date,
                    # which a row that starts tomorrow is for tomorrow though not for today.
                    "current": (fresh_for_date(fact, local) and picked["applies"]
                                and (picked["value"].get("hours") is not None
                                     or picked["value"].get("absence", {}).get("current") is True)),
                    "from": [fact["id"]], "source_ids": list(fact["source_ids"]),
                    **({"status": fact["status"]}
                       if fact["status"] != "known" and not (
                           venue and fact["status"] == "multiple") else {}),
                    **self._open_now(picked, asked_on),
                    **{key: picked[key] for key in ("applicability", "applicability_reason")
                       if key in picked}})
            if venue and superseded:
                # Read, and set aside for this date: not repeated beside the day's answer.
                made = [d for d in self.derived if d["subject"]["id"] == entity["id"]
                        and d["date"] == asked_on.isoformat()]
                if made:
                    made[0]["from"] = list(dict.fromkeys([*made[0]["from"], *superseded]))
        for fact in self.facts[first_new:]:
            menu = fact["value"]
            if fact["predicate"] == "menu" and isinstance(menu, dict) and menu.get(
                    "nutrients_unlisted"):
                # "High protein": the rows carry calories only, so no item is said to match.
                self.notices.append({"type": "nutrients_unlisted", "subject": entity,
                                     "approved_text": (
                                         "The dining service's menu lists each item's calories, "
                                         "portion and diet and allergen labels; it does not list "
                                         f"{' or '.join(menu['nutrients_unlisted'])}.")})
            if fact["predicate"] == "menu" and isinstance(menu, dict) and menu.get(
                    "allergens_unlabelled"):
                # Said in the code's words: no label names it, which is not the same as free of it.
                self.notices.append({"type": "allergen_unlabelled", "subject": entity,
                                     "approved_text": unlabelled_text(
                                         menu["allergens_unlabelled"],
                                         menu.get("allergens_published") or [])})
        if not reader.get("complete", True):
            self.notices.append({"type": "evidence_incomplete", "subject": entity,
                                 "caveats": list(reader.get("caveats", []))})

    def shuttle(self, part: dict[str, Any]) -> None:
        """The timetables read for a shuttle question, and the departures worked out from them."""
        subject = {"id": "shuttle", "name": "Shuttle", "kind": "route"}
        self.entities.setdefault("shuttle", {**subject, "query": part.get("query", "")})
        fact_ids: list[str] = []
        source_ids: list[str] = []
        current = True
        special: list[str] = []
        for reader in part["routes"]:
            # A special-day timetable (a holiday) is offered by name, never as a day's facts.
            if any(value.get("value", {}).get("service_day") == "special"
                   for prop in reader["properties"] if prop["key"] == "trips"
                   for value in prop["values"]):
                special.append(str(reader["entity"]["name"]).removeprefix(
                    "Special Shuttle Schedule: "))
                continue
            self.dataset = self.dataset or {"version": reader.get("dataset_version"),
                                            "identityHash": reader.get("identity_hash")}
            local: dict[str, dict[str, Any]] = {}
            for raw in reader["sources"]:
                source = _source(raw)
                if self.sources.setdefault(source["id"], source) != source:
                    raise PacketInvalid("Two different sources share one id.")
                local[source["id"]] = source
            # A route's stop descriptions reach the answer through the departures they explain.
            for prop in (p for p in reader["properties"] if p["key"] != "stops"):
                for value in prop["values"]:
                    ids = list(value["source_ids"])
                    if not ids or any(sid not in local for sid in ids):
                        raise PacketInvalid("A fact names a source the reader did not return.")
                    fresh = any(local[sid]["current"] for sid in ids)
                    current = current and fresh
                    fact = {"id": f"f{len(self.facts) + 1}",
                            "subject": {"id": reader["entity"]["id"],
                                        "name": reader["entity"]["name"], "kind": "route"},
                            "predicate": prop["key"], "value": value["value"],
                            "status": prop["status"], "current": fresh, "source_ids": ids}
                    self.facts.append(fact)
                    fact_ids.append(fact["id"])
                    source_ids.extend(ids)
            if not reader.get("complete", True):
                self.notices.append({"type": "evidence_incomplete", "subject": subject,
                                     "caveats": list(reader.get("caveats", []))})
        if not fact_ids:
            self.missing.append({"subject": subject, "predicate": "departures",
                                 "reason": "unknown"})
            return
        answer = part["answer"]
        if special:
            self.notices.append({"type": "shuttle_special",
                                 "approved_text": SPECIAL_TEXT.format(days="; ".join(special))})
        if (answer.get("trips") or answer.get("none_left") or answer.get("none_after")
                or answer.get("service") == "none"):
            # Said the same way every time: the timetable is the schedule, not where a shuttle is.
            self.notices.append({"type": "shuttle_scope", "approved_text": SCOPE_TEXT})
        # Beside a named stop, the campus stop is described apart (campus_pickup).
        described = (answer.get("campus_pickup") or answer.get("stop_description")
                     or {}).get("description")
        if answer.get("departs_from") and isinstance(described, str) and described.strip():
            # The campus stop's own page says where the shuttle stops on campus and where it
            # does not go: said as published, so "does it stop at my lot" is never left open.
            self.notices.append({"type": "campus_stop", "approved_text": described.strip()})
        self.derived.append({
            "id": f"d{len(self.derived) + 1}", "subject": subject, "predicate": "departures",
            "day": WEEKDAYS[date.fromisoformat(answer["date"]).weekday()],
            "date": answer["date"], "value": answer, "applies": True, "current": current,
            "from": fact_ids, "source_ids": list(dict.fromkeys(source_ids))})

    def add(self, part: dict[str, Any]) -> None:
        kind = part["kind"]
        if kind == "shuttle_facts":
            self.shuttle(part)
        elif kind == "office_facts":
            self.office(part)
            if part.get("topic"):
                self.add(part["topic"])  # The site the student named sends them to this office.
            if part.get("also"):
                # What the office published beside a contact value it does not publish. It is
                # shown, but it was not asked for, so it is not a requested field or a gap.
                self.office({**part, "facts": part["also"], "fields": []},
                            purpose="also_published")
                if "offices" in part.get("fields", []) and (said := appointment_sentences(
                        part["also"])):
                    # "Where do I go": the office's own words on making an appointment first.
                    self.notices.append({"type": "appointment_note",
                                         "subject": {"id": part["office"]["id"],
                                                     "name": part["office"]["name"],
                                                     "kind": part["office"].get("kind", "office")},
                                         "approved_text": said})
        elif kind == "ambiguous":
            self.ambiguities.append({"query": part["query"], "truncated": part["truncated"],
                                     "candidates": part["candidates"]})
            if part.get("topic"):
                self.add(part["topic"])  # The offices a topic's own page names, to choose from.
        elif kind == "not_found":
            entry = {"query": part["query"], "reason": "no_matching_office"}
            if part.get("approved_text"):
                entry["approved_text"] = part["approved_text"]
            self.unresolved.append(entry)
        elif kind == "safety":
            for contact in part["campusContacts"]:
                self.office(contact, purpose="emergency_contact")
            self.notices.append({"type": "safety", "situation": part["situation"],
                                 "contacts": sorted({c["office"]["id"]
                                                     for c in part["campusContacts"]}),
                                 "approved_text": part["approved_text"]})
        else:
            self.notices.append({"type": kind, **{k: v for k, v in part.items() if k != "kind"}})


def _intent(builder: _Builder) -> str:
    kinds = [notice["type"] for notice in builder.notices]
    if "safety" in kinds:
        return "safety"
    shuttle = any(fact["predicate"] == "trips" for fact in builder.facts)
    if shuttle and not builder.fields:
        return "shuttle"
    if builder.fields:
        wanted = set(builder.fields)
        if "menu" in wanted:
            return "menu"
        if wanted == {"hours"}:
            return "hours"
        if "hours" in wanted:
            return "contact_and_hours"
        return "contact" if wanted <= CONTACT_FIELDS else "office_facts"
    if builder.ambiguities or builder.unresolved:
        return "office_lookup"
    real = [kind for kind in kinds if kind not in {"evidence_incomplete", "incomplete"}]
    return real[0] if real else "unknown"


def _status(builder: _Builder) -> str:
    kinds = {notice["type"] for notice in builder.notices}
    # A fact the Brain worked a date out from is judged by that derived fact, not on its own.
    replaced = {fid for derived in builder.derived for fid in derived["from"]}
    asked = [fact for fact in builder.facts
             if fact.get("purpose") is None and fact["id"] not in replaced]
    absent = [entry for entry in builder.not_published if entry.get("purpose") is None]
    # A confirmed "not published" is an answer too, and so is a day worked out from a schedule.
    answered = asked or absent or builder.derived
    if "safety" in kinds:
        return "emergency"
    if not answered and (builder.ambiguities or "clarification" in kinds):
        return "ambiguous"  # The Brain needs a follow-up question answered before it can look up.
    if not answered and builder.unresolved:
        return "not_found"
    if answered:
        degraded = (builder.missing or builder.ambiguities or builder.unresolved
                    or kinds & ({"evidence_incomplete", "incomplete"} | CANNOT_ANSWER)
                    or any(not f["current"] or f["status"] != "known" for f in asked)
                    or any(not d["current"] for d in builder.derived)
                    or any(not entry["current"] for entry in absent))
        return "partial" if degraded else "complete"
    if builder.missing or kinds & CANNOT_ANSWER:
        return "insufficient"
    return "no_facts_needed"


def build_packet(as_of: str, parts: list[dict[str, Any]]) -> dict[str, Any]:
    """The Fact Packet for a turn's findings; raises PacketInvalid when they break the contract."""
    builder = _Builder()
    try:
        builder.as_of = datetime.fromisoformat(as_of)
    except ValueError:
        builder.as_of = None
    for part in parts:
        builder.add(part)
    packet: dict[str, Any] = {
        "version": PACKET_VERSION,
        "request": {"intent": _intent(builder), "entities": list(builder.entities.values()),
                    "fields": builder.fields, "asOf": as_of, "dataset": builder.dataset,
                    **({"wants": builder.wants} if builder.wants else {})},
        "status": _status(builder),
        "facts": builder.facts,
        "derived_facts": builder.derived,
        "missing": builder.missing,
        "not_published": builder.not_published,
        "ambiguities": builder.ambiguities,
        "unresolved": builder.unresolved,
        "notices": builder.notices,
        "sources": list(builder.sources.values()),
    }
    validate_packet(packet)
    return packet


# Every field the shared reader can confirm unpublished (entity_facts ABSENCE_FIELDS, and a
# venue's menu from its own listing): a confirmed absence of any other field is refused.
ABSENCE_PREDICATES = frozenset({"email", "phones", "offices", "hours", "department",
                               "prefers_email", "preferred_contact", "contact_note", "website",
                               "menu"})


def _plain(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and not any(ord(c) < 32 for c in value)


def _instant_text(value: Any) -> bool:
    try:
        datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    return True


def _validate_absence(entry: Any, source_ids: set[str], held: set[tuple[Any, Any]]) -> None:
    """A confirmed absence is a claim a student will be told: every part of it must hold up."""
    if not isinstance(entry, dict) or not ABSENCE_KEYS <= set(entry):
        raise PacketInvalid("A not-published entry is incomplete.")
    subject = entry["subject"]
    if not isinstance(subject, dict) or not all(
            _plain(subject.get(key)) for key in ("id", "name", "kind")):
        raise PacketInvalid("A not-published entry needs its subject.")
    if entry["predicate"] not in ABSENCE_PREDICATES:
        raise PacketInvalid("A not-published entry names a field that cannot be absent.")
    if not _instant_text(entry["checked_at"]) or not isinstance(entry["current"], bool):
        raise PacketInvalid("A not-published entry needs its check date and whether it is current.")
    if any(key in entry and not _plain(entry[key]) for key in ("scope", "reason")):
        raise PacketInvalid("A not-published entry needs plain review scope and reason.")
    checks = entry["checks"]
    if not isinstance(checks, list) or not checks or not all(
            isinstance(check, dict) and _plain(check.get("section"))
            and isinstance(check.get("url"), str) and check["url"].startswith("https://")
            and _plain(check["url"]) and _instant_text(check.get("checked_at"))
            for check in checks):
        raise PacketInvalid("A not-published entry needs the pages it checked.")
    ids = entry["source_ids"]
    if (not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids)
            or not set(ids) <= source_ids):
        raise PacketInvalid("A not-published entry needs listed sources.")
    if (subject["id"], entry["predicate"]) in held:
        raise PacketInvalid("A field cannot be both answered and confirmed not published.")


DERIVED_KEYS = frozenset({"id", "subject", "predicate", "day", "date", "value", "applies",
                          "current", "from", "source_ids"})


def _validate_derived(entry: Any, source_ids: set[str], fact_ids: set[str]) -> None:
    """A worked-out value is a claim a student will be told: it must trace to what it came from."""
    if not isinstance(entry, dict) or not DERIVED_KEYS <= set(entry):
        raise PacketInvalid("A derived fact is incomplete.")
    if entry["predicate"] not in {"hours_on", "departures"} or entry["day"] not in WEEKDAYS:
        raise PacketInvalid("A derived fact is not a kind the Brain works out.")
    try:
        worked = date.fromisoformat(entry["date"])
    except (TypeError, ValueError) as error:
        raise PacketInvalid("A derived fact needs its date.") from error
    if WEEKDAYS[worked.weekday()] != entry["day"]:
        raise PacketInvalid("A derived fact's weekday is not its date's weekday.")
    if not isinstance(entry["applies"], bool) or not isinstance(entry["current"], bool):
        raise PacketInvalid("A derived fact has the wrong types.")
    if "applicability" in entry and (
            entry["applicability"] != "unverified" or entry["applies"] or entry["current"]
            or not _plain(entry.get("applicability_reason"))):
        raise PacketInvalid("An unverified seasonal date must remain explicitly unverified.")
    if isinstance(entry.get("value"), dict) and "listed_hours" in entry["value"]:
        # What a schedule lists for a weekday is never an opening time for an unverified date.
        listed = entry["value"]["listed_hours"]
        if entry.get("applicability") != "unverified" or entry["value"].get("hours") is not None:
            raise PacketInvalid("Listed hours belong only to an unverified date, beside null hours")
        if listed is not None and not _plain(listed):
            raise PacketInvalid("Listed hours must be the schedule's own text.")
    if "status" in entry and entry["status"] not in {"known", "conflicting", "multiple"}:
        raise PacketInvalid("A derived fact has an unknown status.")
    if not isinstance(entry["value"], dict) or (
            entry["predicate"] == "hours_on" and "hours" not in entry["value"]):
        raise PacketInvalid("A derived fact needs its value.")
    if (not isinstance(entry["from"], list) or not entry["from"]
            or not set(entry["from"]) <= fact_ids):
        raise PacketInvalid("A derived fact must name the facts it was read from.")
    ids = entry["source_ids"]
    if not isinstance(ids, list) or not ids or not set(ids) <= source_ids:
        raise PacketInvalid("A derived fact needs listed sources.")
    subject = entry["subject"]
    if not isinstance(subject, dict) or not all(
            _plain(subject.get(key)) for key in ("id", "name", "kind")):
        raise PacketInvalid("A derived fact needs its subject.")


_LISTS = ("facts", "derived_facts", "missing", "not_published", "ambiguities", "unresolved",
          "notices", "sources")


def validate_packet(packet: dict[str, Any]) -> None:
    """Refuse a packet a writer could not safely trust."""
    if packet.get("version") != PACKET_VERSION:
        raise PacketInvalid("Unsupported packet version.")
    if packet.get("status") not in STATUSES:
        raise PacketInvalid("Unknown packet status.")
    request = packet.get("request")
    if not isinstance(request, dict) or not isinstance(request.get("intent"), str) \
            or not request["intent"] or not isinstance(request.get("asOf"), str):
        raise PacketInvalid("The packet has no request intent and time.")
    for key in _LISTS:
        if not isinstance(packet.get(key), list):
            raise PacketInvalid(f"The packet's {key} must be a list.")
    source_ids = [source.get("id") for source in packet["sources"]]
    if len(source_ids) != len(set(source_ids)) or not all(isinstance(i, str) for i in source_ids):
        raise PacketInvalid("Source ids must be unique text.")
    fact_ids: set[str] = set()
    for fact in packet["facts"]:
        if not FACT_KEYS <= set(fact):
            raise PacketInvalid("A fact is missing a required field.")
        if fact["id"] in fact_ids:
            raise PacketInvalid("Fact ids must be unique.")
        fact_ids.add(fact["id"])
        if not fact["source_ids"] or not set(fact["source_ids"]) <= set(source_ids):
            raise PacketInvalid("Every fact needs sources the packet lists.")
        if fact["status"] not in {"known", "conflicting", "multiple"}:
            raise PacketInvalid("A fact has an unknown status.")
        if not isinstance(fact["current"], bool) or not isinstance(fact["predicate"], str):
            raise PacketInvalid("A fact has the wrong types.")
    for derived in packet["derived_facts"]:
        _validate_derived(derived, set(source_ids), fact_ids)
    for notice in packet["notices"]:
        text = notice.get("approved_text")
        if not isinstance(notice.get("type"), str) or (
                notice["type"] == "safety" and not (isinstance(text, str) and text)):
            raise PacketInvalid("A safety notice must carry its approved text.")
    for entry in packet["missing"]:
        if not {"subject", "predicate", "reason"} <= set(entry):
            raise PacketInvalid("A missing entry is incomplete.")
        if "source_ids" in entry and (not isinstance(entry["source_ids"], list)
                or not entry["source_ids"]
                or not all(isinstance(sid, str) for sid in entry["source_ids"])
                or not set(entry["source_ids"]) <= set(source_ids)):
            raise PacketInvalid("A missing field explanation needs its listed sources.")
    held = {(fact["subject"].get("id"), fact["predicate"]) for fact in packet["facts"]
            if isinstance(fact.get("subject"), dict)}
    held |= {(entry["subject"].get("id"), entry["predicate"]) for entry in packet["missing"]
             if isinstance(entry.get("subject"), dict)}
    for entry in packet["not_published"]:
        _validate_absence(entry, set(source_ids), held)
    try:
        json.dumps(packet, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise PacketInvalid("The packet is not plain JSON.") from error
