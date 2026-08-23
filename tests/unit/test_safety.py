from rockygpt_brain.brain.safety import classify_safety


def _reason(message: str) -> str | None:
    result = classify_safety(message)
    return result.reason if result else None


class TestSuicidalIntent:
    def test_direct_statement_triggers(self) -> None:
        assert _reason("I want to die") == "suicidal_intent"

    def test_kill_myself_triggers(self) -> None:
        assert _reason("I'm going to kill myself tonight") == "suicidal_intent"

    def test_negation_does_not_trigger(self) -> None:
        assert _reason("I don't want to kill myself, just venting") is None

    def test_third_person_does_not_trigger(self) -> None:
        assert _reason("she wants to die, she told me yesterday") is None

    def test_dont_want_to_be_alive_anymore_triggers(self) -> None:
        assert _reason("I don't want to be alive anymore") == "suicidal_intent"

    def test_dont_want_to_be_alive_anymore_survives_prior_contraction(self) -> None:
        # The apostrophe in "I'm" must never be treated as a quote mark.
        assert _reason("I'm scared. I don't want to be alive anymore") == "suicidal_intent"

    def test_quoted_report_does_not_trigger(self) -> None:
        assert _reason('The note said "I want to die"') is None

    def test_reported_negated_phrase_does_not_trigger(self) -> None:
        assert _reason('She said "I don\'t want to be alive anymore"') is None

    def test_minor_injury_unrelated(self) -> None:
        assert _reason("I stabbed my toe on the desk") is None


class TestActiveEmergency:
    def test_active_shooter_triggers(self) -> None:
        assert _reason("there is an active shooter in the library") == "active_emergency"

    def test_fire_triggers(self) -> None:
        assert _reason("there's a fire in my dorm right now, help") == "active_emergency"

    def test_unconscious_triggers(self) -> None:
        assert _reason("my roommate is unconscious and not breathing") == "active_emergency"

    def test_historical_context_suppresses(self) -> None:
        assert _reason("I passed out yesterday but I'm fine now") is None

    def test_minor_self_injury_not_weapon_emergency(self) -> None:
        assert _reason("I stabbed my toe on the desk") is None

    def test_benign_possession_not_emergency(self) -> None:
        assert _reason("I have a gun locked in my car for hunting season") is None

    def test_informational_question_suppressed(self) -> None:
        assert _reason("What is the fire evacuation procedure for the dorms?") is None

    def test_hypothetical_phrasing_with_trigger_words_suppressed(self) -> None:
        assert _reason("What is the procedure if there is a fire in my dorm?") is None

    def test_urgency_overrides_informational_suppression(self) -> None:
        assert (
            _reason("What should I do, there's a fire right now and I can't get out")
            == "active_emergency"
        )

    def test_urgency_wins_within_shared_clause(self) -> None:
        message = "Yesterday this was a drill, but right now someone is shooting"
        assert _reason(message) == "active_emergency"

    def test_urgent_clause_survives_neighboring_historical_clause(self) -> None:
        message = "Yesterday there was a drill. Right now someone is shooting."
        assert _reason(message) == "active_emergency"

    def test_ordinary_procedure_question_not_misclassified(self) -> None:
        assert _reason("How do I report a weapon on campus policy-wise?") is None


class TestNoTrigger:
    def test_ordinary_question_is_none(self) -> None:
        assert classify_safety("What time does the library close?") is None

    def test_empty_message_is_none(self) -> None:
        assert classify_safety("") is None
