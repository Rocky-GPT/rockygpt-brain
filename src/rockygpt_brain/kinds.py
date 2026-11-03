"""Kinds of campus events and clubs: art and crafts, music, cultural, sports, volunteering.

A student names a kind ("any art events", "cultural clubs"); a listing names what happens
("Ceramics and Tea", "Fufu with Afro", "Mask Decorating"). No word is shared, and a whole kind is
too many cards for meaning alone to list (each is only somewhat near the question, so none stands
out). The calendar's own tags are too coarse to say it (44 of 114 events in two weeks were
"Social/Entertainment", 11-03; none was tagged art).

So each event and club card is given its kinds once, when the meaning index is built
(scripts/build_meaning_index.py): the chat model reads the card and picks from this fixed list, and
the picks are cached by the card's text, so a release only pays for new listings. At answer time
code reads a question's kind from the words students call that kind (`named`), never from a
member of it: "art" and "crafts" name art and crafts, "painting" and "ceramics" do not, so "any
painting events" still asks for painting. Kinds only find events and clubs; what a reply says
about them still comes from the release's own records.
"""

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class Kind:
    key: str
    # How a reply says it, before "events" or "clubs".
    name: str
    # What belongs, for the model that gives each card its kinds.
    holds: str
    # The words and phrases students call the kind itself (each a whole word or phrase).
    named: tuple[str, ...]
    # Narrower kinds a student asking for this one also wants listed: a study abroad fair is an
    # international event, a tutoring session an academic one. The model gives each card the
    # kinds that fit it, and a kind asked lists these too (with_included).
    includes: tuple[str, ...] = ()


# Words that name a stage performance of any kind ("any shows this weekend").
_SHOWS = ("show", "shows", "performance", "performances", "perform", "performing", "performer",
          "performers", "performing arts")

KINDS: tuple[Kind, ...] = (
    Kind("arts", "art and crafts",
         "making or viewing visual art: painting, drawing, crafts and DIY, decorating, ceramics, "
         "photography, design, animation and digital art, art exhibits and galleries",
         ("art", "arts", "artsy", "artistic", "artwork", "artworks", "craft", "crafts",
          "crafting", "crafty", "creative", "diy", "arts and crafts")),
    Kind("music", "music",
         "concerts, recitals, live bands, choirs, a cappella, open mics for music, DJs, jam "
         "sessions and music lessons",
         ("music", "musical", "musicals", "musician", "musicians", "concert", "concerts",
          "live music", *_SHOWS)),
    Kind("theater", "theater and stage",
         "plays, musicals, drag shows, comedy, improv, magic, spoken word and talent shows, and "
         "their auditions and rehearsals",
         ("theater", "theaters", "theatre", "theatres", "theatrical", "drama", "musical",
          "musicals", "improv", *_SHOWS)),
    Kind("dance", "dance",
         "dance performances, classes and practices, dance teams, dance parties and socials",
         ("dance", "dances", "dancing", "dancer", "dancers", "performing arts")),
    Kind("film", "movie and film",
         "movie nights, film screenings, film festivals and talks about film",
         ("movie", "movies", "film", "films", "cinema", "screening", "screenings")),
    Kind("culture", "cultural and heritage",
         "celebrating or learning about a culture, ethnicity, nationality or heritage: heritage "
         "months and weeks, cultural holidays, cultural food, music or dance nights, and the "
         "events and meetings of cultural student groups",
         ("culture", "cultures", "cultural", "multicultural", "multiculturalism", "heritage")),
    Kind("international", "international",
         "international students and exchange students, global issues and world affairs, "
         "International Education Week",
         ("international", "global"), includes=("abroad",)),
    Kind("abroad", "study abroad",
         "studying in another country: study abroad programs and exchanges, their info sessions "
         "and fairs, and courses or trips abroad. A trip within the United States or its "
         "territories is not abroad",
         ("abroad", "overseas", "study abroad")),
    Kind("athletics", "sports",
         "physical sports to watch or play: Ramapo Roadrunners varsity games, club sports teams "
         "and their practices, intramural competitions. Not esports or video games (those are "
         "games), and not a party to watch a professional game on a screen",
         ("sport", "sports", "athletic", "athletics", "game", "games", "match", "matches",
          "varsity", "home game", "home games")),
    Kind("fitness", "fitness and recreation",
         "workouts, group exercise, yoga, the gym, intramurals and rec sports to play, hikes and "
         "outdoor recreation",
         ("fitness", "workout", "workouts", "exercise", "exercising", "gym", "recreation",
          "recreational", "rec", "intramural", "intramurals")),
    Kind("wellness", "health and wellness",
         "mental health, stress relief, mindfulness and meditation, self-care, therapy animals, "
         "sleep, nutrition, sexual health, substance use, health screenings and clinics",
         ("wellness", "wellbeing", "well being", "health", "healthy", "mental health",
          "self care", "selfcare", "stress", "destress", "de stress", "stress relief", "relax",
          "relaxing", "relaxation", "mindfulness", "mindful")),
    Kind("career", "career and job",
         "career fairs, jobs and internships, resumes, interviews, networking, employers, "
         "graduate school and professional skills",
         ("career", "careers", "job", "jobs", "internship", "internships", "networking",
          "network", "professional", "professionals", "employer", "employers", "employment",
          "hiring", "professional development")),
    Kind("leadership", "leadership",
         "leadership training, programs, conferences and retreats, and developing student "
         "leaders",
         ("leadership", "leader", "leaders")),
    Kind("academic", "academic",
         "academic subjects and achievement: lectures, talks and discussions on academic "
         "subjects, research presentations, advising and registration help, academic honors, "
         "honor societies and their inductions, and clubs for an academic subject",
         ("academic", "academics"), includes=("study",)),
    Kind("study", "study help",
         "help with classes and studying: tutoring, study sessions, study halls and study "
         "groups, writing, research and citation help, study skills and exam prep",
         ("study", "studying", "tutoring", "tutor", "tutors", "homework", "study session",
          "study sessions", "study group", "study groups", "study hall", "study halls")),
    Kind("service", "volunteer and service",
         "community service, volunteering, donation drives, charity events and fundraisers, "
         "cleanups",
         ("service", "volunteer", "volunteers", "volunteering", "community service", "charity",
          "charities", "charitable", "fundraiser", "fundraisers", "fundraising", "donate",
          "donating", "donation", "donations", "give back", "giving back")),
    Kind("greek", "Greek life",
         "fraternity and sorority chapters and their events, recruitment, step shows",
         ("greek", "greek life", "fraternity", "fraternities", "frat", "frats", "sorority",
          "sororities", "rush")),
    Kind("faith", "religious and faith",
         "worship, prayer, scripture study, religious holidays, faith groups and interfaith "
         "events",
         ("religious", "religion", "religions", "faith", "faiths", "spiritual", "spirituality",
          "worship", "prayer", "prayers", "pray", "interfaith", "religious services",
          "worship services")),
    Kind("civic", "political and civic",
         "politics and government: elections and voting, political parties and political clubs, "
         "debates about politics, student government, public policy and laws, citizenship",
         ("political", "politics", "civic", "civics", "election", "elections", "voting", "vote",
          "voter", "voters", "government", "student government")),
    Kind("advocacy", "activism and advocacy",
         "activism and advocacy: campaigns for a cause, awareness weeks, rallies and protests, "
         "social justice and social change",
         ("activism", "activist", "activists", "advocacy", "advocate", "advocates",
          "social justice", "social change")),
    Kind("lgbtq", "LGBTQ+",
         "Pride, queer and trans community and allies, LGBTQ+ history and issues",
         ("lgbtq", "lgbt", "lgbtqia", "queer", "gay", "lesbian", "bisexual", "trans",
          "transgender", "nonbinary", "pride")),
    Kind("women", "women's and gender",
         "women's empowerment, women's history, feminism and gender equity",
         ("women", "woman", "womens", "feminist", "feminism", "gender")),
    Kind("environment", "environment and sustainability",
         "climate, sustainability, recycling, gardening, nature and conservation, cleanups",
         ("environment", "environmental", "sustainability", "sustainable", "green", "climate",
          "eco", "nature")),
    Kind("tech", "science and technology",
         "STEM, coding, computing, data, engineering, hackathons, science talks and labs. Not "
         "art, music or film made with software, such as animation (those are art, music or "
         "film)",
         ("tech", "technology", "technologies", "technical", "stem", "science", "sciences",
          "scientific", "computer", "computers", "computing", "engineering")),
    Kind("games", "game and gaming",
         "game nights, board, card and video games, esports, trivia, bingo and puzzles",
         ("game", "games", "gaming", "gamer", "gamers", "esports", "e sports", "game night",
          "game nights")),
    Kind("money", "money and finance",
         "budgeting, saving, credit, taxes, financial aid workshops and financial literacy",
         ("money", "financial", "finance", "finances", "budget", "budgeting", "saving",
          "savings", "financial literacy", "personal finance")),
)

