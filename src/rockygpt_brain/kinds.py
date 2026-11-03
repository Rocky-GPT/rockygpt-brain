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


# Words that name a stage performance of any kind ("any shows this weekend").
_SHOWS = ("show", "shows", "performance", "performances", "perform", "performing", "performer",
          "performers", "performing arts")

KINDS: tuple[Kind, ...] = (
    Kind("arts", "art and crafts",
         "making or viewing visual art: painting, drawing, crafts and DIY, decorating, ceramics, "
         "photography, design, art exhibits and galleries",
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
         ("international", "global")),
    Kind("abroad", "study abroad",
         "study abroad programs and info sessions, and trips abroad for credit or service",
         ("abroad", "overseas", "study abroad")),
    Kind("athletics", "sports",
         "games and matches to watch or play: Ramapo Roadrunners varsity games, club sports and "
         "intramural competitions, sports teams, sports watch parties",
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
    Kind("academic", "study help and academic",
         "tutoring, study sessions and study skills, academic workshops, advising and "
         "registration help, research presentations, academic honors",
         ("academic", "academics", "study", "studying", "tutoring", "tutor", "tutors",
          "homework", "study session", "study sessions")),
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
         "elections, voting, political debates, government, activism, advocacy and social "
         "justice",
         ("political", "politics", "civic", "civics", "election", "elections", "voting", "vote",
          "voter", "voters", "government", "activism", "activist", "activists", "advocacy",
          "social justice")),
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
         "STEM, coding, computing, data, engineering, hackathons, science talks and labs",
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

# The list as the model reads it. A change to it gives every card its kinds again (the cache is
# keyed by this version), so a renamed or reworded kind never keeps another kind's picks.
KINDS_VERSION = hashlib.sha256("\n".join(f"{k.key}: {k.holds}" for k in KINDS).encode()
                               ).hexdigest()[:16]


def names(keys: set[str] | frozenset[str]) -> str:
    """The kinds as a reply says them, in the list's order, the last after "or" ("music or
    theater and stage")."""
    said = [kind.name for kind in KINDS if kind.key in keys]
    if len(said) <= 1:
        return "".join(said)
    return ", ".join(said[:-1]) + " or " + said[-1]
