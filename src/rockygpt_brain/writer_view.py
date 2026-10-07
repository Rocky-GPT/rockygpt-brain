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
  omitted when they hold the usual value: `status` "known", `current` true, `freshness`
               "fresh", `validity` "unspecified" and its stock limitation, `truncated` false, a
               reason that is the only one there is, and a list of rows with no rows (`facts`,
               `missing`, `not_published`, `ambiguities`, `unresolved`, `notices`, `sources`)
  renamed      a subject is its name; a source is its number `n` (numbered once the uncited ones
               are left out), and `sources` on a fact or an absence lists those numbers;
               `candidates` are `choices` (names only)
  first        when the packet holds an emergency, its fixed wording comes before everything else

A field a row in one of those lists gains later and this function does not know is carried through
unchanged, and never overwrites a key the view sets. A field added anywhere else (the packet's top
level, the request, a subject) is not carried; the test that compares every packet value with the
view fails until it is handled.
"""

from typing import Any

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
                     "valid_from", "valid_until", "current", "limitations", "original_record_id"})


def _rest(item: dict[str, Any], handled: frozenset[str]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key not in handled}


def writer_view(packet: dict[str, Any]) -> dict[str, Any]:
    """The part of a validated packet a writer needs, as plain JSON."""
    cited = {sid for kind in ("facts", "not_published", "derived_facts", "missing")
             for entry in packet[kind]
             for sid in entry.get("source_ids", [])}
    kept = [source for source in packet["sources"] if source["id"] in cited]
    number = {source["id"]: n for n, source in enumerate(kept, start=1)}
    request = packet["request"]
    view: dict[str, Any] = {
        "asked": {"intent": request["intent"],
                  "offices": [entity["name"] for entity in request["entities"]],
                  "fields": list(request["fields"]), "asOf": request["asOf"]},
        "status": packet["status"],
    }

    derived, replaced = [], set()
    for entry in packet["derived_facts"]:
        row: dict[str, Any] = {"subject": entry["subject"]["name"],
                               "predicate": entry["predicate"], "day": entry["day"],
                               "date": entry["date"], "value": entry["value"]}
        if not entry["applies"]:
            row["applies"] = False  # Then it is not current either: the date is outside it.
        elif not entry["current"]:
            row["current"] = False
        row["sources"] = [number[sid] for sid in entry["source_ids"]]
        derived.append({**_rest(entry, _DERIVED), **row})
        replaced.update(entry["from"])
    if derived:
        view["derived_facts"] = derived

    facts = []
    for fact in packet["facts"]:
        if fact["id"] in replaced:
            continue
        row = {"subject": fact["subject"]["name"], "predicate": fact["predicate"],
               "value": fact["value"]}
        if fact["status"] != "known":
            row["status"] = fact["status"]
        if not fact["current"]:
            row["current"] = False
        if "purpose" in fact:
            row["purpose"] = fact["purpose"]
        row["sources"] = [number[sid] for sid in fact["source_ids"]]
        facts.append({**_rest(fact, _FACT), **row})
    if facts:
        view["facts"] = facts

    missing = []
    for entry in packet["missing"]:
        row = {"subject": entry["subject"]["name"], "predicate": entry["predicate"]}
        if entry["reason"] != "unknown":
            row["reason"] = entry["reason"]
        if entry.get("source_ids"):
            row["sources"] = [number[sid] for sid in entry["source_ids"]]
        missing.append({**_rest(entry, _MISSING), **row})
    if missing:
        view["missing"] = missing

    absent = []
    for entry in packet["not_published"]:
        row = {"subject": entry["subject"]["name"], "predicate": entry["predicate"],
               "checked_at": entry["checked_at"],
               "pages": list(dict.fromkeys(check["url"] for check in entry["checks"]))}
        if not entry["current"]:
            row["current"] = False
        if "purpose" in entry:
            row["purpose"] = entry["purpose"]
        row["sources"] = [number[sid] for sid in entry["source_ids"]]
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

    sources = []
    for source in kept:
        row = {"n": number[source["id"]], "title": source["title"]}
        if source["urls"]:
            row["urls"] = list(source["urls"])
        if source["captured_at"]:
            row["captured_at"] = source["captured_at"]
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
        sources.append({**_rest(source, _SOURCE), **row})
    if sources:
        view["sources"] = sources
    return view
