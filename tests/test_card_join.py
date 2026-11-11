"""Joining a release's how-to tasks into answer cards (retrieval/card_join.py)."""

from datetime import date

import numpy

from rockygpt_brain.retrieval import card_join
from rockygpt_brain.retrieval.card_join import Task

SAFETY, SAFETY_911 = "office:public-safety", "office:public-safety-emergency"
ORIENTATION, ACCOUNTS, REGISTRAR, ADVISING = (
    "office:orientation", "office:student-accounts", "office:registrar", "office:advising")
NAMES = {SAFETY: ["Public Safety (Non-Emergency)"], SAFETY_911: ["Public Safety (Emergency)"],
         ORIENTATION: ["Orientation Programs"], ACCOUNTS: ["Student Accounts", "Bursar"],
         REGISTRAR: ["Registrar"], ADVISING: ["Center for Student Success (Academic Advising)"]}
NAMED = card_join.handlers(NAMES)


def task(record: str, name: str, nodes: tuple[str, ...], *, key: str = "e0",
         **more: object) -> Task:
    return Task(record_id=record, key=key, name=name, nodes=nodes,
                url=f"https://www.ramapo.edu/{record}/", **more)  # type: ignore[arg-type]


def unit(*weights: float) -> list[float]:
    vector = numpy.array(weights, dtype=numpy.float32)
    return list(vector / numpy.linalg.norm(vector))


def test_office_names_are_one_key_however_a_page_writes_them() -> None:
    for written in ("Office of Student Accounts", "Student Accounts Office",
                    "the Student Accounts (OSA)", "Student Accounts"):
        assert card_join.office_key(written) == "student accounts"
    assert card_join.office_key("Registrar’s Office") == "registrar"
    assert card_join.office_key("Events & Conferences") == card_join.office_key(
        "Office of Events and Conferences")
    assert NAMED["public safety"] == (SAFETY, SAFETY_911)
    assert NAMED["bursar"] == (ACCOUNTS,)
    assert card_join.office_key("the College") == ""


def test_two_named_things_differ_and_one_left_unsaid_does_not() -> None:
    rotary = task("rotary", "Donate to the Rotary Memorial Endowed Scholarship", ())
    dwyer = task("dwyer", "Make a donation to the Marie and Francis J. Dwyer Memorial Scholarship",
                 ())
    assert card_join.names_differ(rotary, dwyer)
    fafsa = task("aid", "Complete the FAFSA", ())
    federal = task("faq", "Choose when to apply for federal aid", ())
    assert not card_join.names_differ(fafsa, federal)
    # A name the other task's page or audience carries is no difference.
    pine = task("reslife/pine-hall", "Get a meal plan if you live in Pine Hall", ())
    laurel = task("reslife/laurel-hall", "Get a meal plan", (), applies_to="Laurel Hall residents")
    assert not card_join.names_differ(pine, laurel)
    # Terms, months and the college are not names; "W.I.N." is WIN.
    assert not card_join.names_differ(task("a", "Get a Fall 2026 meal plan refund", ()),
                                      task("b", "Get a Spring 2025 meal plan refund", ()))
    assert not card_join.names_differ(task("a", "Request a W.I.N. Peer Educator for an event", ()),
                                      task("b", "Request outreach from WIN Peer Educators", ()))


def test_groups_join_the_same_task_across_pages_only() -> None:
    tasks = [
        task("safety", "Apply for a parking permit", (SAFETY,)),
        task("safety", "Get a visitor parking permit", (SAFETY,), key="e1"),
        task("orientation", "Get a parking permit", (ORIENTATION,)),
        task("accounts", "Apply online for a parking permit", (ACCOUNTS,)),
        task("rotary", "Donate to the Rotary Memorial Endowed Scholarship", ()),
        task("dwyer", "Donate to the Marie Dwyer Memorial Scholarship", ()),
        task("registrar", "Request a transcript", (REGISTRAR,)),
    ]
    vectors = [unit(1, 0.05, 0, 0), unit(1, 0, 0, 0.5), unit(1, 0, 0.05, 0), unit(1, 0.3, 0.2, 0),
               unit(0, 0, 1, 0.02), unit(0, 0, 1, 0), unit(0, 0, 0, 1)]
    found = card_join.groups(tasks, vectors)
    # The visitor permit is alike enough, but shares the first task's page; the scholarships
    # are alike but each names its own; the transcript is like nothing else.
    assert found == [[0, 2, 3], [1], [4], [5], [6]]


