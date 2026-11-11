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

Who handles it (`owners`): the office the pages say handles it, most named first; when no page
names one, the office whose pages list it most. A term that is over has no say.

Which page wins (`build`): never a past term while a current page exists, then the owner's own
page, then the newest date, then the most steps.
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
    found: set[tuple[float, int, int]] = set()
    reach = min(NEAREST + 1, count)
    for start in range(0, count, CHUNK):
        alike = matrix[start:start + CHUNK] @ matrix.T
        for offset, row in enumerate(alike):
            i = start + offset
            for j in numpy.argpartition(-row, reach - 1)[:reach].tolist():
                if j != i and row[j] >= JOIN_LEAST and tasks[i].record_id != tasks[j].record_id:
                    found.add((round(float(row[j]), 4), min(i, j), max(i, j)))
    group_of = list(range(count))
    members: dict[int, list[int]] = {i: [i] for i in range(count)}
    pages: dict[int, set[str]] = {i: {tasks[i].record_id} for i in range(count)}
    for _, i, j in sorted(found, key=lambda pair: (-pair[0], pair[1], pair[2])):
        a, b = sorted((group_of[i], group_of[j]))
        if a == b or pages[a] & pages[b]:
            continue
        alike = matrix[members[a]] @ matrix[members[b]].T
        if alike.mean() < JOIN_MEAN or alike.min() < JOIN_LEAST:
            continue
        if any(named[x] - context[y] and named[y] - context[x]
               for x in members[a] for y in members[b]):
            continue
        for k in members[b]:
            group_of[k] = a
        members[a] += members.pop(b)
        pages[a] |= pages.pop(b)
    return [sorted(group) for _, group in sorted(members.items())]


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


def build(tasks: Sequence[Task], vectors: Any,
          names: Mapping[str, Sequence[str]]) -> list[dict[str, Any]]:
    """Every card of a release, from its tasks, their question vectors (one per task, in order)
    and each node's names. Cards come in the order of their winning tasks' pages."""
    named = handlers(names)
    cards = [card([tasks[i] for i in group], named) for group in groups(tasks, vectors)]
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
            "rules": {"join_mean": JOIN_MEAN, "join_least": JOIN_LEAST, "big_group": BIG_GROUP},
            "cards": list(cards)}


def read_cards(found: Any, dataset_version: str, identity_hash: str) -> dict[str, Any] | None:
    """A cards file's content when it is one built for this release; else None."""
    if (not isinstance(found, dict) or not isinstance(found.get("cards"), list)
            or (found.get("dataset_version"), found.get("identity_hash"))
            != (dataset_version, identity_hash)):
        return None
    return found
