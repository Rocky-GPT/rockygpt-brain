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

Each event and club card is also given its kinds (rockygpt_brain/kinds.py: art and crafts,
cultural, sports...) by the chat model, cached by the card's text, so "any art events" lists every
art event whatever its listing says. --kinds-only gives them to an index already built (an older
release's, or a copy on a laptop) without embedding anything again:

    python scripts/build_meaning_index.py --dir PUBLISHED_DIR --cache CACHE_DIR --kinds-only
"""

import argparse
import array
import hashlib
import json
import math
import os
import random
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from importlib.resources import files
from pathlib import Path
from typing import Any

from rockygpt_brain.academics import requisite_codes
from rockygpt_brain.kinds import KINDS, KINDS_VERSION, LABEL_READS
from rockygpt_brain.kinds import RULES as KINDS_RULES
from rockygpt_brain.retrieval.entity_facts import _procedure_units, linked_rows
from rockygpt_brain.retrieval.graph_store import POINTER, GraphOnlyFacts
from rockygpt_brain.retrieval.meaning import MEANING_SUFFIX, VECTORS_SUFFIX

EMBED_MODEL, DIMENSIONS, CHAT_MODEL = "text-embedding-3-large", 512, "gpt-6-luna"
QUESTIONS_PROMPT = (
    "You see how-to entries from one page of Ramapo College's website. For each entry, write three "
    "short questions a student, parent or visitor might type to a campus chatbot that this entry "
    "answers, in their everyday words (not the page's wording), varied: one plain, one casual, one "
    "describing their situation. Only questions the entry really answers.")
# Only how-to entries get questions. Questions for every page section were tried (Brain #159, dev
# release v2-20261104003042, $0.54): on 79 how-to questions with known answer sections
# (rockygpt-evals search-reach-2026-11-04), they brought no answer section into the 60 the one call
# picks from that the sections' own text did not already bring (77 of 79 either way), and moved 6
# answer sections out of its first 12 while moving 2 in. "how do i add money 2 my id card" still
# missed "Buy Ramapo Dollars", and was once answered from PaperCut's "add funds" (11-04). Questions
# written for 10,700 sections are 32,000 more ways for a near miss to match a student's words.
# A build stops asking once its calls have cost QUESTIONS_BUDGET_NUSD at the chat model's bundled
# prices, and the entries left are asked by the next build.
QUESTIONS_BUDGET_NUSD = 1_000_000_000
QUESTIONS_SAVE_EVERY = 100  # Batches between saves of the question cache, kept if a run stops.
PRICES = json.loads(files("rockygpt_brain").joinpath("provider-release.json").read_text())
QUESTIONS_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["items"],
                    "properties": {"items": {"type": "array", "items": {
                        "type": "object", "additionalProperties": False,
                        "required": ["n", "questions"],
                        "properties": {"n": {"type": "integer"},
                                       "questions": {"type": "array",
                                                     "items": {"type": "string"}}}}}}}
KINDS_PROMPT = (KINDS_RULES + "\n\nKinds:\n"
                + "\n".join(f"- {kind.key}: {kind.holds}" for kind in KINDS))
KINDS_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["items"],
                "properties": {"items": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False, "required": ["n", "kinds"],
                    "properties": {"n": {"type": "integer"},
                                   "kinds": {"type": "array", "items": {
                                       "type": "string",
                                       "enum": [kind.key for kind in KINDS]}}}}}}}
MAX_KINDS = 3


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
            if any("writing intensive" in str(a).casefold()
                   for a in course.get("attributes") or []):
                titles[found["entity"]["id"] + "#writing_intensive"] = "yes"
            if course.get("description"):
                card += f"\n{str(course['description'])[:400]}"
            cards.append((found["entity"]["id"], "course", card))
    cards += club_cards(facts, version, identity)
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
    cards += event_cards(facts, version, identity)
    return cards, {code: sorted(set(later)) for code, later in required_by.items()}, titles


def club_cards(facts: GraphOnlyFacts, version: str,
               identity: str) -> list[tuple[str, str, str]]:
    """Each club's or student organization's directory name, category and its own mission ("is
    there a club for anime", "cultural clubs", "service clubs"). Only clubs with a club directory
    record have facts to read: a club node made from a ramapo.edu page alone (one the page says
    is no longer active) has no directory row, and its status is answered from the identity, not
    from a card."""
    listed = set(facts.club_directory_ids())
    clubs = [e["id"] for e in facts.identities(("club", "organization")) if e["id"] in listed]
    cards: list[tuple[str, str, str]] = []
    for start in range(0, len(clubs), 12):
        for found in facts.get_club_facts(clubs[start:start + 12], version,
                                          identity_hash=identity):
            club = found["properties"][0]["values"][0]["value"]
            cards.append((found["entity"]["id"], "club",
                          f"Ramapo College student club: {club.get('name')}"
                          + (f" ({club['category']})" if club.get("category") else "")
                          + (f"\n{str(club['mission'])[:300]}" if club.get("mission") else "")))
    return cards


def event_cards(facts: GraphOnlyFacts, version: str,
                identity: str) -> list[tuple[str, str, str]]:
    """Each calendar event's title, tags, host, place, admission and the start of its
    description ("free food this week", "the next home game", "guest speakers")."""
    events = [e["id"] for e in facts.identities(("event",))]
    cards: list[tuple[str, str, str]] = []
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
    return cards


def label_kinds(cards: list[tuple[str, str, str]],
                cache: dict[str, list[str]]) -> tuple[int, int]:
    """Each event and club card's kinds (kinds.py), read by the chat model 25 cards at a time for
    the cards not already in the cache, which is keyed by the list's version and the card's text
    (a weekly event's dates share one card). Each card is read LABEL_READS times, beside other
    cards each time, and keeps the kinds more than half of its reads give; a card fewer than two
    reads answered for is failed. Returns how many cards were read and how many of those failed
    (they are read again by the next build)."""
    due = list({_digest(KINDS_VERSION, card): card for _, kind, card in cards
                if kind in ("event", "club") and _digest(KINDS_VERSION, card) not in cache
                }.values())
    batches = []
    for read_number in range(LABEL_READS):
        order = list(due)
        # The same orders for the same cards and rules, so a build can be run again as it was.
        random.Random(f"{KINDS_VERSION}:{read_number}").shuffle(order)  # noqa: S311 - an order, not a secret
        batches += [order[k:k + 25] for k in range(0, len(order), 25)]

    def read(batch: list[str]) -> dict[str, list[str]]:
        try:
            reply = _post("responses", {
                "model": CHAT_MODEL, "store": False, "max_output_tokens": 4000,
                "input": [{"role": "system", "content": KINDS_PROMPT},
                          {"role": "user", "content": json.dumps(
                              {"listings": [{"n": n, "listing": card}
                                            for n, card in enumerate(batch)]},
                              ensure_ascii=False)}],
                "text": {"format": {"type": "json_schema", "name": "kinds",
                                    "schema": KINDS_SCHEMA, "strict": True}}})
            text = next((c["text"] for o in reply["output"] if o["type"] == "message"
                         for c in o["content"] if c["type"] == "output_text"), "")
            got = json.loads(text)["items"] if text else []
        except (RuntimeError, ValueError, KeyError):
            return {}
        known = {kind.key for kind in KINDS}
        return {_digest(KINDS_VERSION, batch[x["n"]]):
                list(dict.fromkeys(k for k in x["kinds"] if k in known))[:MAX_KINDS]
                for x in got if 0 <= x["n"] < len(batch)}

    reads: dict[str, list[list[str]]] = {}
    with ThreadPoolExecutor(8) as pool:
        for found in pool.map(read, batches):
            for key, kinds in found.items():
                reads.setdefault(key, []).append(kinds)
    for key, given in reads.items():
        if len(given) < 2:
            continue
        counts = Counter(kind for kinds in given for kind in kinds)
        first = list(dict.fromkeys(kind for kinds in given for kind in kinds))
        kept = [kind for kind in first if counts[kind] * 2 > len(given)]
        cache[key] = sorted(kept, key=lambda kind: (-counts[kind], first.index(kind)))[:MAX_KINDS]
    return len(due), sum(_digest(KINDS_VERSION, card) not in cache for card in due)


def kinds_of(cards: list[tuple[str, str, str]],
             cache: dict[str, list[str]]) -> dict[str, list[str]]:
    """Each event's and club's kinds by entity id, for the cards that have any."""
    return {entity_id: cache[key] for entity_id, kind, card in cards
            if kind in ("event", "club") and cache.get(key := _digest(KINDS_VERSION, card))}


def load_kinds(cache_dir: Path) -> dict[str, list[str]]:
    path = cache_dir / "kinds.json"
    found: dict[str, list[str]] = json.loads(path.read_text()) if path.exists() else {}
    return found


def add_kinds(published: Path, cache_dir: Path) -> int:
    """Give the index beside the active release its event and club kinds, nothing else changed:
    for an index built before kinds, or a copy of production's on a laptop."""
    pointer = json.loads((published / POINTER).read_text())
    release_file = published / pointer["file"]
    meta_path = release_file.with_suffix(MEANING_SUFFIX)
    if not meta_path.is_file():
        print("No meaning index beside the active release; build it first.", file=sys.stderr)
        return 1
    meta = json.loads(meta_path.read_text())
    if (meta["dataset_version"], meta["identity_hash"]) != (pointer["dataset_version"],
                                                            pointer["identity_hash"]):
        print("The meaning index was built for another release.", file=sys.stderr)
        return 1
    facts = GraphOnlyFacts(published)
    version, identity = facts.active_release()
    cards = [*club_cards(facts, version, identity), *event_cards(facts, version, identity)]
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = load_kinds(cache_dir)
    read, failed = label_kinds(cards, cache)
    (cache_dir / "kinds.json").write_text(json.dumps(cache, ensure_ascii=False))
    meta["kinds"], meta["kinds_version"] = kinds_of(cards, cache), KINDS_VERSION
    temporary = meta_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(meta))
    os.replace(temporary, meta_path)
    print(f"kinds for {version}: {len(meta['kinds'])} of {len(cards)} event and club cards "
          f"have a kind ({read} cards read, {failed} failed)")
    return 0 if not failed else 1


def cost_nusd(reply: dict[str, Any]) -> int:
    """What one chat reply cost, from its usage, at the bundled prices (cached input apart)."""
    usage = reply.get("usage") or {}
    given = int(usage.get("input_tokens") or 0)
    cached = int((usage.get("input_tokens_details") or {}).get("cached_tokens") or 0)
    return ((given - cached) * int(PRICES["input_nusd_per_token"])
            + cached * int(PRICES["cached_input_nusd_per_token"])
            + int(usage.get("output_tokens") or 0) * int(PRICES["output_nusd_per_token"]))


def write_questions(units: list[dict[str, Any]], cache: dict[str, list[str]],
                    save: Callable[[], None] | None = None,
                    budget_nusd: int = QUESTIONS_BUDGET_NUSD) -> tuple[int, int]:
    """Three student questions for each how-to entry not already in the cache, read 12 entries at
    a time per page, until the calls have cost `budget_nusd` (the rest wait for the next build).
    `save` is called every QUESTIONS_SAVE_EVERY batches and at the end, so a run that is stopped
    keeps what it paid for. Returns how many entries were read and what the calls cost."""
    due: dict[str, list[dict[str, Any]]] = {}
    for unit in units:
        if unit["entry"] and _digest(unit["heading"], unit["text"]) not in cache:
            due.setdefault(unit["page"], []).append(unit)
    batches = iter([(page, items[k:k + 12]) for page, items in due.items()
                    for k in range(0, len(items), 12)])

    def read(batch: tuple[str, list[dict[str, Any]]]) -> tuple[dict[str, list[str]], int, int]:
        page, items = batch
        entries = [{"n": n, "task": u["heading"], "steps": u["text"][:500]}
                   for n, u in enumerate(items)]
        try:
            reply = _post("responses", {
                "model": CHAT_MODEL, "store": False, "max_output_tokens": 8000,
                # Questions in a student's words need little thought; at the default effort the
                # model's reasoning would cost more than the questions it writes.
                "reasoning": {"effort": "low"},
                "input": [{"role": "system", "content": QUESTIONS_PROMPT},
                          {"role": "user", "content": json.dumps(
                              {"page": page, "entries": entries}, ensure_ascii=False)}],
                "text": {"format": {"type": "json_schema", "name": "questions",
                                    "schema": QUESTIONS_SCHEMA, "strict": True}}})
        except RuntimeError:
            return {}, 0, len(items)
        try:
            text = next((c["text"] for o in reply["output"] if o["type"] == "message"
                         for c in o["content"] if c["type"] == "output_text"), "")
            got = json.loads(text)["items"] if text else []
            found = {_digest(items[x["n"]]["heading"], items[x["n"]]["text"]):
                     [q.strip() for q in x["questions"] if q.strip()][:3]
                     for x in got if 0 <= x["n"] < len(items)}
        except (ValueError, KeyError, TypeError):
            found = {}
        return found, cost_nusd(reply), len(items)

    spent = read_count = finished = 0
    pending: set[Future[tuple[dict[str, list[str]], int, int]]] = set()
    with ThreadPoolExecutor(16) as pool:
        while True:
            while len(pending) < 16 and spent < budget_nusd:
                batch = next(batches, None)
                if batch is None:
                    break
                pending.add(pool.submit(read, batch))
            if not pending:
                break
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                found, cost, count = future.result()
                cache.update(found)
                spent, read_count, finished = spent + cost, read_count + count, finished + 1
                if save is not None and finished % QUESTIONS_SAVE_EVERY == 0:
                    save()
    if save is not None:
        save()
    return read_count, spent


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


def load_vectors(cache_dir: Path) -> dict[str, list[float]]:
    """The embedding cache: the JSON one a laptop keeps, else the compact one a scheduled job
    keeps (the digests in .keys.json, their vectors in .f32, in the same order)."""
    path = cache_dir / f"vectors-{EMBED_MODEL}-{DIMENSIONS}"
    if path.with_suffix(".json").exists():
        return json.loads(path.with_suffix(".json").read_text())  # type: ignore[no-any-return]
    if not path.with_suffix(".keys.json").exists():
        return {}
    keys = json.loads(path.with_suffix(".keys.json").read_text())
    flat = array.array("f")
    flat.frombytes(path.with_suffix(".f32").read_bytes())
    return {key: flat[k * DIMENSIONS:(k + 1) * DIMENSIONS].tolist()
            for k, key in enumerate(keys)}


def save_vectors(cache_dir: Path, cache: dict[str, list[float]], used: list[str]) -> None:
    """The JSON cache whole when one is kept; else the compact one with only this index's texts
    (about 105 MB, small enough for a scheduled job's cache)."""
    path = cache_dir / f"vectors-{EMBED_MODEL}-{DIMENSIONS}"
    if path.with_suffix(".json").exists():
        path.with_suffix(".json").write_text(json.dumps(cache))
        return
    keys = list(dict.fromkeys(used))
    flat = array.array("f")
    for key in keys:
        flat.extend(cache[key])
    path.with_suffix(".f32").write_bytes(flat.tobytes())
    path.with_suffix(".keys.json").write_text(json.dumps(keys))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the meaning index for the active release.")
    parser.add_argument("--dir", required=True, type=Path, help="the published graph directory")
    parser.add_argument("--cache", required=True, type=Path, help="embedding and question cache")
    parser.add_argument("--kinds-only", action="store_true",
                        help="only give the index already built its event and club kinds")
    args = parser.parse_args(argv)
    if not os.environ.get("BRAIN_OPENAI_API_KEY"):
        print("BRAIN_OPENAI_API_KEY is not set.", file=sys.stderr)
        return 2
    if args.kinds_only:
        return add_kinds(args.dir, args.cache)
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
    questions: dict[str, list[str]] = (json.loads(questions_path.read_text())
                                       if questions_path.exists() else {})

    def save_questions() -> None:
        # Written whole and renamed into place: a run stopped mid-write keeps the last whole one.
        temporary = questions_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(questions, ensure_ascii=False))
        os.replace(temporary, questions_path)

    read, spent = write_questions(units, questions, save_questions)
    kinds = load_kinds(args.cache)
    # A card the model could not read gets its kinds at the next build; the day's index still
    # goes out.
    kinds_read, kinds_failed = label_kinds(academic, kinds)
    (args.cache / "kinds.json").write_text(json.dumps(kinds, ensure_ascii=False))
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
    cache = load_vectors(args.cache)
    every = ([t for _, t in texts] + [card for _, _, card in cards]
             + [card for _, _, card in academic])
    embedded = embed_all(every, cache)
    save_vectors(args.cache, cache, [_digest(EMBED_MODEL, str(DIMENSIONS), t) for t in every])
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
            "labels": [[_where(u["page"]), u["heading"]] for u in units],
            "kinds": kinds_of(academic, kinds), "kinds_version": KINDS_VERSION}
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
          f"({read} entries given questions for ${spent / 1e9:.2f}, {embedded} texts embedded, "
          f"{kinds_read} event and "
          f"club cards given kinds, {kinds_failed} failed), "
          f"{time.monotonic() - started:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
