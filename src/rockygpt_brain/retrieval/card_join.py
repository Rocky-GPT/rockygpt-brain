"""Join the how-to tasks a release lists page by page into answer cards, by rules alone.

The night job lists tasks one page at a time, so one task sits on many pages: "apply for a parking
permit" is on eleven, from Public Safety's own page to a 2022 orientation FAQ that names a site no
longer used. A card is one task: every page that lists it, the office that handles it, the one
page whose steps are said (the winner), and why each other page is not. No model is called here;
what the rules cannot settle is flagged for a person to look at.

Which tasks are the same task (`groups`). Each task has the questions a student might type for it
(scripts/build_meaning_index.py); two tasks are compared by the mean of their questions' vectors,
which says what is asked and not which page it is on. Measured on the 8,102 tasks of dev release
v2-20261111133419 (2026-11-11):

- The same task worded two ways scores 0.85 to 0.97 ("Apply for a parking permit" on ten pages),
  a related but different task 0.70 to 0.85 ("Get a visitor or temporary parking permit").
- A group must be alike on average (JOIN_MEAN) with no pair far apart (JOIN_LEAST). Asking every
  pair to reach 0.85 split the parking pages in three; an average alone let chains form.
- Two tasks of one page are never one task: the page's reader listed them apart.
- Two tasks that each carry a name the other lacks are never one task (`names_differ`). Vectors
  alone joined 58 "Donate to the ... Scholarship" pages into one group at 0.86 to 0.94; the names
  rule kept 810 such pairs apart.

Which cards are near-copies (`near`). The join commits to "one task", so it asks a lot, and two
pages that word one task apart stay two cards: "Apply for a parking permit" (eleven pages) and
"Obtain and display a campus parking permit" are 0.84 alike. A question then matches both and
neither leads (card_answer.best_card). So each card also names the cards that pass every join
rule but the average, at a lower one (NEAR_MEAN, with no pair under NEAR_LEAST): its
near-copies, which a question's best card does not have to lead. Measured on dev release
v2-20261111174533 with 90 practice questions (2026-11-11, never the blind set):

- At 0.78 and 0.70, code can answer 25 of the 70 how-to questions (14 without near-copies),
  each from a card that answers it, and none of the 20 look-alikes. At 0.75 and 0.65 it is 26,
  and a look-alike ("what's the phone number for public safety") is answered too.
- Lowering the join's own numbers instead does little and joins what should stay apart: at 0.78
  and 0.70 it is 18 of 70 with a look-alike answered, and 799 cards join tasks that today's
  rules keep apart. Groups split what one question sees as alike; near-copies are counted from
  the question's best card.
- Only near-copies with the same owner: 21 of 70. The owner is not asked for; "Log in to
  register for classes" (Advising) and "Register for classes online" (Registrar) are one task.

Who handles it (`owners`): the office the pages say handles it, most named first; when no page
names one, the office whose pages list it most. A term that is over has no say.

Which page wins (`build`): never a past term while a current page exists, then the owner's own
page, then the newest date, then the most steps.

Whose page is said among near-copies (`page_ahead`). The best-matching card answers, unless a
near-copy's page beats its own by one of the join's two plain rules for a winning page: an old
page loses to one that is not old, and another office's page loses to the own page of an office
that handles both cards. Read on the same release (11 practice questions and 40 written ones
whose best card had a near-copy within the lead, each candidate's answer side by side):

- The best-matching card alone gave the weaker page in 4 of the 11: "connect to the campus
  wifi" from a 2021 FAQ ("scan for networks, and connect") while a near-copy names
  Ramapo-Secure, "change my major" from an EOF page ("an admissions counselor will assist you")
  while the Registrar's own page has the form and the 5 to 7 days.
- The join's finer rules must not be asked across cards. The newest date picked "Do not
  withdraw after the published deadline" (2 steps) over the Registrar's 6 steps; the most steps
  picked a space committee's 5 steps for "request a space on campus for an event". Near-copies
  are only probably one task, so the best match keeps its say unless its page is plainly worse.
- The owner's page counts only for an owner both cards share: "am I required to have a meal
  plan" matched its own card (Dining Services, on a Residence Life page), and a near-copy on
  Student Accounts' own page lists prices, not who needs a plan.
"""

import hashlib
import importlib
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from rockygpt_brain.retrieval.entity_facts import _stem

numpy: Any
try:  # By name, so the type check never reads numpy's own stubs (see retrieval/meaning.py).
    numpy = importlib.import_module("numpy")
