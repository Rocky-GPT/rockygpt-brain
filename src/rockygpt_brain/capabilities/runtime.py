"""Optional composition boundary for RockyGPT's installed capabilities."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from importlib import import_module
from types import ModuleType
from typing import Literal, Protocol, TypedDict, cast
from zoneinfo import ZoneInfo

from openai import OpenAI
from openai.types.responses import FunctionToolParam, ResponseInputParam

TRANSPORTATION_UNAVAILABLE_ANSWER = "Campus transportation is temporarily unavailable."
TRANSPORTATION_TOOL_NAME = "campus_transportation"
TRANSPORTATION_UNAVAILABLE_INSTRUCTIONS = """For a RockyGPT campus transportation request,
call the campus_transportation tool instead of answering. For any other request, answer normally.
Do not provide campus transportation facts."""
TRANSPORTATION_UNAVAILABLE_TOOL = cast(
    FunctionToolParam,
    {
        "type": "function",
        "name": TRANSPORTATION_TOOL_NAME,
        "description": (
            "Select for any request about RockyGPT campus shuttle transportation. "
            "The transportation capability is currently unavailable."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        "strict": True,
    },
)
_CAMPUS_TIME_ZONE = ZoneInfo("America/New_York")
_TRANSPORTATION_MODULE_NAMES = frozenset(
    {
        "rockygpt_brain.capabilities.transportation",
        "rockygpt_brain.capabilities.transportation.contracts",
        "rockygpt_brain.capabilities.transportation.execution",
        "rockygpt_brain.capabilities.transportation.interpretation",
    }
)


class ConversationMessage(TypedDict):
    """One original ordered chat message."""

    role: Literal["user", "assistant"]
    content: str


class _Dumpable(Protocol):
    def model_dump(self, *, mode: Literal["json"]) -> dict[str, object]: ...


class _Interpretation(_Dumpable, Protocol):
    selected: bool
    request: object | None
    model: str


class _Result(_Dumpable, Protocol):
    provenance: _Dumpable | None


class _Execute(Protocol):
    def __call__(self, request: object, *, data: object | None) -> _Result: ...


@dataclass(frozen=True)
class _TransportationModules:
    interpret: Callable[
        [Sequence[ConversationMessage], str], tuple[str, _Interpretation]
    ]
    repair: Callable[
        [Sequence[ConversationMessage], str], tuple[str, _Interpretation]
    ]
    interpretation_failure: Callable[[str], tuple[str, _Interpretation]]
    load_data: Callable[[], object]
    route_mentions_match: Callable[[object, object], bool]
    execute: _Execute
    answer: Callable[[_Result], str]


def load_transportation_modules() -> _TransportationModules | None:
    """Load only this capability; its deliberate absence does not break the Brain shell."""
    try:
        import_module("rockygpt_brain.capabilities.transportation.contracts")
        execution = import_module("rockygpt_brain.capabilities.transportation.execution")
        interpretation = import_module("rockygpt_brain.capabilities.transportation.interpretation")
    except ModuleNotFoundError as error:
        if error.name in _TRANSPORTATION_MODULE_NAMES:
            return None
        raise
    return _modules(execution, interpretation)


def run_chat(
    messages: Sequence[ConversationMessage], model: str
) -> dict[str, object]:
    """Run chat with transportation when installed and a safe fallback when absent."""
    modules = load_transportation_modules()
    if modules is None:
        return _run_without_transportation(messages, model)
    return _run_with_transportation(messages, model, modules)


def _modules(execution: ModuleType, interpretation: ModuleType) -> _TransportationModules:
    return _TransportationModules(
        interpret=cast(
            Callable[[Sequence[ConversationMessage], str], tuple[str, _Interpretation]],
            interpretation.interpret_transportation,
        ),
        repair=cast(
            Callable[[Sequence[ConversationMessage], str], tuple[str, _Interpretation]],
            interpretation.repair_transportation_interpretation,
        ),
        interpretation_failure=cast(
            Callable[[str], tuple[str, _Interpretation]],
            interpretation.interpretation_failure,
        ),
        load_data=cast(Callable[[], object], execution.load_trusted_shuttle_data),
        route_mentions_match=cast(
            Callable[[object, object], bool],
            execution.route_mentions_match_trusted_data,
        ),
        execute=cast(_Execute, execution.execute_transportation),
        answer=cast(Callable[[_Result], str], execution.answer_transportation),
    )


def _run_with_transportation(
    messages: Sequence[ConversationMessage],
    model: str,
    modules: _TransportationModules,
) -> dict[str, object]:
    answer, interpretation = modules.interpret(messages, model)
    transportation_result: _Result | None = None
    transportation_provenance: _Dumpable | None = None
    if interpretation.selected:
        transportation_request = interpretation.request
        assert transportation_request is not None
        trusted_data = None
        if _request_kind(transportation_request) in {"query", "comparison"}:
            trusted_data = modules.load_data()
            if not modules.route_mentions_match(transportation_request, trusted_data):
                _, interpretation = modules.repair(messages, model)
                transportation_request = interpretation.request
                assert transportation_request is not None
                if (
                    _request_kind(transportation_request) in {"query", "comparison"}
                    and not modules.route_mentions_match(transportation_request, trusted_data)
                ):
                    _, interpretation = modules.interpretation_failure(interpretation.model)
                    transportation_request = interpretation.request
                    assert transportation_request is not None
        transportation_result = modules.execute(
            transportation_request,
            data=trusted_data,
        )
        transportation_provenance = transportation_result.provenance
        answer = modules.answer(transportation_result)
    return _response(
        answer=answer,
        model=interpretation.model,
        interpretation=interpretation.model_dump(mode="json"),
        result=(
            transportation_result.model_dump(mode="json")
            if transportation_result is not None
            else None
        ),
        provenance=(
            transportation_provenance.model_dump(mode="json")
            if transportation_provenance is not None
            else None
        ),
    )


def _run_without_transportation(
    messages: Sequence[ConversationMessage], model: str
) -> dict[str, object]:
    response = OpenAI().responses.create(
        model=model,
        input=cast(ResponseInputParam, list(messages)),
        instructions=TRANSPORTATION_UNAVAILABLE_INSTRUCTIONS,
        tools=[TRANSPORTATION_UNAVAILABLE_TOOL],
        tool_choice="auto",
        parallel_tool_calls=False,
        store=False,
        temperature=0,
    )
    selected = any(
        item.type == "function_call" and item.name == TRANSPORTATION_TOOL_NAME
        for item in response.output
    )
    if not selected:
        return _response(
            answer=response.output_text,
            model=response.model,
            interpretation={"selected": False, "request": None, "model": response.model},
            result=None,
            provenance=None,
        )

    request = {"kind": "unsupported", "reason": "capability_unavailable"}
    return _response(
        answer=TRANSPORTATION_UNAVAILABLE_ANSWER,
        model=response.model,
        interpretation={"selected": True, "request": request, "model": response.model},
        result={
            "outcome": "unsupported",
            "request": request,
            "evaluated_at": datetime.now(_CAMPUS_TIME_ZONE).isoformat(),
            "query_results": [],
            "comparison": None,
            "candidates": [],
            "provenance": None,
        },
        provenance=None,
    )


def _request_kind(request: object) -> object:
    return getattr(request, "kind", None)


def _response(
    *,
    answer: str,
    model: str,
    interpretation: dict[str, object],
    result: dict[str, object] | None,
    provenance: dict[str, object] | None,
) -> dict[str, object]:
    return {
        "answer": answer,
        "model": model,
        "transportationInterpretation": interpretation,
        "transportationResult": result,
        "transportationProvenance": provenance,
    }
