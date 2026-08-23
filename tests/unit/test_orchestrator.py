import json
from typing import Any

from rockygpt_brain.brain.model_client import ModelTurn, ToolCall
from rockygpt_brain.brain.orchestrator import (
    FALLBACK_ANSWER,
    MAX_TOOL_ITERATIONS,
    MAX_TOTAL_TOOL_CALLS,
    run_chat_turn,
)
from rockygpt_brain.data_client.errors import DataServiceUnavailable
from rockygpt_brain.schemas.chat import ChatRequest


class FakeModelClient:
    """Scripted ModelClient: returns each queued ModelTurn in order."""

    def __init__(self, turns: list[ModelTurn]) -> None:
        self._turns = list(turns)
        self.calls: list[dict[str, Any]] = []

    async def complete(self, *, messages, tools, force_tool=None) -> ModelTurn:
        self.calls.append({"force_tool": force_tool, "message_count": len(messages)})
        if not self._turns:
            raise AssertionError("FakeModelClient ran out of scripted turns")
        return self._turns.pop(0)

    async def aclose(self) -> None:
        pass


class FakeDataClient:
    """Duck-typed stand-in for DataServiceClient; only used if a test's
    scripted tool calls actually reach data-service execution."""

    async def search_campus_hours(self, **kwargs: object):
        raise DataServiceUnavailable()


def _submit_call(call_id: str, **args: object) -> ToolCall:
    payload = {"answerMarkdown": "Here is the answer.", "route": "standard"}
    payload.update(args)
    return ToolCall(id=call_id, name="submit_answer", arguments_json=json.dumps(payload))


def _tool_call(call_id: str, name: str = "search_campus_hours", **args: object) -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments_json=json.dumps(args))


def _request(message: str = "What time does the library close?") -> ChatRequest:
    return ChatRequest(message=message)


class TestSubmitAnswerConstraints:
    async def test_submit_mixed_with_data_call_falls_back(self) -> None:
        turn = ModelTurn(
            content=None, tool_calls=[_submit_call("c1"), _tool_call("c2")]
        )
        model_client = FakeModelClient([turn] * MAX_TOOL_ITERATIONS)
        outcome = await run_chat_turn(
            request=_request(), model_client=model_client, data_client=FakeDataClient()
        )
        assert outcome.answer == FALLBACK_ANSWER
        assert outcome.route == "ungrounded"

    async def test_multiple_submit_calls_in_one_turn_falls_back(self) -> None:
        turn = ModelTurn(content=None, tool_calls=[_submit_call("c1"), _submit_call("c2")])
        model_client = FakeModelClient([turn] * MAX_TOOL_ITERATIONS)
        outcome = await run_chat_turn(
            request=_request(), model_client=model_client, data_client=FakeDataClient()
        )
        assert outcome.answer == FALLBACK_ANSWER

    async def test_unknown_cited_source_id_falls_back(self) -> None:
        turn = ModelTurn(
            content=None,
            tool_calls=[_submit_call("c1", citedSourceIds=["never-produced"])],
        )
        model_client = FakeModelClient([turn])
        outcome = await run_chat_turn(
            request=_request(), model_client=model_client, data_client=FakeDataClient()
        )
        assert outcome.answer == FALLBACK_ANSWER

    async def test_clean_submit_succeeds(self) -> None:
        turn = ModelTurn(content=None, tool_calls=[_submit_call("c1")])
        model_client = FakeModelClient([turn])
        outcome = await run_chat_turn(
            request=_request(), model_client=model_client, data_client=FakeDataClient()
        )
        assert outcome.answer == "Here is the answer."
        assert outcome.route == "standard"