except ImportError:  # pragma: no cover - numpy is a dependency of the graph build.
    numpy = None

JOIN_MEAN, JOIN_LEAST = 0.85, 0.78
NEAR_MEAN, NEAR_LEAST = 0.78, 0.70  # Near-copies: every join rule, at a lower average.
NEAREST = 40  # The most alike tasks looked at for each task.
CHUNK = 1024  # Tasks compared with all the others at a time (32 MB at 8,000 tasks).
BIG_GROUP = 12  # A card with more tasks than this is flagged: its group may hold several tasks.

READY, NEEDS_LOOK, DATE_PASSED = "ready", "needs_look", "date_passed"
# The cards are written beside the release graph file, like its meaning index.
CARDS_SUFFIX = ".cards.json"


@dataclass(frozen=True)
class Task:
    """One how-to task as one page lists it (`_procedure_units`, keyed "e<n>")."""

    record_id: str
    key: str
    name: str
    url: str
    nodes: tuple[str, ...]  # The nodes whose links hold its page.
    steps: int = 1
    applies_to: str | None = None
    handled_by: str | None = None
    section: str | None = None
    updated: date | None = None  # The day its page says it last changed.
    over: str | None = None  # The term, year or deadline it is for, when that is over.
    old: bool = False  # Its page last changed OLD_PAGE_YEARS or more ago.


# --- the same task? -----------------------------------------------------------------------------

# Capitalised words that name no particular thing: a term, a month, the college itself.
_NOT_NAMES = frozenset(
    "fall spring summer winter january february march april may june july august september "
    "october november december academic year semester ramapo college rcnj new jersey".split())
_POSSESSIVE = re.compile(r"['’]s\b")


def _words(text: str) -> list[str]:
    """A text's words as written, "W.I.N." read as WIN and "J.Lee’s" as JLee."""
    return re.findall(r"[A-Za-z0-9]+", _POSSESSIVE.sub("", text.replace(".", "")))


def _named(name: str) -> frozenset[str]:
    """The names a task's name carries: its capitalised words past the first ("Rotary Memorial
    Endowed Scholarship", "FAFSA"), by stem. Task names are written in sentence case."""
    return frozenset(_stem(word.casefold()) for word in _words(name)[1:]
                     if len(word) > 1 and word[0].isupper() and word.casefold() not in _NOT_NAMES)


def _context(task: Task) -> frozenset[str]:
    """Every word a task is listed with, by stem: its name, who it is for, its section and its
    page's address ("Get a meal plan" on /reslife/pine-hall is about Pine Hall)."""
    text = " ".join(part for part in (task.name, task.applies_to, task.section) if part)
    text += " " + urlsplit(task.url).path.replace("-", " ").replace("/", " ")
    return frozenset(_stem(word.casefold()) for word in _words(text))


def names_differ(first: Task, second: Task) -> bool:
    """Whether each task carries a name the other is not listed with: two scholarships, two
    placement tests. One task naming what the other leaves unsaid ("Complete the FAFSA", "Apply
    for federal aid") is no difference."""
    return bool(_named(first.name) - _context(second)) and bool(
        _named(second.name) - _context(first))


def _alike(tasks: Sequence[Task], matrix: Any, least: float) -> set[tuple[float, int, int]]:
    """Every pair of tasks of two pages that are `least` alike or more, as (how alike, the two
    places in `tasks`, the lower first). Only each task's NEAREST most alike tasks are looked
    at."""
    count = len(tasks)
    found: set[tuple[float, int, int]] = set()
    reach = min(NEAREST + 1, count)
    for start in range(0, count, CHUNK):
        alike = matrix[start:start + CHUNK] @ matrix.T
        for offset, row in enumerate(alike):
            i = start + offset
            for j in numpy.argpartition(-row, reach - 1)[:reach].tolist():
                if j != i and row[j] >= least and tasks[i].record_id != tasks[j].record_id:
                    found.add((round(float(row[j]), 4), min(i, j), max(i, j)))
    return found


def _one_task(first: Sequence[int], second: Sequence[int], matrix: Any, mean: float,
              least: float, named: Sequence[frozenset[str]],
              context: Sequence[frozenset[str]]) -> bool:
    """Whether two groups of tasks (places in the release's tasks) are alike enough to be one
    task: `mean` alike on average with no pair under `least`, and no two of them differ by
    name."""
    alike = matrix[list(first)] @ matrix[list(second)].T
    if alike.mean() < mean or alike.min() < least:
        return False
    return not any(named[x] - context[y] and named[y] - context[x]
                   for x in first for y in second)


