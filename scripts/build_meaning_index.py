"""Build the meaning index beside the active release graph, for finding how-to units by meaning.

Every unit a how-to read can offer (each linked office page's how-to entries and headed sections,
numbered exactly as the Brain numbers them) is embedded with its page and heading, and each how-to
entry also with three questions a student might type for it, written once by the chat model. Both
are cached by their text (CACHE_DIR), so a new release only pays for units that changed.

    python scripts/build_meaning_index.py --dir PUBLISHED_DIR --cache CACHE_DIR

Run it through local-env.py brain (it reads BRAIN_OPENAI_API_KEY and BRAIN_OPENAI_PROJECT, never
prints them) after scripts/build_graph.py. It writes release-*.meaning.json and .meaning.f32 next
to the active release file; the Brain uses them only for that release. Embeddings find units; they
are never evidence.
"""

import argparse
import array
import hashlib
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from rockygpt_brain.academics import requisite_codes
from rockygpt_brain.retrieval.entity_facts import _procedure_units, linked_rows
from rockygpt_brain.retrieval.graph_store import POINTER, GraphOnlyFacts
from rockygpt_brain.retrieval.meaning import MEANING_SUFFIX, VECTORS_SUFFIX

EMBED_MODEL, DIMENSIONS, CHAT_MODEL = "text-embedding-3-large", 512, "gpt-6-luna"
QUESTIONS_PROMPT = (
    "You see how-to entries from one page of Ramapo College's website. For each entry, write three "
    "short questions a student, parent or visitor might type to a campus chatbot that this entry "
    "answers, in their everyday words (not the page's wording), varied: one plain, one casual, one "
    "describing their situation. Only questions the entry really answers.")
QUESTIONS_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["items"],
                    "properties": {"items": {"type": "array", "items": {
                        "type": "object", "additionalProperties": False,
                        "required": ["n", "questions"],
                        "properties": {"n": {"type": "integer"},
                                       "questions": {"type": "array",
                                                     "items": {"type": "string"}}}}}}}


def _post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    headers = {"content-type": "application/json",
               "authorization": "Bearer " + os.environ["BRAIN_OPENAI_API_KEY"]}
    if os.environ.get("BRAIN_OPENAI_PROJECT"):
        headers["openai-project"] = os.environ["BRAIN_OPENAI_PROJECT"]
    for attempt in range(5):
        try:
            request = urllib.request.Request(f"https://api.openai.com/v1/{path}",
                                             data=json.dumps(body).encode(), headers=headers)
            with urllib.request.urlopen(request, timeout=180) as response:  # noqa: S310
                return json.load(response)  # type: ignore[no-any-return]
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503) or attempt == 4:
                raise RuntimeError(f"OpenAI {path} failed with HTTP {error.code}") from None
        except (urllib.error.URLError, TimeoutError):
            if attempt == 4:
                raise RuntimeError(f"OpenAI {path} could not be reached") from None
        time.sleep(3 * (attempt + 1))
    raise RuntimeError("unreachable")


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def _where(url: str) -> str:
    path = url.split("://", 1)[-1].split("/", 1)[-1].split("#")[0]
    return " > ".join(w.replace("-", " ") for w in path.strip("/").split("/") if w and w != "pages")


