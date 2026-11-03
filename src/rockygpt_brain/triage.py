"""Read the message first: what kind of message it is, before anything is searched.

11-03 (a 60-message set written the way students really type, 24 of 60 good): every message that
was not a plain office, menu or shuttle lookup went to the college-page search, so "hello", "cha
cha cha", "i hate my professor" and "wen r finals" were all searched like how-to questions, and
the reply was written from the nearest page by words ("i hate my professor" got ENHANCE, a
program for autistic students; "i miss home so much" got study-abroad re-entry).

One small model call now reads the message before anything else: whether it is small talk,
nonsense, a feeling or a problem, a danger, something RockyGPT can't do, or a campus question,
and the campus question in plain English ("wen r finals" is "When are finals?"). The model only
understands the message. It never writes the reply and never supplies a campus fact: code writes
every reply that is not a campus answer (the fixed texts here, and the offices' contacts read
from the graph), and a campus question goes through the usual answer paths in its clean words.

Safety: the phrase floor (boundary.py) still runs first. The read can only add an emergency,
never take one away. A read that fails, runs late or comes back malformed leaves the turn
exactly as it was before this step existed.
"""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

SOCIAL = ("none", "greeting", "how_are_you", "thanks", "goodbye", "okay", "about_bot",
          "compliment", "complaint", "joke", "laugh", "nonsense", "upset")
DANGER = ("none", "self_harm", "medical", "danger", "fire", "other")
NEEDS = ("stress_or_sadness", "lonely", "school_trouble", "professor_trouble",
         "roommate_trouble", "health", "alcohol_or_drugs", "money", "thinking_of_leaving",
         "harassment", "food_or_housing", "disability")
LIMITS = ("none", "clock", "off_topic", "my_records", "homework", "writing", "do_it_for_me",
          "opinion", "prediction", "live_status")

# A message longer than this is a detailed question, not small talk: it goes on as it is.
MAX_READ_CHARS = 1_000
# The earlier reply the read is shown, only so "thanks" and "that's wrong" can be told apart.
PREVIOUS_REPLY_CHARS = 400
# A clean question is one question, not a rewrite of a long message.
MAX_QUESTION_CHARS = 300
READ_SECONDS = 8.0

