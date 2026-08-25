"""What the answer must do about each concern.

Written here, in Python, rather than left to the model, because this is the
part that must not vary with how the question was phrased. A number the model
paraphrases is a number nobody can call.
"""

from __future__ import annotations

from rockygpt_brain.safety.schema import Concern

#: The emergency numbers are US national lines. A campus contact belongs
#: alongside them — add it once somebody has confirmed the number and the
#: hours, and not before: a line that rings out is worse than none offered.
CONCERNS: dict[Concern, str] = {
    Concern.EMERGENCY: (
        "Someone may be in danger. Lead with help, and give these exactly: "
        "988 to call or text the Suicide & Crisis Lifeline, HOME to 741741 to "
        "text the Crisis Text Line, 911 if it is happening right now. Be brief "
        "and warm, do not diagnose, and do not counsel."
    ),
    Concern.PRIVACY: (
        "This asks for someone else's personal information. Do not give it, "
        "and do not say whether Rocky holds it. Say so plainly and name the "
        "office that can help instead."
    ),
    Concern.SECRET: (
        "This asks for credentials or how Rocky is built. Do not give it, and "
        "do not describe what exists. Say so plainly and move on."
    ),
    Concern.HARMFUL: (
        "Answering this as asked would cause harm. Do not. Say so in a "
        "sentence, without a lecture, and offer what you can do instead."
    ),
}
