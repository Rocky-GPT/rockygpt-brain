# Writing blind test questions for a campus chatbot

You are writing realistic questions that real people would type into RockyGPT, the chatbot of Ramapo
College of New Jersey. You are one of several independent writers. Do NOT read any files in this
workspace other than this brief, do not run code, and do not browse the web: write from the persona
alone, the way a real person would ask, without knowing how the bot works.

What the bot is meant to answer (from the college's published pages):
- Campus offices and services: phone, email, room or location, website, opening hours, and how to
  reach them (appointments, walk-ins). This covers student offices and also staff, administrative,
  alumni, parent and public-facing offices.
- Dining: hours of the campus dining places, and menus (dishes, calories, vegan/vegetarian, allergens).
- The campus shuttle: posted timetables, when the next/first/last shuttle leaves, where it stops.
- Sites with no office of their own (like "scholarships" or "summer session"): which office handles it.
What it does NOT know: anyone's personal records (grades, balance, schedule), live shuttle location,
events, class content, prices, advice. Real people still ask such things sometimes; include a few.

Write exactly 25 questions in your persona's voice. Mix: short and long, typos and abbreviations
where natural, relative times ("tomorrow", "tonight", "right now"), questions that ask for two
things at once, and 2-3 out-of-scope ones. Do not copy any example below; they show style only.
Each question must stand alone (one message, no earlier conversation).

Output: a JSON array written with the Write tool to the file named in your task:
[{"id": "<prefix>-01", "question": "...", "topic": "office|dining|menu|shuttle|topic|out_of_scope"}, ...]
Reply with just "done".
