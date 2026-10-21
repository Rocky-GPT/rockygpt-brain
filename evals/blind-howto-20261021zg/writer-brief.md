# Writing blind test questions for a campus chatbot: "how do I" and policy questions

You are writing realistic questions that real people would type into RockyGPT, the chatbot of Ramapo
College of New Jersey. You are one of several independent writers. Do NOT read any files in this
workspace other than this brief, do not run code, and do not browse the web: write from the persona
alone, the way a real person would ask, without knowing how the bot works.

This set is about procedures and rules: how to get something done at the college, and what an
office's policy is. Examples of the kind (do not copy them): how to order a transcript, how to
appeal a parking ticket, how to change a major, whether guests can stay overnight, how to get a
replacement ID, how to drop a class, how to request housing accommodations, what to do if you lose
something, how to get reimbursed, how to reserve a room.

The bot is meant to answer from what the college's own office web pages say. It does NOT know:
anyone's personal records (grades, balance, schedule), live status, prices that change, or advice.
Real people still ask such things sometimes; include two or three.

Write exactly 15 questions in your persona's voice. Mix: short and long, typos and abbreviations
where natural, questions that ask for two things at once (how to do it and who to contact), and
two or three out-of-scope ones. Each question must stand alone (one message, no earlier
conversation).

Output: a JSON array written with the Write tool to the file named in your task:
[{"id": "<prefix>-01", "question": "...", "topic": "procedure|policy|contact|out_of_scope"}, ...]
Reply with just "done".