READ_PROMPT = """You read one message a student sent to RockyGPT, Ramapo College's campus
assistant, and say what kind of message it is. You never answer it, and you know nothing about
the college. The student's previous message and RockyGPT's last reply are given only so you can
tell what the latest message is doing (a "thanks" after an answer); read the latest message.
Fill every field:
- social: what the message does when it is small talk: greeting (hi, hello, good morning, "anyone
  there?"), how_are_you, thanks, goodbye, okay (ok, got it, cool), about_bot (are you a bot or a
  person, who made you, what can you do, do you know my name), compliment, complaint (an insult,
  or "that's wrong" about RockyGPT's last reply), joke (asks for a joke), laugh (lol, haha, a
  laughing emoji), nonsense (keyboard mashing, a word or emoji repeated with no meaning, a lone
  "?"), upset (sounds upset or stressed in passing about a practical problem it asks how to fix:
  something lost, missed, broken or locked), else none. A hello before a question is still
  greeting. A message that names a campus office, place or service is never nonsense, however
  short or oddly typed.
- danger: someone may be in danger right now: self_harm (wanting to die, to hurt or kill
  themselves, in any wording or slang, such as kms or unalive), medical (someone unconscious,
  not breathing, overdosed, having a seizure, badly hurt), danger (a weapon, an attack, being
  followed or threatened now), fire (fire, smoke, gas), other (an emergency of unclear kind),
  else none. Figures of speech are none: "this class is killing me", "I'm dead lol".
- needs: the problems or feelings the student shares, only from the list: stress_or_sadness
  (stress, anxiety, sadness, anger, grief, a breakup, feeling overwhelmed), lonely (lonely,
  homesick, no friends, left out), school_trouble (failing or failed a class or exam, missed an
  exam, behind in class), professor_trouble, roommate_trouble, health (feeling sick, an injury,
  medicine, a medical question), alcohol_or_drugs, money (can't pay or afford something),
  thinking_of_leaving (dropping out, transferring, quitting school), harassment (harassment,
  discrimination, sexual misconduct), food_or_housing (no food, nowhere to live), disability
  (accommodations). A practical question with no feeling or problem in it has none, and so does a
  passing word of feeling about a practical problem (that is social upset). A question about a
  cost, a rule or a deadline is no need either.
- limit: what the message asks that RockyGPT cannot do: clock (the current time or date),
  off_topic (not about Ramapo or college life: sports scores, TV, dating advice, world news),
  my_records (their own grades, GPA, balance, schedule, aid status, who they are), homework (do
  or solve their homework, a quiz or a test), writing (write an essay, statement or letter for
  them), do_it_for_me (send a message, register, drop a class, pay or change something for them),
  opinion (which is best or better, what they should choose), prediction (will they get in, will
  something happen), live_status (weather, closings or cancellations today, how busy a place is,
  whether someone is in right now), else none. Whether a place is open on a day or at a time
  asks its published hours, which is no limit.
- question: the campus question in the message, in plain English with spelling and slang fixed,
  nothing added and nothing left out: "wen r finals" is "When are finals?". Keep the student's
  own names, places, days and numbers, and never add one. Keep "it", "that" and "they" as they
  are. When a limit has a campus side, write that instead: homework is where to get tutoring in
  that subject; writing is where to get help writing it; my_records is how to check it;
  do_it_for_me is how to do it themselves; opinion is what the college publishes about the
  choices named; prediction is what the requirement is; live_status is how Ramapo announces
  closings, when closings are asked. Homework and writing always have a campus side. Leave it
  empty when the message asks nothing about Ramapo: small talk, nonsense, off_topic, the
  weather, an opinion with no campus side, a feeling the student only shares, and a medical
  question (what to take, whether something is safe), which is no campus question.
Answer only with the read_message tool."""

READ_TOOL: dict[str, Any] = {
    "type": "function", "name": "read_message", "strict": True,
    "description": "What kind of message the student's latest message is.",
    "parameters": {
        "type": "object", "additionalProperties": False,
        "required": ["social", "danger", "needs", "limit", "question"],
        "properties": {
            "social": {"type": "string", "enum": list(SOCIAL)},
            "danger": {"type": "string", "enum": list(DANGER)},
            "needs": {"type": "array", "items": {"type": "string", "enum": list(NEEDS)}},
            "limit": {"type": "string", "enum": list(LIMITS)},
            "question": {"type": "string"},
        },
    },
}


@dataclass(frozen=True, slots=True)
class Reading:
    """What the read found. Every field is checked against its list; anything else is dropped."""

    social: str = "none"
    danger: str = "none"
    needs: tuple[str, ...] = ()
    limit: str = "none"
    question: str = ""

    def as_trace(self) -> dict[str, Any]:
        return {"social": self.social, "danger": self.danger, "needs": list(self.needs),
                "limit": self.limit, "question": self.question}


def read_messages(latest: str, previous_user: str | None,
                  previous_reply: str | None) -> list[dict[str, Any]]:
    """The read's whole input: the prompt, then the latest message and the exchange before it."""
    given: dict[str, Any] = {"latest_message": latest}
    if previous_user:
        given["previous_student_message"] = previous_user[:MAX_READ_CHARS]
    if previous_reply:
        given["previous_reply"] = previous_reply[:PREVIOUS_REPLY_CHARS]
    return [{"role": "system", "content": READ_PROMPT},
            {"role": "user", "content": json.dumps(given, ensure_ascii=False)}]


def _digits(text: str) -> set[str]:
    return set(re.findall(r"\d+", text))


