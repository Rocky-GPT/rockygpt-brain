"""What the bot says from one answer card, written by code.

An answer card is one task a student asks about, joined from every page that lists it
(retrieval/card_join.py), read in full by retrieval/answer_cards.view. Its answer is the task's
name; who it is for and its term, as the winning page gives them; that page's steps in the page
read's own words and order, each citing the page, with a link the page gives beside a step that
names none; the term or date that has passed, when the card is for one; and the deadlines its
left-out pages name that are over. Nothing else: no step of another page, no reworded sentence.
The engine adds the contacts of the offices that handle it, from the directory. No model call
writes any of it (Dan 11-03: boring is beneficial; 11-10: do the thinking at build time).

Code does not answer from every card (`refusal`): a card the rules flagged for a look, one whose
date passed after the cards were built, one with no step or no page address, and a list too long
to say are left to the path that reads the pages.

A chat reply is a card's answer only when the question clearly matches that one card (`best_card`,
`clear`), and a page answer then keeps the cards' choice of page (`read_winners`); both are behind
the engine's card setting, off by default (engine._card_answer, engine._read_winners).
"""

import re
from collections.abc import Collection, Mapping, Sequence
from datetime import date
from typing import Any

from rockygpt_brain.plain_writer import literal
from rockygpt_brain.retrieval.entity_facts import MEANING_PAST_TERM

MAX_STEP_LINKS = 2  # At most this many of a step's page links are said beside it.
# A task with more steps than this is a table read as steps (a year's fees: 25 to 44 lines), not
# something to do: the path that reads the page says the part that was asked. Of 131 real tasks
# read on 11-11, 124 had 6 steps or fewer and the longest that was a procedure had 8.
MAX_STEPS = 12
_URL = re.compile(r"https?://[^\s()\[\]<>\"']+")


# When one card clearly matches a question: its best vector scores MATCH_TOP or more, and it
# leads every other card by MATCH_MARGIN or more. Measured 2026-11-11 on dev release
# v2-20261111174533 (6,459 cards), never on the blind set:
# - 90 practice questions (70 how-to, 20 look-alikes such as "when is spring break"): the rule
#   fires on 15 of the how-to questions, every one on a card that answers it, and on none of the
#   look-alikes. At a margin of 0.04 it fires on 24, and then also answers "how do I make an
#   appointment with my advisor" from a new-student orientation page and "when is the last day
#   to withdraw", a calendar question, from one term's card.
# - 3,039 of the questions the build wrote for the tasks, each asked with its own vector taken
#   out: the rule fires for 22.5%, and for 75% of those on the question's own card. Of the 145
#   fires on another card that code can answer from, 49 were read by their words: about 30 are
#   the same task on another page or a better card for the question, about 10 a neighbour's
#   answer ("I'm on an F-1 visa, what form do I get" answered from the J-1 change-of-address
#   card), and the rest questions that only make sense on their own page ("this scholarship").
#   With the question's whole card taken out, so no right card exists, it still fires for 8.4%.
# Below 0.80 the best card is often narrower than the question ("take a leave of absence"
# matched only the medical leave, at 0.79).
MATCH_TOP = 0.80
MATCH_MARGIN = 0.08


Key = tuple[str, str]  # A page record's id and a task's key on it: a how-to unit of the index.


def tasks_of(cards: Sequence[Mapping[str, Any]], known: Collection[Key],
             ) -> tuple[dict[Key, int], frozenset[int], dict[Key, tuple[Key, str]]]:
    """What a release's cards say of its tasks: each task's card (its place in `cards`); the
    cards whose date has passed; and, for each task a card leaves out, the card's winning task
    and why it was left out ("over": its term or date is; "old": its page is, beside a newer
    one). Only tasks the meaning index holds (`known`) are named. A card whose own date has
    passed has no current page to read in place of a term that is over: its winning task is the
    newest of the passed ones (Spring 2026's refund dates for Spring 2022's)."""
    card_of: dict[Key, int] = {}
    left_out: dict[Key, tuple[Key, str]] = {}
    passed = frozenset(at for at, card in enumerate(cards) if card.get("status") == "date_passed")
    for at, card in enumerate(cards):
        keys = [(str(m.get("record_id")), str(m.get("key"))) for m in card.get("members") or []]
        for member, key in zip(card.get("members") or [], keys, strict=True):
            if key not in known:
                continue
            card_of[key] = at
            why = member.get("why")
            if (member.get("role") == "left_out" and keys[0] in known
                    and (why == "old" or (why == "over" and at not in passed))):
                left_out[key] = (keys[0], str(why))
    return card_of, passed, left_out


def best_card(scores: Mapping[Key, float], card_of: Mapping[Key, int],
              passed: Collection[int] = ()) -> tuple[int, float, float] | None:
    """The card a question's unit scores (MeaningIndex.scores) match best, its score and its lead
    over the next card; None when no unit is a card's. A card's score is its best task's. A card
    whose date has passed (`passed`) is marked down as a how-to read marks down a term that is
    over, so today's card leads last year's ("change my meal plan" matched Fall 2024's card at
    0.93 and Spring 2027's at 0.87)."""
    best: dict[int, float] = {}
    for key, score in scores.items():
        at = card_of.get(key)
        if at is None:
            continue
        if at in passed:
            score -= MEANING_PAST_TERM
        if score > best.get(at, -2.0):
            best[at] = score
    if not best:
        return None
    first = max(best, key=best.__getitem__)
    top = best.pop(first)
    return first, top, top - max(best.values(), default=-1.0)