def groups(tasks: Sequence[Task], vectors: Any) -> list[list[int]]:
    """The tasks that are one task, as lists of their places in `tasks`; a task like no other is
    a group of one. `vectors` holds one vector of length one per task, in the same order.

    The most alike pair is joined first. Two groups join when their tasks are JOIN_MEAN alike on
    average with no pair under JOIN_LEAST, share no page, and no two of them differ by name."""
    count = len(tasks)
    if not count:
        return []
    if numpy is None:
        raise RuntimeError("Joining tasks into cards needs numpy.")
    matrix = numpy.asarray(vectors, dtype=numpy.float32).reshape(count, -1)
    named = [_named(task.name) for task in tasks]
    context = [_context(task) for task in tasks]
    group_of = list(range(count))
    members: dict[int, list[int]] = {i: [i] for i in range(count)}
    pages: dict[int, set[str]] = {i: {tasks[i].record_id} for i in range(count)}
    for _, i, j in sorted(_alike(tasks, matrix, JOIN_LEAST),
                          key=lambda pair: (-pair[0], pair[1], pair[2])):
        a, b = sorted((group_of[i], group_of[j]))
        if a == b or pages[a] & pages[b]:
            continue
        if not _one_task(members[a], members[b], matrix, JOIN_MEAN, JOIN_LEAST, named, context):
            continue
        for k in members[b]:
            group_of[k] = a
        members[a] += members.pop(b)
        pages[a] |= pages.pop(b)
    return [sorted(group) for _, group in sorted(members.items())]


def near(tasks: Sequence[Task], vectors: Any,
         grouped: Sequence[Sequence[int]]) -> list[list[int]]:
    """For each group of `grouped` (as `groups` gives them), the other groups that are its
    near-copies, by their places in `grouped`: they share no page, no two of their tasks differ
    by name, and their tasks are NEAR_MEAN alike on average with no pair under NEAR_LEAST. The
    join's own rules, at a lower average: the same task worded apart on two pages, which a
    question matches about as well as the group itself."""
    count = len(tasks)
    found: list[list[int]] = [[] for _ in grouped]
    if not count:
        return found
    matrix = numpy.asarray(vectors, dtype=numpy.float32).reshape(count, -1)
    named = [_named(task.name) for task in tasks]
    context = [_context(task) for task in tasks]
    group_of = {i: at for at, group in enumerate(grouped) for i in group}
    pages = [{tasks[i].record_id for i in group} for group in grouped]
    pairs = {(min(group_of[i], group_of[j]), max(group_of[i], group_of[j]))
             for _, i, j in _alike(tasks, matrix, NEAR_LEAST) if group_of[i] != group_of[j]}
    for a, b in sorted(pairs):
        if not pages[a] & pages[b] and _one_task(grouped[a], grouped[b], matrix, NEAR_MEAN,
                                                 NEAR_LEAST, named, context):
            found[a].append(b)
            found[b].append(a)
    return found


def task_vectors(meta: Mapping[str, Any], flat: Any, tasks: Sequence[Task]) -> Any:
    """One vector per task, in order, from a meaning index (its meta and its float vectors): the
    mean of the task's written questions, scaled to length one; its opening's vector while it has
    no questions; zeros when the index does not hold it, so it joins nothing."""
    size = int(meta["dimensions"])
    owner = list(meta["owner"])
    offsets = list(meta.get("offsets") or [0] * len(owner))
    matrix = numpy.asarray(flat, dtype=numpy.float32)[:len(owner) * size].reshape(len(owner), size)
    place = {(str(unit[0]), str(unit[1])): n for n, unit in enumerate(meta["units"])}
    asked: dict[int, list[int]] = {}
    opening: dict[int, int] = {}
    for at, (unit, offset) in enumerate(zip(owner, offsets, strict=True)):
        if offset == -1:
            asked.setdefault(unit, []).append(at)
        elif offset == 0:
            opening.setdefault(unit, at)
    vectors = numpy.zeros((len(tasks), size), dtype=numpy.float32)
    for n, task in enumerate(tasks):
        unit = place.get((task.record_id, task.key))
        rows = [] if unit is None else asked.get(unit) or [
            at for at in [opening.get(unit)] if at is not None]
        if rows:
            mean = matrix[rows].mean(axis=0)
            length = float(numpy.linalg.norm(mean))
            if length:
                vectors[n] = mean / length
    return vectors