def _plain(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def reading_of(arguments: Any, latest: str) -> Reading | None:
    """The read's tool arguments as a Reading, or None when they are not the shape asked for.

    The clean question is dropped (the student's own words are used instead) when it adds a
    number the message does not hold, or runs long: it may only fix the words, never add a fact.
    """
    if not isinstance(arguments, dict):
        return None
    social, danger, limit = (arguments.get(k) for k in ("social", "danger", "limit"))
    needs, question = arguments.get("needs"), arguments.get("question")
    if social not in SOCIAL or danger not in DANGER or limit not in LIMITS:
        return None
    if not isinstance(needs, list) or not isinstance(question, str):
        return None
    kept = tuple(dict.fromkeys(n for n in needs if n in NEEDS))
    question = " ".join(question.split())
    if (len(question) > MAX_QUESTION_CHARS or not _digits(question) <= _digits(latest)
            or not re.search(r"[A-Za-z]", question)):
        question = ""
    elif _plain(question) == _plain(latest):
        question = latest.strip()  # Nothing to fix: the student's own words, as they wrote them.
    return Reading(str(social), str(danger), kept, str(limit), question)


# What code says for each kind of message that is not a campus question. None holds a campus fact
# (the graph-only rule): any office, phone or date in a reply comes from the graph.
CAN_HELP = ("office hours and contacts, dining menus, the shuttle, events and clubs, programs and "
            "professors, and the academic calendar")
SOCIAL_TEXTS = {
    "greeting": f"Hi! I'm RockyGPT, Ramapo's campus assistant. I can help with {CAN_HELP}. "
                "What do you need?",
    "how_are_you": "Hi! I'm doing well, thanks for asking. I'm RockyGPT, Ramapo's campus "
                   f"assistant. I can help with {CAN_HELP}. What do you need?",
    "thanks": "You're welcome! Let me know if there's anything else I can help with.",
    "goodbye": "Bye! Come back anytime you have a question about Ramapo.",
    "okay": "Okay! Let me know if there's anything else I can help with.",
    "about_bot": ("I'm RockyGPT, an AI assistant, not a real person. I answer questions about "
                  "Ramapo College from the college's own published information, and I can make "
                  "mistakes, so check anything important with the office. I can't see who you "
                  "are or your personal student records."),
    "compliment": "Aw, thank you! I'm just an AI, but I'm glad I can help. What else can I do "
                  "for you?",
    "complaint": ("Sorry about that! I'm an AI and I can get things wrong. Could you tell me what "
                  "you're looking for in a different way? I'll try again."),
    "laugh": "Ha! Glad something made you laugh. What can I help you with?",
    "nonsense": (f"I'm not sure what you mean. I can help with Ramapo things like {CAN_HELP}. "
                 "What would you like to know?"),
    "upset": "Sorry to hear that! If there's something at Ramapo I can help you find, just ask.",
}
# Before a campus answer, only a short word back.
SOCIAL_LEADS = {"greeting": "Hi!", "how_are_you": "Hi! I'm doing well, thanks for asking.",
                "thanks": "You're welcome!", "upset": "Sorry to hear that!"}
JOKES = (
    "Why did the student bring a ladder to class? They heard the course was on another level.",
    "Why was the math book sad? It had too many problems.",
    "What do you call a student who's always on time for an 8 a.m.? A legend.",
)
JOKE_AFTER = "What can I help you with at Ramapo?"

LIMIT_TEXTS = {
    "off_topic": (f"I only know about Ramapo College, so I can't help with that one. I can help "
                  f"with {CAN_HELP}."),
    "my_records": "I can't see your personal records, like your grades, balance, schedule or "
                  "financial aid status.",
    "homework": "I can't do homework, quizzes or tests for you, but I can help you find help on "
                "campus.",
    "writing": "I can't write essays, statements or letters for you, but I can help you find "
               "help on campus.",
    "do_it_for_me": "I can't send messages or make changes for you, like registering, dropping a "
                    "class or making a payment.",
    "opinion": "I can't choose for you, but I can share what the college publishes.",
    "prediction": "I can't predict that.",
    "live_status": "I can't see live updates, like the weather, closings or how busy a place is "
                   "right now.",
}
# A limit with nothing else asked: where the student can find out, or what RockyGPT can do
# instead. Every reply that says no also says what it can do (11-03 practice: "I can't predict
# that." alone read as a dead end).
LIMIT_ALONE = {
    "my_records": " Your student account, or the office that handles it, can tell you.",
    "do_it_for_me": " Please use your own student account, or ask the office that handles it.",
    "opinion": " Tell me what you'd like to compare, and I'll share what I can find.",
    "prediction": f" I can help with {CAN_HELP}.",
    "live_status": f" I can help with {CAN_HELP}.",
}
# A limit whose campus side is the same whatever was asked: homework always has tutoring, and
# writing always has writing help. When the read leaves the question empty ("solve this: ..."
# names no subject), this is the campus question asked in its place.
LIMIT_QUESTIONS = {
    "homework": "Where can I get tutoring?",
    "writing": "Where can I get help with my writing?",
}
OFF_TOPIC_BESIDE = "I can't help with that part directly, but here's what Ramapo offers:"

# What a feeling or a problem gets: one kind sentence, then the offices whose job it is, whose
# contacts are read from the graph. The offices are reviewed published names, like the emergency
# reply's (engine.HELP_OFFICES); a name the release does not publish is skipped, never guessed.
SUPPORT_OFFICES: dict[str, tuple[str, ...]] = {
    "stress_or_sadness": ("Counseling Center",),
    "lonely": ("Counseling Center", "Center for Student Involvement"),
    "school_trouble": ("Center for Student Success (Academic Advising)",),
    "professor_trouble": ("Ombuds Office", "Center for Student Success (Academic Advising)"),
    "roommate_trouble": ("Housing & Residence Life", "Ombuds Office"),
    "health": ("Health Services",),
    "alcohol_or_drugs": ("Substance Use Disorder Services", "Counseling Center"),
    "money": ("Financial Aid", "Student Accounts"),
    "thinking_of_leaving": ("Center for Student Success (Academic Advising)",
                            "Counseling Center", "Financial Aid"),
    "harassment": ("Title IX", "Ombuds Office"),
    "food_or_housing": ("Civic and Community Engagement Center",),
    "disability": ("Office of Specialized Services",),
}
SUPPORT_TEXTS = {
    "stress_or_sadness": ("I'm sorry you're going through that. It's okay to not be okay, and you "
                          "don't have to handle it alone."),
    "lonely": ("That sounds really hard, and a lot of students feel this way, especially at first. "
               "You're not the only one."),
    "school_trouble": ("That's stressful, but one bad grade doesn't decide everything. Talking "
                       "with your professor and an advisor soon can really help."),
    "professor_trouble": ("That sounds frustrating. If you can, try talking with your professor "
                          "first. If that doesn't help, there are people who can help you figure "
                          "out next steps."),
    "roommate_trouble": ("Roommate problems are really common, and they can be worked out. Your "
                         "RA is a good first step."),
    "health": ("I can't give medical advice, so please ask a doctor or pharmacist. If it's an "
               "emergency, call 911."),
    "alcohol_or_drugs": "Thanks for being honest about that. You don't have to figure it out "
                        "alone.",
    "money": "Money stress is really common, and you don't have to sort it out alone.",
    "thinking_of_leaving": ("That's a big decision, and it's worth talking it through with someone "
                            "before you decide."),
    "harassment": ("I'm sorry that happened. You deserve to feel safe. If you're in danger right "
                   "now, call 911."),
    "food_or_housing": "I'm sorry you're dealing with that. You're not alone, and you can ask for "
                       "help.",
    "disability": "You can ask for support with that.",
}
SUPPORT_LEAD = "These offices can help:"
MAX_SUPPORT_OFFICES = 3


def support_offices(needs: Sequence[str]) -> list[str]:
    """The offices for the needs read, in order, each once, at most MAX_SUPPORT_OFFICES."""
    names = dict.fromkeys(name for need in needs for name in SUPPORT_OFFICES.get(need, ()))
    return list(names)[:MAX_SUPPORT_OFFICES]


def joke(message: str) -> str:
    """One joke from the short fixed list, the same one for the same message."""
    return JOKES[sum(map(ord, message)) % len(JOKES)]
