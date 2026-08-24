"""Emergency replies, assembled rather than written. Contract section 4.2.

Whether someone is in danger is a judgement, so the Listener makes it. What to
say once that judgement is made is not a judgement, so it is fixed here, where a
later stage cannot soften it.

The kind matters. A single paragraph for every emergency answers the wrong
question for most of them, so the reply differs by danger class and the
difference lives in code.
"""

from __future__ import annotations

from rockygpt_brain.core.interpretation import DangerClass

_UNIVERSAL = "If anyone is in immediate danger, call 911 now."

_BLOCKS: dict[DangerClass, str] = {
    DangerClass.MEDICAL: (
        f"{_UNIVERSAL} Stay with the person, check whether they are breathing, and tell the "
        "dispatcher your exact building and room. On campus you can also reach Public Safety, "
        "but call 911 first."
    ),
    DangerClass.FIRE: (
        f"{_UNIVERSAL} Leave the building by the nearest exit, pull an alarm on your way out if "
        "one is not already sounding, and do not use lifts."
    ),
    DangerClass.WEAPON: (
        f"{_UNIVERSAL} Get out of sight, stay quiet, and do not approach the person. Call when "
        "it is safe to speak, or send a text if it is not."
    ),
    DangerClass.VIOLENCE: (
        f"{_UNIVERSAL} Move somewhere safe before doing anything else, and call when you can."
    ),
    DangerClass.SELF_HARM: (
        "If you are in danger of hurting yourself, call or text 988 to reach the Suicide and "
        "Crisis Lifeline in the United States. If you have already been hurt, call 911."
    ),
    DangerClass.OTHER: _UNIVERSAL,
}


def safety_block(danger: DangerClass) -> str | None:
    """The reply for a danger class, or None when there is no emergency."""

    if danger is DangerClass.NONE:
        return None
    return _BLOCKS[danger]