def _pieces(text: str, start: int = 1_000, size: int = 1_000) -> list[tuple[int, str]]:
    """A long text past its first `start` characters, in pieces of about `size` characters that
    end at a sentence or space and overlap by 100, each with where it starts."""
    pieces = []
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            cut = max(text.rfind(". ", start, end), text.rfind(" ", start + size // 2, end))
            end = cut + 1 if cut > start else end
        at = max(0, start - 100)
        pieces.append((at, text[at:end].strip()))
        start = end
    return pieces


def collect(facts: GraphOnlyFacts) -> tuple[str, str, list[dict[str, Any]], dict[str, str]]:
    """Every how-to unit of the active release, once per page, with the offices linking it."""
    version, identity = facts.active_release()
    units: dict[tuple[str, str], dict[str, Any]] = {}
    names: dict[str, str] = {}
    with facts.snapshot() as snapshot:
        facts._pin(snapshot, version, identity)
        for entity in snapshot.entities:
            if not any(link.get("collection") == "office_sections" for link in entity["links"]):
                continue
            names[entity["id"]] = entity["name"]
            rows = linked_rows(entity, snapshot.record_reader(entity, "office_sections"),
                               "office_sections")
            for row in rows:
                offered = _procedure_units(row)
                if offered and offered[0][0].startswith("e"):
                    offered = [*offered, *_procedure_units(row, sections_only=True)]
                for index, unit, _, _ in offered:
                    key = (str(row["id"]), index)
                    if key in units:
                        if entity["id"] not in units[key]["entities"]:
                            units[key]["entities"].append(entity["id"])
                        continue
                    units[key] = {"record_id": key[0], "index": index, "entities": [entity["id"]],
                                  "page": str(row.get("url") or ""), "heading": unit["heading"],
                                  "text": unit["text"], "entry": index.startswith("e")}
    return version, identity, list(units.values()), names


CARD_HEADINGS = 30


def office_cards(facts: GraphOnlyFacts, version: str, identity: str,
                 units: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    """Each office's card, read with the shared fact reader: its name, its other names, the
    titles of its contact lines ("Director of Athletics, Intramurals and Recreation") and the
    headings of the how-to units it is first to link (what it handles)."""
    handles: dict[str, list[str]] = {}
    for unit in units:
        headings = handles.setdefault(unit["entities"][0], [])
        if unit["heading"] not in headings and len(headings) < CARD_HEADINGS:
            headings.append(unit["heading"])
    with facts.snapshot() as snapshot:
        facts._pin(snapshot, version, identity)
        offices = [(e["id"], e["name"], list(e.get("aliases") or []))
                   for e in snapshot.entities if e.get("kind") == "office"]
    cards = []
    for entity_id, name, aliases in offices:
        found = facts.get_office_facts(entity_id, ["phones"], version, identity_hash=identity)
        titles = [str(line.get("type")) for prop in found["properties"]
                  if prop["key"] == "phones" and prop["status"] == "known"
                  for value in prop["values"] for line in value["value"]
                  if isinstance(line, dict) and " " in str(line.get("type") or "").strip()]
        card = f"Ramapo College office: {name}"
        if aliases:
            card += f"\nAlso called: {', '.join(aliases)}"
        if titles:
            card += f"\nContacts: {'; '.join(dict.fromkeys(titles))}"
        if handles.get(entity_id):
            card += f"\nHandles: {'; '.join(handles[entity_id])}"
        cards.append((entity_id, name, card))
    return cards


def academic_cards(facts: GraphOnlyFacts, version: str,
                   identity: str,
                   ) -> tuple[list[tuple[str, str, str]], dict[str, list[str]], dict[str, str]]:
    """Each program's and course's card, read with the shared fact reader: a program's name,
    degree, the start of its description and its recommended plans' own names (concentrations:
    Contemporary Arts of Visual Arts); a course's code, title and the start of its description.
    They find a program or course a question names only in part ("the english major", "a class
    on machine learning"). Also the reverse of every course's published prerequisites: which
    courses name each course; and each course's catalog title, for everyday names ("calc 2").
    """
    entities = facts.identities(("program", "course"))
    cards: list[tuple[str, str, str]] = []
    for entity in entities:
        if entity["kind"] != "program":
            continue
        found = facts.get_program_facts(entity["id"], version, identity_hash=identity)
        program = found["properties"][0]["values"][0]["value"]
        card = f"Ramapo College program: {program.get('name')} ({program.get('degree')})"
        plans = next((p for p in found["properties"] if p["key"] == "plans"), None)
        variants = sorted({str(v["value"].get("name")) for v in (plans or {}).get("values", [])
                           if v["value"].get("name")} - {str(program.get("name"))})
        if variants:
            card += f"\nRecommended plans: {', '.join(variants)}"
        if program.get("description"):
            card += f"\n{str(program['description'])[:500]}"
        cards.append((entity["id"], "program", card))
    courses = [e["id"] for e in entities if e["kind"] == "course"]
    required_by: dict[str, list[str]] = {}
    titles: dict[str, str] = {}
    for start in range(0, len(courses), 12):
        for found in facts.get_course_facts(courses[start:start + 12], version,
                                            identity_hash=identity):
            course = found["properties"][0]["values"][0]["value"]
            for code in requisite_codes(course.get("requisites") or []):
                required_by.setdefault(code, []).append(str(course.get("code")))
            card = f"Ramapo College course: {course.get('code')} {course.get('name') or ''}"
            titles[found["entity"]["id"]] = str(course.get("name") or "")
            gen_ed = [str(a) for a in course.get("attributes") or []
                      if "gen ed" in str(a).casefold()]
            if gen_ed:
                titles[found["entity"]["id"] + "#gen_ed"] = "; ".join(gen_ed)
            if course.get("description"):
                card += f"\n{str(course['description'])[:400]}"
            cards.append((found["entity"]["id"], "course", card))
    # Each club's or student organization's directory name and category ("is there a club for
    # anime", "cultural clubs").
    clubs = [e["id"] for e in facts.identities(("club", "organization"))]
    for start in range(0, len(clubs), 12):
        for found in facts.get_club_facts(clubs[start:start + 12], version,
                                          identity_hash=identity):
            club = found["properties"][0]["values"][0]["value"]
            cards.append((found["entity"]["id"], "club",
                          f"Ramapo College student club: {club.get('name')}"
                          + (f" ({club['category']})" if club.get("category") else "")))
    # Each person's name, title, school and interests ("who researches machine learning").
    people = [e["id"] for e in facts.identities(("person",))]
    for start in range(0, len(people), 6):
        for found in facts.get_person_facts(people[start:start + 6], version,
                                            identity_hash=identity):
            prop = found["properties"][0]
            person = prop["values"][0]["value"] if prop["values"] else {}
            card = f"Ramapo College faculty or staff: {found['entity']['name']}"
            for key in ("title", "school", "department"):
                if person.get(key):
                    card += f", {person[key]}"
            interests = [*person.get("research_interests", []),
                         *person.get("teaching_interests", []), *person.get("courses", [])]
            if interests:
                card += f"\nInterests and courses: {'; '.join(interests)[:500]}"
            cards.append((found["entity"]["id"], "person", card))
    # Each calendar event's title, tags, host, place, admission and the start of its
    # description ("free food this week", "the next home game", "guest speakers").
    events = [e["id"] for e in facts.identities(("event",))]
    for start in range(0, len(events), 40):
        for found in facts.get_event_facts(events[start:start + 40], version,
                                           identity_hash=identity):
            prop = found["properties"][0]
            event = prop["values"][0]["value"] if prop["values"] else {}
            card = f"Ramapo College campus event: {event.get('title') or found['entity']['name']}"
            if event.get("tags"):
                card += f" ({', '.join(event['tags'])})"
            for label, key in (("Hosted by", "organizer"), ("At", "location"),
                               ("Admission", "ticket_status")):
                if event.get(key):
                    card += f"\n{label}: {event[key]}"
            if event.get("description"):
                card += f"\n{str(event['description'])[:400]}"
            cards.append((found["entity"]["id"], "event", card))
    return cards, {code: sorted(set(later)) for code, later in required_by.items()}, titles


def write_questions(units: list[dict[str, Any]], cache: dict[str, list[str]]) -> int:
    """Three student questions per how-to entry not already in the cache, read 12 entries at a time
    per page. Returns how many entries were read."""
    due: dict[str, list[dict[str, Any]]] = {}
    for unit in units:
        if unit["entry"] and _digest(unit["heading"], unit["text"]) not in cache:
            due.setdefault(unit["page"], []).append(unit)
    batches = [(page, items[k:k + 12]) for page, items in due.items()
               for k in range(0, len(items), 12)]

    def read(batch: tuple[str, list[dict[str, Any]]]) -> dict[str, list[str]]:
        page, items = batch
        entries = [{"n": n, "task": u["heading"], "steps": u["text"][:500]}
                   for n, u in enumerate(items)]
        try:
            reply = _post("responses", {
                "model": CHAT_MODEL, "store": False, "max_output_tokens": 8000,
                "input": [{"role": "system", "content": QUESTIONS_PROMPT},
                          {"role": "user", "content": json.dumps(
                              {"page": page, "entries": entries}, ensure_ascii=False)}],
                "text": {"format": {"type": "json_schema", "name": "questions",
                                    "schema": QUESTIONS_SCHEMA, "strict": True}}})
            text = next((c["text"] for o in reply["output"] if o["type"] == "message"
                         for c in o["content"] if c["type"] == "output_text"), "")
            got = json.loads(text)["items"] if text else []
        except (RuntimeError, ValueError, KeyError):
            return {}
        return {_digest(items[x["n"]]["heading"], items[x["n"]]["text"]):
                [q.strip() for q in x["questions"] if q.strip()][:3]
                for x in got if 0 <= x["n"] < len(items)}

    with ThreadPoolExecutor(16) as pool:
        for found in pool.map(read, batches):
            cache.update(found)
    return sum(len(items) for _, items in batches)


def embed_all(texts: list[str], cache: dict[str, list[float]]) -> int:
    """Embed every text not already cached; returns how many were embedded."""
    due = list(dict.fromkeys(t for t in texts if _digest(EMBED_MODEL, str(DIMENSIONS), t)
                             not in cache))
    for start in range(0, len(due), 300):
        chunk = due[start:start + 300]
        reply = _post("embeddings", {"model": EMBED_MODEL, "input": chunk,
                                     "dimensions": DIMENSIONS})
        for text, item in zip(chunk, sorted(reply["data"], key=lambda d: d["index"]), strict=True):
            cache[_digest(EMBED_MODEL, str(DIMENSIONS), text)] = item["embedding"]
    return len(due)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the meaning index for the active release.")
    parser.add_argument("--dir", required=True, type=Path, help="the published graph directory")
    parser.add_argument("--cache", required=True, type=Path, help="embedding and question cache")
    args = parser.parse_args(argv)
    if not os.environ.get("BRAIN_OPENAI_API_KEY"):
        print("BRAIN_OPENAI_API_KEY is not set.", file=sys.stderr)
        return 2
    started = time.monotonic()
    pointer = json.loads((args.dir / POINTER).read_text())
    release_file = args.dir / pointer["file"]
    facts = GraphOnlyFacts(args.dir)
    version, identity, units, names = collect(facts)
    cards = office_cards(facts, version, identity, units)
    academic, required_by, course_titles = academic_cards(facts, version, identity)
    names.update({entity_id: name for entity_id, name, _ in cards})
    args.cache.mkdir(parents=True, exist_ok=True)
    questions_path = args.cache / "questions.json"
    vectors_path = args.cache / f"vectors-{EMBED_MODEL}-{DIMENSIONS}.json"
    questions: dict[str, list[str]] = (json.loads(questions_path.read_text())
                                       if questions_path.exists() else {})
    read = write_questions(units, questions)
    questions_path.write_text(json.dumps(questions, ensure_ascii=False))
    texts: list[tuple[int, str]] = []
    # Where each vector's text starts in its unit: 0 its opening, a piece's offset, -1 a question.
    offsets: list[int] = []
    for n, unit in enumerate(units):
        header = f"Ramapo College page: {_where(unit['page'])}\n{unit['heading']}\n"
        texts.append((n, header + unit["text"][:1200]))
        offsets.append(0)
        # A long section is also embedded piece by piece past its opening, so an item deep in a
        # list ("Animals" in a prohibited-items list) can be found by its meaning.
        for at, piece in _pieces(unit["text"]):
            texts.append((n, header + "…" + piece))
            offsets.append(at)
        if unit["entry"]:
            asked = questions.get(_digest(unit["heading"], unit["text"]), [])
            texts += [(n, q) for q in asked]
            offsets += [-1] * len(asked)
    cache: dict[str, list[float]] = (json.loads(vectors_path.read_text())
                                     if vectors_path.exists() else {})
    embedded = embed_all([t for _, t in texts] + [card for _, _, card in cards]
                         + [card for _, _, card in academic], cache)
    vectors_path.write_text(json.dumps(cache))
    flat = array.array("f")
    # The office cards' vectors follow the units' vectors, in the order of "cards"; then the
    # program and course cards, in the order of "academic".
    for text in ([t for _, t in texts] + [card for _, _, card in cards]
                 + [card for _, _, card in academic]):
        vector = cache[_digest(EMBED_MODEL, str(DIMENSIONS), text)]
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        flat.extend(x / norm for x in vector)
    meta = {"dataset_version": version, "identity_hash": identity, "model": EMBED_MODEL,
            "dimensions": DIMENSIONS, "entities": names,
            "units": [[u["record_id"], u["index"], u["entities"]] for u in units],
            "owner": [n for n, _ in texts], "offsets": offsets,
            "cards": [entity_id for entity_id, _, _ in cards],
            "academic": [[entity_id, kind] for entity_id, kind, _ in academic],
            "required_by": required_by,
            "course_titles": course_titles,
            "labels": [[_where(u["page"]), u["heading"]] for u in units]}
    if (version, identity) != (pointer["dataset_version"], pointer["identity_hash"]):
        print("The active release changed while the index was built; run it again.",
              file=sys.stderr)
        return 1
    # Each file is written whole and renamed into place, so a running Brain never reads half of one.
    for suffix, data in ((VECTORS_SUFFIX, flat.tobytes()),
                         (MEANING_SUFFIX, json.dumps(meta).encode())):
        temporary = release_file.with_suffix(suffix + ".tmp")
        temporary.write_bytes(data)
        os.replace(temporary, release_file.with_suffix(suffix))
    print(f"meaning index for {version}: {len(units)} units, {len(cards)} office cards, "
          f"{len(academic)} program and course cards, "
          f"{len(texts)} vectors "
          f"({read} entries given questions, {embedded} texts embedded), "
          f"{time.monotonic() - started:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