def test_a_group_needs_every_pair_near_and_the_average_nearer() -> None:
    tasks = [task(f"page-{n}", "Apply for housing", ()) for n in range(3)]
    near, far = card_join.JOIN_MEAN + 0.02, card_join.JOIN_LEAST - 0.02

    def at(cosine: float) -> list[float]:
        return [cosine, float(numpy.sqrt(1 - cosine * cosine)), 0.0]

    # The second is near the first; the third is near neither.
    assert card_join.groups(tasks, [at(1.0), at(near), [far, 0.0, float(numpy.sqrt(1 - far ** 2))]]
                            ) == [[0, 1], [2]]
    assert card_join.groups([], numpy.zeros((0, 3))) == []
    assert card_join.groups(tasks[:1], [at(1.0)]) == [[0]]


def test_near_copies_pass_every_join_rule_but_the_average() -> None:
    tasks = [
        task("safety", "Apply for a parking permit", (SAFETY,)),
        task("rules", "Obtain and display a campus parking permit", (SAFETY,)),
        task("safety", "Get a visitor parking permit", (SAFETY,), key="e1"),
        task("rotary", "Donate to the Rotary Memorial Endowed Scholarship", ()),
        task("dwyer", "Donate to the Marie Dwyer Memorial Scholarship", ()),
        task("registrar", "Request a transcript", (REGISTRAR,)),
    ]
    # The second is 0.8 like the first: under the join's average, over a near-copy's. The third
    # is as like the first and shares its page. The scholarships are 0.8 alike and each names
    # its own.
    vectors = [unit(1, 0, 0, 0, 0, 0), unit(0.8, 0.6, 0, 0, 0, 0), unit(0.8, 0, 0.6, 0, 0, 0),
               unit(0, 0, 0, 1, 0, 0), unit(0, 0, 0, 0.8, 0.6, 0), unit(0, 0, 0, 0, 0, 1)]
    grouped = card_join.groups(tasks, vectors)
    assert grouped == [[0], [1], [2], [3], [4], [5]]
    assert card_join.near(tasks, vectors, grouped) == [[1], [0], [], [], [], []]
    assert card_join.near([], numpy.zeros((0, 3)), []) == []


def test_a_near_copy_is_near_every_task_of_the_group() -> None:
    tasks = [task(f"page-{n}", "Apply for housing", ()) for n in range(3)]
    # The first two join (0.95). The third is 0.88 like the first and 0.69 like the second:
    # 0.785 on average, with one pair under the least a near-copy may have.
    vectors = [[1.0, 0.0, 0.0], [0.95, 0.3122, 0.0], [0.88, -0.4677, 0.083]]
    grouped = card_join.groups(tasks, vectors)
    assert grouped == [[0, 1], [2]]
    assert card_join.near(tasks, vectors, grouped) == [[], []]
    # 0.74 like the second: every pair is near enough.
    vectors[2] = [0.88, -0.3076, 0.3616]
    assert card_join.near(tasks, vectors, card_join.groups(tasks, vectors)) == [[1], [0]]


def test_the_pages_own_word_names_the_owner() -> None:
    listed = [
        task("orientation", "Get a parking permit", (ORIENTATION,), handled_by="Public Safety"),
        task("orientation-2", "Get a parking permit", (ORIENTATION,)),
        task("accounts", "Apply for a parking permit", (ACCOUNTS,)),
        task("safety", "Apply for a parking permit", (SAFETY, SAFETY_911)),
    ]
    assert card_join.owners(listed, NAMED) == ((SAFETY, SAFETY_911), True)
    # No page names one: the office whose pages list it most.
    assert card_join.owners(listed[1:3] + [listed[1]], NAMED) == ((ORIENTATION,), True)
    # Two offices, one page each: the rules cannot settle it.
    assert card_join.owners(listed[1:3], NAMED) == ((ORIENTATION, ACCOUNTS), False)
    # A name no node carries is no owner; a past term has no say beside a current one.
    assert card_join.owners([
        task("registrar", "Request a transcript", (REGISTRAR,),
             handled_by="National Student Clearinghouse"),
        task("advising", "Request a transcript", (ADVISING,), over="Fall 2022"),
        task("advising-2", "Request a transcript", (ADVISING,), over="Fall 2023"),
    ], NAMED) == ((REGISTRAR,), True)
    assert card_join.owners([task("nowhere", "Do a thing", ())], NAMED) == ((), False)