BY_KEY = {kind.key: kind for kind in KINDS}


def with_included(keys: set[str] | frozenset[str]) -> frozenset[str]:
    """The kinds asked and the narrower kinds each includes (Kind.includes), for finding the
    listings: "international events" lists the study abroad sessions too (11-03 practice: once
    a study abroad fair was judged by what happens at it, not by its international center
    host, "events for international students" lost them)."""
    found = set(keys)
    for key in keys:
        found |= set(BY_KEY[key].includes) if key in BY_KEY else set()
    return frozenset(found)


# How the model gives each card its kinds (scripts/build_meaning_index.py), before the list.
RULES = (
    "You see listings from Ramapo College's campus calendar and club directory. For each, pick "
    "the kinds below that a student asking for that kind of event or club would want it listed "
    "for. Most listings have one kind, some two or three, and some none (a residence hall's floor "
    "meeting, an office's open hours). Judge an event by what happens at it, not by who hosts it "
    "or where: an event is not cultural because a cultural center hosts it, nor theater because "
    "it is held in a theater. Judge a club by its main purpose, from its name and mission, not by "
    "an event it sometimes hosts: a board that plans campus events is not a music club because "
    "it books concerts, and an honor society is academic, not a sports or political club because "
    "its members play sports or study politics. A club's own meeting or social is the club's "
    "kind. An office's or academic department's own directory entry has no kind. A listing that "
    "only reserves, holds, blocks or locks out a room or stage, or is for staff only, has no "
    "kind, even when the room is a theater. Use only the listing's own words, never what you may "
    "know about the club or college: a listing whose name and words don't say what it is or does "
    "has no kind.")

# The rules and the list as the model reads them. A change to either gives every card its kinds
# again (the cache is keyed by this version), so a reworded rule or kind never keeps old picks.
KINDS_VERSION = hashlib.sha256("\n".join([RULES, *(f"{k.key}: {k.holds}" for k in KINDS)]
                                         ).encode()).hexdigest()[:16]


def names(keys: set[str] | frozenset[str]) -> str:
    """The kinds as a reply says them, in the list's order, the last after "or" ("music or
    theater and stage")."""
    said = [kind.name for kind in KINDS if kind.key in keys]
    if len(said) <= 1:
        return "".join(said)
    return ", ".join(said[:-1]) + " or " + said[-1]
