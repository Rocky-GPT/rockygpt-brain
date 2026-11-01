# Writing blind test questions for a campus chatbot: majors, minors, requirements and courses

You are writing realistic questions that real people would type into RockyGPT, the chatbot of Ramapo
College of New Jersey. You are one of several independent writers. Do NOT read any files in this
workspace other than this brief, do not run code, and do not browse the web: write from the persona
alone, the way a real person would ask, without knowing how the bot works.

This set is about academics: majors, minors and certificates, what a program requires, specific
courses (what a course covers, its credits, its prerequisites, whether it counts for something),
general education requirements, and recommended course sequences. Examples of the kind (do not
copy them): what classes do I need for the psychology major, what are the prerequisites for a given
course code, how many credits is a minor, does a course count toward gen ed, what should a freshman
take first in a major, is there a major in some field.

The bot is meant to answer from the college's own published catalog and program pages. It does NOT
know: anyone's personal records (grades, transcript, degree audit, schedule), which sections are
open this semester or who is teaching them, or advice about what someone should choose. Real people
still ask such things sometimes; include two or three.

Write exactly 15 questions in your persona's voice. Mix: short and long, typos and abbreviations
where natural (people often type course codes like "cmps 147" or "psych 101"), questions that ask
for two things at once, and two or three out-of-scope ones. Each question must stand alone (one
message, no earlier conversation).

Output: a JSON array written with the Write tool to the file named in your task:
[{"id": "<prefix>-01", "question": "...", "topic": "program|course|gen_ed|sequence|out_of_scope"}, ...]
Reply with just "done".