def test_a_card_ranks_its_pages_and_says_why_each_is_not_said() -> None:
    faq = task("orientation/faq", "Get a parking permit", (ORIENTATION,), steps=15,
               updated=date(2022, 7, 14), old=True)
    faq_new = task("orientation/faq-2", "Apply for and obtain a parking permit", (ORIENTATION,),
                   steps=13, updated=date(2026, 9, 28), handled_by="Public Safety")
    how_to = task("publicsafety/how-to-apply", "Apply for a parking permit", (SAFETY, SAFETY_911),
                  steps=3, updated=date(2026, 8, 17))
    permits = task("publicsafety/parking-permits", "Apply for a parking permit",
                   (SAFETY, SAFETY_911), steps=1, updated=date(2026, 8, 17))
    checklist = task("orientation/checklist", "Get a current parking permit", (ORIENTATION,),
                     updated=date(2026, 9, 9), over="Fall 2022")
    found = card_join.card([faq, checklist, permits, faq_new, how_to], NAMED)
    assert found["owners"] == [SAFETY, SAFETY_911]
    assert found["status"] == card_join.READY and found["flags"] == [] and found["notes"] == []
    assert found["id"] == card_join.card_id("publicsafety/how-to-apply", "e0")
    places = [(m["record_id"], m["role"], m.get("why"), m.get("label")) for m in found["members"]]
    assert places == [
        ("publicsafety/how-to-apply", "winner", None, None),  # the owner's, and more steps
        ("publicsafety/parking-permits", "owner_page", None, None),
        ("orientation/faq-2", "mentions", None, None),
        ("orientation/faq", "left_out", "old", None),
        ("orientation/checklist", "left_out", "over", "Fall 2022"),
    ]


def test_a_card_for_a_term_that_is_over_keeps_its_latest_term() -> None:
    refunds = [
        task("previous-2024", "Get a Fall 2024 meal plan refund", (ACCOUNTS,),
             updated=date(2025, 1, 29), over="Fall 2024"),
        task("previous", "Get a Fall 2026 meal plan refund", (ACCOUNTS,),
             updated=date(2026, 11, 2), over="September 1, 2026"),
        task("previous-2025", "Get a Fall 2025 meal plan refund", (ACCOUNTS,),
             updated=date(2026, 2, 5), over="Fall 2025"),
        task("costs", "Know meal plan refunds", (ACCOUNTS,), updated=date(2026, 6, 1),
             over="2025-2026"),
    ]
    found = card_join.card(refunds, NAMED)
    assert found["status"] == card_join.DATE_PASSED
    # The winner says which date passed.
    assert [(m["record_id"], m.get("label")) for m in found["members"]] == [
        ("previous", "September 1, 2026"), ("costs", "2025-2026"), ("previous-2025", "Fall 2025"),
        ("previous-2024", "Fall 2024")]
    # A current page wins over every past term, whoever owns it.
    current = task("deadlines", "Get a Spring 2027 meal plan refund", (ORIENTATION,))
    assert card_join.card([*refunds, current], NAMED)["members"][0]["record_id"] == "deadlines"