def clear(top: float, lead: float) -> bool:
    """Whether a best card's score and lead make it the one card the question clearly matches."""
    return top >= MATCH_TOP and lead >= MATCH_MARGIN


def read_winners(order: Sequence[Key], left_out: Mapping[Key, tuple[Key, str]],
                 given: Collection[Key] = ()) -> tuple[list[Key], dict[Key, Key]]:
    """The sections a page answer reads, in order, with the cards' choice of page kept: a task
    its card left out for an old page gives its place to the card's winning task, and one left
    out for a term or date that is over stays (it says what has passed) with the winning task
    read before it. A winning task already read (`given`) is not read again. Also each winning
    task that was moved up or brought in, by the left-out task it is read for."""
    final: list[Key] = []
    read_for: dict[Key, Key] = {}
    for key in order:
        winner, why = left_out.get(key, (None, None))
        if winner is not None and winner not in final and winner not in given:
            final.append(winner)
            read_for[winner] = key
        if why != "old" and key not in final:
            final.append(key)
    return final, read_for


def refusal(card: Mapping[str, Any]) -> str | None:
    """Why code does not answer from a card, or None when it does: "needs_look" (the rules could
    not settle its owner or its date), "date_changed" (its term or date passed, or stopped being
    past, after the cards were built), "no_steps", "too_long", "no_page"."""
    if card.get("status") not in ("ready", "date_passed"):
        return "needs_look"
    if (card.get("over_now") or None) != (card.get("label") or None):
        return "date_changed"
    steps = [step for step in (card.get("task") or {}).get("steps") or []
             if str(step.get("text") or "").strip()]
    if not steps:
        return "no_steps"
    if len(steps) > MAX_STEPS:
        return "too_long"
    if not (card.get("page") or {}).get("url"):
        return "no_page"
    return None


def _plain(text: Any) -> str:
    """Page words as words: nothing in them reads as formatting, and a web address stays as the
    page wrote it (an escaped address is not the address the facts hold)."""
    value = " ".join(str(text or "").split())
    kept, at = [], 0
    for found in _URL.finditer(value):
        kept += [literal(value[at:found.start()]), found.group(0)]
        at = found.end()
    return "".join([*kept, literal(value[at:])])


def _sentence(text: str) -> str:
    return text if text[-1:] in ".!?:" else f"{text}."


def _trimmed(url: str) -> str:
    return url.rstrip(".,;:").rstrip("/")


def _step_links(step: Mapping[str, Any], said: Sequence[str]) -> list[str]:
    """The web addresses a step's page words give that no step and no source says: "Register
    using the registration link" is no help without the link the page put there."""
    given = {_trimmed(url) for text in said for url in _URL.findall(text)}
    links: list[str] = []
    for url in _URL.findall(str(step.get("quote") or "")):
        url = url.rstrip(".,;:")
        if _trimmed(url) not in given:
            given.add(_trimmed(url))
            links.append(url)
    return links[:MAX_STEP_LINKS]


def _day(iso: Any) -> str | None:
    try:
        found = date.fromisoformat(str(iso))
    except ValueError:
        return None
    return f"{found:%B} {found.day}, {found.year}"


def say(card: Mapping[str, Any], numbered_from: int = 0) -> tuple[str, list[dict[str, Any]]]:
    """A card's answer and the sources its marks stand for, numbered after `numbered_from`: the
    winning page first, then the page of each passed deadline it names. Call only for a card
    `refusal` gives no reason against."""
    task, page = card["task"], card["page"]
    n = numbered_from + 1
    sources = [{"n": n, "url": page["url"], "title": str(task["task"])}]
    blocks = [f"**{_plain(task['task'])}**"]
    label = card.get("label")
    if label:
        blocks.append(f"This is for {_plain(label)}, which has passed. The page said:")
    elif task.get("term"):
        blocks.append(f"Term: {_plain(task['term']).rstrip('.')}.")
    if task.get("applies_to"):
        blocks.append(f"Who it is for: {_plain(task['applies_to']).rstrip('.')}.")
    steps = [step for step in task["steps"] if str(step.get("text") or "").strip()]
    said = [str(step["text"]) for step in steps] + [str(page["url"])]
    lines = []
    for at, step in enumerate(steps, start=1):
        line = _sentence(_plain(step["text"]))
        if links := _step_links(step, said):
            line += " Link: " + " ".join(links)
            said += links
        lines.append(f"{at}. {line} [{n}]" if len(steps) > 1 else f"{line} [{n}]")
    blocks.append("\n".join(lines))
    if "old_page" in (card.get("notes") or []) and (changed := _day(page.get("updated"))):
        blocks.append(f"This page last changed on {changed}.")
    for item in card.get("passed") or []:
        if not item.get("url"):
            continue
        sources.append({"n": numbered_from + len(sources) + 1, "url": item["url"],
                        "title": str(item["task"])})
        blocks.append(f"Already passed: {_plain(item['task']).rstrip('.')} "
                      f"({_plain(item['label'])}). [{sources[-1]['n']}]")
    return "\n\n".join(blocks), sources