# --- who handles it -----------------------------------------------------------------------------

_OFFICE_FILLER = frozenset("the office of department dept center for ramapo college and "
                           "services service".split())


def office_key(name: str) -> str:
    """An office's name as pages write it, reduced to what tells offices apart: "Office of
    Student Accounts", "Student Accounts Office" and "Student Accounts (OSA)" are one key."""
    plain = re.sub(r"\([^)]*\)", " ", name.casefold().replace("&", " and ").replace("’", "'"))
    return " ".join(word for word in re.findall(r"[a-z0-9]+", plain.replace("'s", ""))
                    if word not in _OFFICE_FILLER)


def handlers(names: Mapping[str, Sequence[str]]) -> dict[str, tuple[str, ...]]:
    """Each office key and the nodes it names, from every node's name and other names. "Public
    Safety" names both of its nodes (emergency and non-emergency)."""
    found: dict[str, set[str]] = {}
    for entity_id, known in names.items():
        for name in known:
            if key := office_key(name):
                found.setdefault(key, set()).add(entity_id)
    return {key: tuple(sorted(ids)) for key, ids in found.items()}


def owners(tasks: Sequence[Task], named: Mapping[str, tuple[str, ...]],
           ) -> tuple[tuple[str, ...], bool]:
    """The nodes that handle a group's task, and whether that is clear. The pages' own word wins
    (`handled_by`, when it names a node); else the nodes whose pages list the task. A task for a
    term that is over has no say while a current one is listed, nor has an old page beside a
    newer one (a 2018 Summer Session page tied Student Accounts for its own refund deadline).
    The nodes named most are the owners; it is clear when they always come together (Public
    Safety's two nodes), unclear when two offices tie."""
    current = [task for task in tasks if task.over is None]
    voters = [task for task in current if not task.old] or current or list(tasks)
    said = [named[office_key(task.handled_by)] for task in voters
            if task.handled_by and office_key(task.handled_by) in named]
    votes = said or [task.nodes for task in voters if task.nodes]
    tally = Counter(node for vote in votes for node in vote)
    if not tally:
        return (), False
    most = max(tally.values())
    best = {node for node, count in tally.items() if count == most}
    clear = all(best <= set(vote) or not best & set(vote) for vote in votes)
    return tuple(sorted(best)), clear


# --- one card -----------------------------------------------------------------------------------

_SEASON_MONTH = {"winter": 1, "spring": 5, "summer": 8, "fall": 12}
_MONTH = {name: n for n, name in enumerate(
    "january february march april may june july august september october november "
    "december".split(), 1)}


def _over_order(label: str | None) -> tuple[int, int]:
    """When a past term's label ends, as (year, month), to tell the latest of several: "Fall
    2025" (2025, 12), "2024-2025" (2025, 8), "September 1, 2026" (2026, 9), "2017" (2017, 12)."""
    years = [int(year) for year in re.findall(r"(?<!\d)(?:19|20)\d\d(?!\d)", label or "")]
    if not years:
        return (0, 0)
    words = re.findall(r"[a-z]+", (label or "").casefold())
    month = next((_SEASON_MONTH.get(word) or _MONTH.get(word) for word in words
                  if word in _SEASON_MONTH or word in _MONTH), 8 if len(years) > 1 else 12)
    return (max(years), int(month or 12))


def card_id(record_id: str, key: str) -> str:
    """A card's id, from its winning task: the same from night to night while that page wins."""
    return "c" + hashlib.sha256(f"{record_id}\x1f{key}".encode()).hexdigest()[:12]