def test_what_the_rules_cannot_settle_is_flagged() -> None:
    registrar = task("registrar", "Declare a major", (REGISTRAR,), updated=date(2021, 9, 13),
                     old=True)
    advising = task("advising", "Plan when to declare your major", (ADVISING,),
                    updated=date(2026, 4, 20))
    accounts = task("accounts", "Declare your major", (ACCOUNTS,), updated=date(2026, 1, 9))
    tied = card_join.card([accounts, advising], NAMED)
    assert tied["status"] == card_join.NEEDS_LOOK and tied["flags"] == ["owner_unclear"]
    assert tied["members"][0]["record_id"] == "advising"  # the newest date, for want of an owner
    # An old page has no say beside a newer one: its office does not tie the current page's.
    settled = card_join.card([registrar, advising], NAMED)
    assert settled["status"] == card_join.READY and settled["owners"] == [ADVISING]
    assert settled["members"][1] == {"record_id": "registrar", "key": "e0", "role": "left_out",
                                     "why": "old"}
    assert card_join.card([registrar], NAMED)["owners"] == [REGISTRAR]  # alone, it still owns
    undated = card_join.card([task("registrar", "Request a transcript", (REGISTRAR,))], NAMED)
    assert undated["flags"] == ["no_date"] and undated["status"] == card_join.NEEDS_LOOK
    many = [task(f"page-{n}", "Apply", (REGISTRAR,), updated=date(2026, 1, 1))
            for n in range(card_join.BIG_GROUP + 1)]
    assert card_join.card(many, NAMED)["flags"] == ["big_group"]
    # An old page that still wins is noted, not flagged; so is an owner with no page of its own.
    alone = card_join.card([task("orientation", "Get a permit", (ORIENTATION,), old=True,
                                 updated=date(2020, 1, 1), handled_by="Public Safety")], NAMED)
    assert alone["status"] == card_join.READY
    assert alone["notes"] == ["old_page", "owner_has_no_page"]


def test_build_gives_every_task_one_card_in_page_order() -> None:
    tasks = [
        task("b-accounts", "Apply online for a parking permit", (ACCOUNTS,),
             updated=date(2026, 6, 30), handled_by="Public Safety"),
        task("a-safety", "Apply for a parking permit", (SAFETY, SAFETY_911),
             updated=date(2026, 8, 17)),
        task("c-registrar", "Request a transcript", (REGISTRAR,), updated=date(2026, 9, 1)),
    ]
    cards = card_join.build(tasks, [unit(1, 0.1, 0), unit(1, 0, 0), unit(0, 0, 1)], NAMES)
    assert [[(m["record_id"], m["role"]) for m in found["members"]] for found in cards] == [
        [("a-safety", "winner"), ("b-accounts", "mentions")], [("c-registrar", "winner")]]
    assert all(found["status"] == card_join.READY for found in cards)
    assert sum(len(found["members"]) for found in cards) == len(tasks)
    assert [found["near"] for found in cards] == [[], []]
    # Two cards the join keeps apart for the average alone name each other.
    apart = card_join.build(tasks[1:], [unit(1, 0, 0), unit(0, 0, 1)], NAMES)
    assert [found["near"] for found in apart] == [[], []]
    copies = card_join.build(
        [tasks[1], task("d-rules", "Obtain and display a campus parking permit", (SAFETY,))],
        [unit(1, 0, 0), unit(0.8, 0.6, 0)], NAMES)
    assert [found["near"] for found in copies] == [[copies[1]["id"]], [copies[0]["id"]]]


def test_a_task_is_compared_by_the_questions_written_for_it() -> None:
    tasks = [task("asked", "Apply for a permit", ()), task("new", "Request a transcript", ()),
             task("absent", "Not in the index", ())]
    # Vectors in the index's order: the first unit's opening and its two questions, a section's
    # opening, then the second unit's opening (no questions written yet).
    meta = {"dimensions": 2, "units": [["asked", "e0", []], ["asked", "0", []], ["new", "e0", []]],
            "owner": [0, 0, 0, 1, 2], "offsets": [0, -1, -1, 0, 0]}
    flat = numpy.array([[0, 1], [1, 0], [0.6, 0.8], [0, 1], [0, 1]], dtype=numpy.float32)
    found = card_join.task_vectors(meta, flat.ravel(), tasks)
    mean = numpy.array([0.8, 0.4]) / numpy.linalg.norm([0.8, 0.4])
    assert numpy.allclose(found[0], mean) and numpy.allclose(found[1], [0, 1])
    assert not found[2].any()  # a task the index does not hold joins nothing


def test_a_cards_file_is_only_read_for_its_own_release() -> None:
    written = card_join.cards_file("release-1", "identities-1", date(2026, 11, 11), [{"id": "c1"}])
    assert written["built_on"] == "2026-11-11" and written["rules"]["join_mean"] == 0.85
    assert card_join.read_cards(written, "release-1", "identities-1") == written
    assert card_join.read_cards(written, "release-2", "identities-1") is None
    assert card_join.read_cards({"cards": "none"}, "release-1", "identities-1") is None
    assert card_join.read_cards([], "release-1", "identities-1") is None
