# Writing blind test questions for a campus chatbot: faculty and staff lookup

You are writing realistic questions that real people would type into RockyGPT, the chatbot of Ramapo
College of New Jersey. You are one of several independent writers. Do NOT read any files in this
workspace other than this brief, do not run code, and do not browse the web: write from the persona
alone, the way a real person would ask, without knowing how the bot works.

This set is about looking up people who work at the college: a professor's or staff member's email,
phone, office room, title, department or school, office hours, what they research or teach, and who
someone is. Real people often know only a last name ("professor smith"), misspell names, or ask for
"the chair of" or "who teaches" something. You do not know the real names of Ramapo faculty, so
where a question needs a name, use the kind of name a student might type (common surnames are
fine) and accept that some will not exist.

The bot answers from the college's published faculty and staff directory. It does NOT know:
personal information beyond the directory (home address, personal phone, schedule, whether
someone is in today), ratings or opinions of professors, or who to pick. Real people still ask such
things sometimes; include two or three.

Write exactly 15 questions in your persona's voice. Mix: short and long, typos where natural,
questions asking two things at once, and two or three out-of-scope ones. Each question must stand
alone (one message, no earlier conversation).

Output: a JSON array written with the Write tool to the file named in your task:
[{"id": "<prefix>-01", "question": "...", "topic": "contact|role|teaching|out_of_scope"}, ...]
Reply with just "done".
