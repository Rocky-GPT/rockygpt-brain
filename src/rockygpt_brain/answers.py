"""Render campus values from the shared fact reader, never from model-written values."""

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from rockygpt_brain.retrieval import EvidenceUnavailable


@dataclass
class Rendered:
    text: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    supported: bool = False
    complete: bool = True


def literal(value: Any) -> str:
    """Keep published text as text rather than executable HTML or model-authored links."""
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value)
    value = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_{}\[\]()#!|])", r"\\\1", value)


def readable_quote(content: str, role: str) -> str:
    """An earlier reply as plain words, so RockyGPT's own formatting never shows as symbols.

    Only unescaped marks are RockyGPT's own formatting. Published text that happened to contain
    brackets or asterisks was escaped when it was written, and stays as it was published.
    """
    if role != "assistant":
        return content
    text = re.sub(r"(?<!\\)\[([^\]]*)\]\([^)\s]*\)", r"\1", content)  # [label](url) becomes label.
    text = re.sub(r"(?<!\\)\*\*(.+?)(?<!\\)\*\*", r"\1", text)
    return re.sub(r"\\([\\`*_{}\[\]()#!|])", r"\1", text)  # Undo the escaping literal() adds.


def citation_url(value: Any) -> str | None:
    if (
        not isinstance(value, str)
        or len(value) > 4_096
        or "\\" in value
        or any(c.isspace() or ord(c) < 32 for c in value)
        or any(ord(c) < 32 for c in unquote(value))
    ):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return None
        _ = parsed.port  # A malformed or out-of-range port is not a usable citation.
    except ValueError:
        return None
    return quote(value, safe="/:?&=#%+@,;-._~!$*")


def _value_text(key: str, value: Any) -> str:
    """Format structured fields without changing their published content or order."""
    if key == "phones" and isinstance(value, list):
        phones = []
        for phone in value:
            if not isinstance(phone, dict) or not set(phone) <= {
                "number",
                "extension",
                "type",
                "label",
                "text",
            }:
                phones.append(literal(phone))
                continue
            text = literal(phone.get("number") or phone.get("text") or "")
            if phone.get("extension") is not None:
                text += ("; " if text else "") + "extension " + literal(phone["extension"])
            if phone.get("type"):
                text += " (" + literal(phone["type"]) + ")"
            if phone.get("label"):
                text += " (" + literal(phone["label"]) + ")"
            phones.append(text)
        return "; ".join(phones)
    if key == "offices" and isinstance(value, list):
        return "; ".join(
            (literal(item["location"]) +
             (" (" + literal(item["label"]) + ")" if item.get("label") else ""))
            if isinstance(item, dict) and set(item) <= {"location", "label"}
            and isinstance(item.get("location"), str) else literal(item)
            for item in value)
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return literal(value)


_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _day_span(first: str, last: str, count: int) -> str:
    if count == 1:
        return first
    return f"{first} and {last}" if count == 2 else f"{first} to {last}"


def _plain_name(name: str) -> str:
    """A name without a trailing abbreviation such as "(CSI)", which adds nothing to read."""
    return re.sub(r"\s*\([A-Z]{2,6}\)\s*$", "", name).casefold().strip()


def _hours_text(value: dict[str, Any], *, name_it: bool) -> str:
    """One schedule as published. Next weekdays with the same text share a span; a day with no
    record is never covered by one."""
    runs: list[dict[str, Any]] = []
    for entry in value["days"]:
        day = entry["day"]
        index = _WEEKDAYS.index(day) if day in _WEEKDAYS else None
        last = runs[-1] if runs else None
        if (last and last["hours"] == entry["hours"] and index is not None
                and last["index"] is not None and index == last["index"] + 1):
            last.update(last=day, index=index, count=last["count"] + 1)
        else:
            runs.append({"first": day, "last": day, "hours": entry["hours"], "index": index,
                         "count": 1})
    text = "; ".join(
        f"{literal(_day_span(r['first'], r['last'], r['count']))}: "
        f"{literal(r['hours']) if r['hours'] is not None else 'Hours unavailable'}"
        for r in runs
    )
    if name_it and value.get("schedule"):
        text = f"{literal(value['schedule'])}. {text}"
    if value.get("season") and value["season"] not in str(value.get("schedule", "")):
        text = f"{literal(value['season'])}. {text}"
    notes = " ".join(literal(note) for note in value.get("notes", []))
    return f"{text}. Published note: {notes}" if notes else text