def card(tasks: Sequence[Task], named: Mapping[str, tuple[str, ...]]) -> dict[str, Any]:
    """One card from the tasks of one group: its owners, its tasks in the order their pages rank
    (the winner first) with why each other one is not said, its flags and its status.

    Only the winner's steps are said; steps are never mixed from two pages. A task's place:
    "winner" (with its `label` when its own term is over: the card is then for a date that
    passed); "owner_page" for the owner's other current pages; "left_out" for a term that is over
    or a page old beside a newer winner (`why`: "over" with its `label`, or "old"); "mentions"
    for another office's current page."""
    owned_by, clear = owners(tasks, named)
    owning = set(owned_by)

    def rank(task: Task) -> tuple[Any, ...]:
        end = _over_order(task.over)
        return (task.over is not None, -end[0], -end[1], not owning & set(task.nodes),
                task.updated is None, -(task.updated.toordinal() if task.updated else 0),
                -task.steps, task.url, task.record_id, task.key)

    ranked = sorted(tasks, key=rank)
    winner = ranked[0]
    members: list[dict[str, Any]] = []
    for task in ranked:
        place: dict[str, Any] = {"record_id": task.record_id, "key": task.key}
        if task is winner:
            place |= {"role": "winner"} | ({"label": task.over} if task.over is not None else {})
        elif task.over is not None:
            place |= {"role": "left_out", "why": "over", "label": task.over}
        elif task.old and not winner.old:
            place |= {"role": "left_out", "why": "old"}
        else:
            place["role"] = "owner_page" if owning & set(task.nodes) else "mentions"
        members.append(place)
    flags = []
    if not clear:
        flags.append("owner_unclear")
    if all(task.updated is None for task in tasks):
        flags.append("no_date")
    if len(tasks) > BIG_GROUP:
        flags.append("big_group")
    notes = []
    if winner.old:
        notes.append("old_page")
    if owning and not any(owning & set(task.nodes) for task in tasks):
        notes.append("owner_has_no_page")
    status = DATE_PASSED if winner.over is not None else NEEDS_LOOK if flags else READY
    return {"id": card_id(winner.record_id, winner.key), "status": status, "flags": flags,
            "notes": notes, "owners": list(owned_by), "members": members}


def page_ahead(owners: Sequence[str], own: Task, its_owners: Sequence[str], its: Task) -> bool:
    """Whether a near-copy's winning page (`its`) is said in place of a card's own (`own`), by
    the two plain rules the join has for a winning page, asked across the two cards: a page that
    is not old is ahead of an old one; when they are alike in that, the own page of an office
    that handles both cards is ahead of a page that is no owner's. Not asked of a term that is
    over (a match marks those down itself), and the finer rules, the newest date and the most
    steps, are not asked across cards."""
    if own.over is not None or its.over is not None:
        return False
    if own.old != its.old:
        return own.old
    shared = set(owners) & set(its_owners)
    return bool(shared & set(its.nodes)) and not set(owners) & set(own.nodes)


def build(tasks: Sequence[Task], vectors: Any,
          names: Mapping[str, Sequence[str]]) -> list[dict[str, Any]]:
    """Every card of a release, from its tasks, their question vectors (one per task, in order)
    and each node's names. Cards come in the order of their winning tasks' pages; each names its
    near-copies by their ids (`near`), and those of them whose page is said in place of its own
    (`ahead`)."""
    named = handlers(names)
    grouped = groups(tasks, vectors)
    cards = [card([tasks[i] for i in group], named) for group in grouped]
    place = {(task.record_id, task.key): task for task in tasks}
    won = [place[(found["members"][0]["record_id"], found["members"][0]["key"])]
           for found in cards]
    for at, others in enumerate(near(tasks, vectors, grouped)):
        cards[at]["near"] = sorted(cards[other]["id"] for other in others)
        cards[at]["ahead"] = sorted(
            cards[other]["id"] for other in others
            if page_ahead(cards[at]["owners"], won[at], cards[other]["owners"], won[other]))
    return sorted(cards, key=lambda found: (found["members"][0]["record_id"],
                                            found["members"][0]["key"]))


def cards_file(dataset_version: str, identity_hash: str, built_on: date,
               cards: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """What is written beside a release (CARDS_SUFFIX): the release the cards were built from,
    the day (a term is over, a page is old, as of that day), the rules' settings and the cards.
    A card holds places, not facts: a task is named by its page's record id and its key, and what
    it says is read from the release."""
    return {"dataset_version": dataset_version, "identity_hash": identity_hash,
            "built_on": built_on.isoformat(),
            "rules": {"join_mean": JOIN_MEAN, "join_least": JOIN_LEAST, "big_group": BIG_GROUP,
                      "near_mean": NEAR_MEAN, "near_least": NEAR_LEAST},
            "cards": list(cards)}


def read_cards(found: Any, dataset_version: str, identity_hash: str) -> dict[str, Any] | None:
    """A cards file's content when it is one built for this release; else None."""
    if (not isinstance(found, dict) or not isinstance(found.get("cards"), list)
            or (found.get("dataset_version"), found.get("identity_hash"))
            != (dataset_version, identity_hash)):
        return None
    return found