class TestDuplicateAndBudget:
    async def test_duplicate_ids_within_one_batch_falls_back(self) -> None:
        turn = ModelTurn(
            content=None, tool_calls=[_tool_call("dup"), _tool_call("dup", name="search_clubs")]
        )
        model_client = FakeModelClient([turn] * MAX_TOOL_ITERATIONS)
        outcome = await run_chat_turn(
            request=_request(), model_client=model_client, data_client=FakeDataClient()
        )
        assert outcome.answer == FALLBACK_ANSWER

    async def test_duplicate_id_across_batches_falls_back(self) -> None:
        first = ModelTurn(content=None, tool_calls=[_tool_call("reused-id")])
        second = ModelTurn(content=None, tool_calls=[_tool_call("reused-id", name="search_clubs")])
        model_client = FakeModelClient([first, second, second, second])
        outcome = await run_chat_turn(
            request=_request(), model_client=model_client, data_client=FakeDataClient()
        )
        assert outcome.answer == FALLBACK_ANSWER

    async def test_batch_exceeding_remaining_budget_falls_back(self) -> None:
        oversized_batch = ModelTurn(
            content=None,
            tool_calls=[
                _tool_call(f"c{i}") for i in range(MAX_TOTAL_TOOL_CALLS + 1)
            ],
        )
        model_client = FakeModelClient([oversized_batch])
        outcome = await run_chat_turn(
            request=_request(), model_client=model_client, data_client=FakeDataClient()
        )
        assert outcome.answer == FALLBACK_ANSWER

    async def test_solo_submit_fits_exactly_at_reserved_final_slot(self) -> None:
        # Consume MAX_TOTAL_TOOL_CALLS - 1 of the budget with one batch of
        # data calls (derived from the constant, not hardcoded, so this
        # keeps testing the actual boundary if the limit ever changes).
        # That leaves exactly one slot: force_final must trigger
        # (total_tool_calls >= MAX_TOTAL_TOOL_CALLS - 1), and the resulting
        # solo submit_answer call must still fit the uniform budget check
        # (remaining_budget == 1, batch size == 1) rather than being
        # rejected for being "at" the cap.
        near_cap_batch = ModelTurn(
            content=None,
            tool_calls=[_tool_call(f"c{i}") for i in range(MAX_TOTAL_TOOL_CALLS - 1)],
        )
        final_submit = ModelTurn(content=None, tool_calls=[_submit_call("final")])
        model_client = FakeModelClient([near_cap_batch, final_submit])
        outcome = await run_chat_turn(
            request=_request(), model_client=model_client, data_client=FakeDataClient()
        )
        assert outcome.answer == "Here is the answer."
        # No single "total tool calls including submit" counter is exposed
        # on ChatOutcome (submit_answer itself is deliberately never added
        # to tool_calls_log/tools_invoked — see orchestrator.py). Instead,
        # confirm the full MAX_TOTAL_TOOL_CALLS budget was exercised via
        # the two things that *are* observable: exactly
        # MAX_TOTAL_TOOL_CALLS - 1 data calls were logged, and the solo
        # submit that consumed the final slot succeeded (proven by the
        # answer above) rather than falling back.
        assert len(outcome.tool_calls_log) == MAX_TOTAL_TOOL_CALLS - 1
        assert outcome.debug_info.get("tool_call_count") == MAX_TOTAL_TOOL_CALLS - 1


class TestLoggingNeverContainsRawArguments:
    async def test_tool_calls_log_has_no_raw_argument_values(self) -> None:
        secret_looking_arg = "eve@example.com super secret plan"
        first = ModelTurn(
            content=None, tool_calls=[_tool_call("c1", q=secret_looking_arg)]
        )
        second = ModelTurn(content=None, tool_calls=[_submit_call("c2")])
        model_client = FakeModelClient([first, second])
        outcome = await run_chat_turn(
            request=_request(), model_client=model_client, data_client=FakeDataClient()
        )
        serialized_log = json.dumps(outcome.tool_calls_log)
        assert secret_looking_arg not in serialized_log
        assert outcome.tool_calls_log == [
            {"tool": "search_campus_hours", "result": "data_unavailable"}
        ]

    async def test_unknown_model_provided_tool_name_logged_as_unknown(self) -> None:
        first = ModelTurn(content=None, tool_calls=[_tool_call("c1", name="not_a_real_tool")])
        second = ModelTurn(content=None, tool_calls=[_submit_call("c2")])
        model_client = FakeModelClient([first, second])
        outcome = await run_chat_turn(
            request=_request(), model_client=model_client, data_client=FakeDataClient()
        )
        assert outcome.tools_invoked == ["unknown"]
        assert outcome.tool_calls_log[0]["tool"] == "unknown"


class TestNoToolCallFallsBack:
    async def test_plain_text_is_retried_with_submit_answer_forced(self) -> None:
        # The model answered without calling submit_answer. That content is
        # a real answer, so the turn is re-prompted with the tool forced
        # rather than thrown away.
        plain = ModelTurn(content="RockyGPT is your campus assistant.", tool_calls=[])
        submitted = ModelTurn(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_1",
                    name="submit_answer",
                    arguments_json=json.dumps(
                        {
                            "answerMarkdown": "RockyGPT is your campus assistant.",
                            "route": "ungrounded",
                        }
                    ),
                )
            ],
        )
        model_client = FakeModelClient([plain, submitted])
        outcome = await run_chat_turn(
            request=_request(), model_client=model_client, data_client=FakeDataClient()
        )
        assert outcome.answer == "RockyGPT is your campus assistant."
        assert outcome.route == "ungrounded"
        assert model_client.calls[1]["force_tool"] == "submit_answer"

    async def test_plain_text_falls_back_when_the_model_still_refuses_the_tool(self) -> None:
        # One retry only: a model that will not call submit_answer even
        # when it is forced still falls back.
        plain = ModelTurn(content="just chatting, no tool call", tool_calls=[])
        model_client = FakeModelClient([plain, plain])
        outcome = await run_chat_turn(
            request=_request(), model_client=model_client, data_client=FakeDataClient()
        )
        assert outcome.answer == FALLBACK_ANSWER
        assert len(model_client.calls) == 2


class TestSafetyBypassesModel:
    async def test_active_emergency_never_calls_model(self) -> None:
        model_client = FakeModelClient([])  # would raise if called at all

        class UnavailableSafetyDataClient:
            async def safety_resources(self):
                raise DataServiceUnavailable()

        outcome = await run_chat_turn(
            request=_request("there's a fire in my dorm right now, help"),
            model_client=model_client,
            data_client=UnavailableSafetyDataClient(),
        )
        assert outcome.route == "safety"
        assert "911" in outcome.answer
        assert model_client.calls == []