def _current(source: dict[str, Any]) -> bool:
    return (source["freshness"] == "fresh" and source["validity"] in {"current", "unspecified"}
            and not (source.get("season") and
                     (not source.get("valid_from") or not source.get("valid_until"))))


def _boundaries(source: dict[str, Any], *, show_dates: bool) -> str:
    notes = []
    start, end = source.get("valid_from"), source.get("valid_until")
    if start and end:
        notes.append(f"published validity {literal(start)} through {literal(end)}")
    elif start:
        notes.append(f"published validity from {literal(start)}; no end date")
    elif end:
        notes.append(f"published validity through {literal(end)}; no start date")
    elif show_dates:
        notes.append("no published validity range")
    if source.get("season") and (not start or not end):
        notes.append("seasonal applicability unverified; complete dates are not published")
    if source["validity"] == "expired":
        notes.append("expired record")
    elif source["validity"] == "future":
        notes.append("future record")
    if source["freshness"] != "fresh":
        notes.append("stale capture" if source["freshness"] == "stale" else "freshness unknown")
    if show_dates or not _current(source):
        captured = source.get("collected_at")
        notes.append("captured " + literal(captured) if captured else "capture time unknown")
    return "; ".join(notes)


def _asserted_sources(
    value: dict[str, Any],
    prop: dict[str, Any],
    sources: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Every displayed value must retain its exact assertions in this result."""
    assertions = {assertion["id"]: assertion for assertion in prop["assertions"]}
    expected = json.dumps(value["value"], sort_keys=True, allow_nan=False)
    asserted_source_ids = set()
    if not value["assertion_ids"] or not value["source_ids"]:
        raise EvidenceUnavailable("A displayed value is missing original evidence.")
    for aid in value["assertion_ids"]:
        assertion = assertions.get(aid)
        if (
            assertion is None
            or json.dumps(
                assertion["value"],
                sort_keys=True,
                allow_nan=False,
            )
            != expected
        ):
            raise EvidenceUnavailable("A displayed value is not supported by its assertions.")
        asserted_source_ids.add(assertion["source_id"])
    if asserted_source_ids != set(value["source_ids"]) or not asserted_source_ids <= sources.keys():
        raise EvidenceUnavailable("A displayed value has invalid original evidence links.")
    return [sources[sid] for sid in value["source_ids"]]


def render_facts(facts: dict[str, Any]) -> Rendered:
    """Keep conflicts and dated observations visible; never silently select a winner."""
    entity = facts["entity"]
    heading = literal(entity["name"])
    lines = [f"**{heading}**"]
    sources = {s["id"]: s for s in facts["sources"]}
    if len(sources) != len(facts["sources"]):
        raise EvidenceUnavailable("Duplicate original evidence identifiers.")
    citations: dict[str, dict[str, Any]] = {}
    supported = False
    complete = bool(facts.get("complete", True))
    if not complete:
        lines.append("Some linked evidence is unavailable; these details may be incomplete.")
    for prop in facts["properties"]:
        label = literal(prop["label"])
        for issue in prop.get("issues", []):
            issue_sources = [sources[sid] for sid in issue["source_ids"] if sid in sources]
            if not issue_sources or len(issue_sources) != len(issue["source_ids"]):
                raise EvidenceUnavailable("An unavailable schedule has invalid evidence links.")
            references = []
            for source in issue_sources:
                urls = list(dict.fromkeys(url for candidate in source["citation_urls"]
                                         if (url := citation_url(candidate))))
                if not urls:
                    continue
                sid = source["id"]
                citations[sid] = {
                    "id": sid, "title": str(source["source_key"]), "url": urls[0], "urls": urls,
                    "collection": source["collection"], "collected_at": source["collected_at"],
                    "freshness": source["freshness"], "valid_from": source["valid_from"],
                    "valid_until": source["valid_until"], "limitations": source["caveats"],
                    "record_title": str(entity["name"]),
                }
                links = " ".join(f"[{literal(source['source_key'])}]({url})" for url in urls)
                boundary = _boundaries(source, show_dates=not _current(source))
                references.append(links + (f" ({boundary})" if boundary else ""))
            if references:
                lines.append(f"{literal(issue['schedule'])}: {literal(issue['reason'])} "
                             f"{' '.join(references)}")
            else:
                lines.append(f"{label}: I can't verify a current value from citable evidence.")
            complete = False
        if prop["status"] == "unknown":
            if not prop.get("issues"):
                lines.append(f"{label}: I have no published information about this.")
            complete = False
            continue
        if prop["status"] == "not_published":
            # The pages were read and state no value: that is an answer, not a gap.
            absence = prop["absence"]
            checked_sources = [sources[sid] for sid in absence["source_ids"] if sid in sources]
            if not checked_sources or len(checked_sources) != len(absence["source_ids"]):
                raise EvidenceUnavailable("A confirmed absence has bad evidence links.")
            urls = list(dict.fromkeys(
                url for check in absence["checks"] if (url := citation_url(check["url"]))))
            if not urls:
                complete = False
                lines.append(f"{label}: I can't verify a current value from fresh, "
                             "citable evidence.")
                continue
            first = checked_sources[0]
            cited = f"{first['id']}:{prop['key']}:not_published"
            citations[cited] = {
                "id": cited, "title": str(first["source_key"]), "url": urls[0], "urls": urls,
                "collection": first["collection"], "collected_at": absence["checked_at"],
                "freshness": first["freshness"], "valid_from": first["valid_from"],
                "valid_until": first["valid_until"], "limitations": first["caveats"],
                "record_title": str(entity["name"]),
            }
            links = " ".join(f"[{literal(first['source_key'])}]({url})" for url in urls)
            checked = literal(absence["checked_at"][:10])
            line = f"{label}: not published on Ramapo's pages (checked {checked}) {links}"
            fresh = any(_current(source) for source in checked_sources)
            if absence.get("current") is True and fresh:
                supported = True
            else:
                complete = False
                line += " (dated observation; current status unverified)"
            lines.append(line)
            continue
        if prop["status"] == "conflicting":
            lines.append(f"{label}: conflicting published records; I can't choose a current value.")
            complete = False
        elif prop["status"] == "multiple":
            lines.append(f"{label}: different values have different published date ranges.")
        displayed = current_supported = False
        missing_citation = False
        for value in prop["values"]:
            valid_sources = _asserted_sources(value, prop, sources)
            references: list[str] = []
            value_current = False
            for source in valid_sources:
                urls = list(
                    dict.fromkeys(
                        url
                        for candidate in source["citation_urls"]
                        if (url := citation_url(candidate))
                    )
                )
                if not urls:
                    missing_citation = True
                    continue
                sid = source["id"]
                citations[sid] = {
                    "id": sid,
                    "title": str(source["source_key"]),
                    "url": urls[0],
                    "urls": urls,
                    "collection": source["collection"],
                    "collected_at": source["collected_at"],
                    "freshness": source["freshness"],
                    "valid_from": source["valid_from"],
                    "valid_until": source["valid_until"],
                    "limitations": source["caveats"],
                    "record_title": str(entity["name"]),
                }
                if "original_record_id" in source:
                    citations[sid].update({key: source[key] for key in (
                        "original_record_id", "observation_field", "original_collected_at",
                    )})
                links = " ".join(f"[{literal(source['source_key'])}]({url})" for url in urls)
                boundary = _boundaries(source, show_dates=prop["status"] != "known")
                references.append(links + (f" ({boundary})" if boundary else ""))
                value_current |= _current(source)
            if references:
                prefix = (
                    "Published value" if prop["status"] in {"conflicting", "multiple"} else label
                )
                if not value_current:
                    prefix += " (dated observation; current value unverified)"
                if prop["key"] == "hours":
                    # A schedule name is shown when there are several, or when it says more than
                    # the office's own name (the heading already says that).
                    own = _plain_name(str(value["value"].get("schedule", "")))
                    formatted = _hours_text(
                        value["value"],
                        name_it=len(prop["values"]) > 1 or own != _plain_name(str(entity["name"])),
                    )
                else:
                    formatted = _value_text(prop["key"], value["value"])
                lines.append(f"{prefix}: {formatted} {'; '.join(dict.fromkeys(references))}")
                displayed = True
                current_supported |= value_current
        supported |= current_supported
        if missing_citation:
            complete = False
            lines.append(f"{label}: some supporting records lack a usable secure citation.")
        if not displayed:
            complete = False
            lines.append(f"{label}: I can't verify a current value from fresh, citable evidence.")
        elif not current_supported:
            # Every value shown is already marked "dated observation; current value unverified"
            # with its date, so a second line saying the same thing would only repeat it.
            complete = False
    return Rendered("\n\n".join(lines), list(citations.values()), supported, complete)
