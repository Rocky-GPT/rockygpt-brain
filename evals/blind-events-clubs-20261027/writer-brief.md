# Writing blind test questions for a campus chatbot: campus events and student clubs

You are writing realistic questions that real people would type into RockyGPT, the chatbot of Ramapo
College of New Jersey. You are one of several independent writers. Do NOT read any files in this
workspace other than this brief, do not run code, and do not browse the web: write from the persona
alone, the way a real person would ask, without knowing how the bot works.

This set is about campus events and student clubs and organizations: what is happening today,
tonight, this weekend or next week, events of a kind (sports games, performances, career events,
free food, workshops), where and when an event is, who is hosting it, whether it is free; and clubs:
is there a club for something, what clubs exist in an area (cultural, academic, sports, arts,
service, Greek life), how to find or join one, a club's page.

The bot answers from the college's published event calendar and club directory. It does NOT know:
anything not on the calendar, live attendance, a club's meeting times or officers unless published,
or advice about which club someone should join. Real people still ask such things sometimes;
include two or three.

Write exactly 15 questions in your persona's voice. Mix: short and long, typos and abbreviations
where natural, questions asking two things at once, relative times ("tonight", "this weekend",
"next friday") and two or three out-of-scope ones. Each question must stand alone (one message, no
earlier conversation).

Output: a JSON array written with the Write tool to the file named in your task:
[{"id": "<prefix>-01", "question": "...", "topic": "event|club|out_of_scope"}, ...]
Reply with just "done".
