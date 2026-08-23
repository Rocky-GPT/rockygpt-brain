import json

from rockygpt_brain.brain.answer import (
    MAX_CITED_SOURCE_ID_LENGTH,
    MAX_SUGGESTED_QUESTION_LENGTH,
    parse_submit_answer,
)


def _args(**overrides: object) -> str:
    base: dict[str, object] = {"answerMarkdown": "The library closes at 9pm.", "route": "standard"}
    base.update(overrides)
    return json.dumps(base)


def test_valid_standard_answer_parses() -> None:
    parsed = parse_submit_answer(_args())
    assert parsed is not None
    assert parsed.answer_markdown == "The library closes at 9pm."
    assert parsed.route == "standard"
    assert parsed.cited_source_ids == []


def test_valid_answer_with_citations_parses() -> None:
    parsed = parse_submit_answer(_args(citedSourceIds=["src-1", "src-2"]))
    assert parsed is not None
    assert parsed.cited_source_ids == ["src-1", "src-2"]


def test_malformed_json_fails_closed() -> None:
    assert parse_submit_answer("{not json") is None


def test_non_object_json_fails_closed() -> None:
    assert parse_submit_answer("42") is None
    assert parse_submit_answer('"a string"') is None
    assert parse_submit_answer("null") is None


def test_unknown_field_rejected() -> None:
    assert parse_submit_answer(_args(unexpectedField="x")) is None


def test_whitespace_only_answer_rejected() -> None:
    assert parse_submit_answer(_args(answerMarkdown="   \n\t  ")) is None


def test_empty_answer_rejected() -> None:
    assert parse_submit_answer(_args(answerMarkdown="")) is None


def test_invalid_route_rejected() -> None:
    assert parse_submit_answer(_args(route="safety")) is None  # model can't self-route safety
    assert parse_submit_answer(_args(route="made_up")) is None


def test_ungrounded_with_citations_rejected_whole_answer() -> None:
    parsed = parse_submit_answer(
        _args(route="ungrounded", citedSourceIds=["src-1"])
    )
    assert parsed is None


def test_ungrounded_without_citations_ok() -> None:
    parsed = parse_submit_answer(_args(route="ungrounded"))
    assert parsed is not None
    assert parsed.route == "ungrounded"


def test_oversized_cited_source_id_rejects_whole_answer() -> None:
    too_long = "x" * (MAX_CITED_SOURCE_ID_LENGTH + 1)
    assert parse_submit_answer(_args(citedSourceIds=[too_long])) is None


def test_whitespace_padded_cited_source_id_rejected() -> None:
    assert parse_submit_answer(_args(citedSourceIds=[" src-1"])) is None
    assert parse_submit_answer(_args(citedSourceIds=["src-1 "])) is None


def test_control_char_in_cited_source_id_rejected() -> None:
    assert parse_submit_answer(_args(citedSourceIds=["src-1\x00"])) is None


def test_duplicate_cited_source_ids_rejected() -> None:
    assert parse_submit_answer(_args(citedSourceIds=["src-1", "src-1"])) is None


def test_duplicate_suggested_questions_rejected() -> None:
    parsed = parse_submit_answer(
        _args(suggestedQuestions=["What are library hours?", "What are library hours?"])
    )
    assert parsed is None


def test_whitespace_only_suggested_question_rejected() -> None:
    assert parse_submit_answer(_args(suggestedQuestions=["   "])) is None


def test_oversized_suggested_question_rejected_not_truncated() -> None:
    too_long = "x" * (MAX_SUGGESTED_QUESTION_LENGTH + 1)
    assert parse_submit_answer(_args(suggestedQuestions=[too_long])) is None


def test_too_many_suggested_questions_rejected() -> None:
    questions = [f"Question number {i}?" for i in range(11)]
    assert parse_submit_answer(_args(suggestedQuestions=questions)) is None


def test_valid_ui_action_parses() -> None:
    parsed = parse_submit_answer(
        _args(uiActions=[{"type": "VIEW_MENU", "payload": {"meal": "dinner"}}])
    )
    assert parsed is not None
    assert parsed.ui_actions[0].type == "VIEW_MENU"


def test_invalid_ui_action_type_rejects_whole_answer() -> None:
    assert parse_submit_answer(_args(uiActions=[{"type": "VIEW_NONSENSE"}])) is None


def test_ui_action_payload_with_control_char_value_rejects_whole_answer() -> None:
    assert (
        parse_submit_answer(
            _args(uiActions=[{"type": "VIEW_MENU", "payload": {"meal": "dinner\x00"}}])
        )
        is None
    )


def test_ui_action_payload_with_whitespace_only_key_rejects_whole_answer() -> None:
    assert (
        parse_submit_answer(
            _args(uiActions=[{"type": "VIEW_MENU", "payload": {"  ": "dinner"}}])
        )
        is None
    )


def test_ui_action_payload_too_many_entries_rejects_whole_answer() -> None:
    payload = {f"key{i}": "v" for i in range(6)}
    assert (
        parse_submit_answer(_args(uiActions=[{"type": "VIEW_MENU", "payload": payload}])) is None
    )


def test_extra_field_in_ui_action_rejected() -> None:
    assert (
        parse_submit_answer(
            _args(uiActions=[{"type": "VIEW_MENU", "extraField": "nope"}])
        )
        is None
    )


def test_wrong_type_for_arguments_rejects_strictly() -> None:
    # strict=True: no coercing an int into a string field.
    assert parse_submit_answer(_args(answerMarkdown=12345)) is None
