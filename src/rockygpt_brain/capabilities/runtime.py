"""Discover and run optional Brain capabilities without naming any one of them."""

import os
from collections.abc import Iterable, Sequence
from datetime import datetime
from importlib import import_module
from pathlib import Path
from pkgutil import iter_modules
from typing import cast
from zoneinfo import ZoneInfo

from openai import OpenAI
from openai.types.responses import FunctionToolParam, ResponseInputParam

from rockygpt_brain.capabilities.base import (
    Capability,
    CapabilityRun,
    ConversationMessage,
)

EXPECTED_CAPABILITIES_ENV = "ROCKYGPT_EXPECTED_CAPABILITIES"
_CAMPUS_TIME_ZONE = ZoneInfo("America/New_York")
_PACKAGE_DIRECTORY = Path(__file__).parent
_NON_CAPABILITY_MODULES = frozenset({"base", "runtime"})


def discover_capabilities() -> tuple[list[Capability], list[str]]:
    """Discover capability packages and report configured packages that cannot load."""
    discovered = {
        module.name
        for module in iter_modules([str(_PACKAGE_DIRECTORY)])
        if module.ispkg and module.name not in _NON_CAPABILITY_MODULES
    }
    expected = _expected_capabilities(os.getenv(EXPECTED_CAPABILITIES_ENV, ""))
    available: list[Capability] = []
    unavailable: list[str] = []
    for name in sorted(discovered | expected):
        try:
            package = import_module(f"rockygpt_brain.capabilities.{name}")
        except ModuleNotFoundError:
            unavailable.append(name)
            continue
        capability = cast(Capability | None, getattr(package, "CAPABILITY", None))
        if capability is None or capability.name != name:
            unavailable.append(name)
            continue
        available.append(capability)
    return available, unavailable


def run_chat(
    messages: Sequence[ConversationMessage], model: str
) -> dict[str, object]:
    """Run dynamically discovered capabilities, then fall back to normal chat."""
    capabilities, unavailable = discover_capabilities()
    inspections: dict[str, object] = {}
    normal: CapabilityRun | None = None

    for capability in capabilities:
        result = capability.run(messages, model)
        inspections.update(result.inspection)
        if result.selected:
            return _response(result.answer, result.model, inspections)
        normal = normal or result

    if unavailable:
        result = _run_unavailable_selector(messages, model, unavailable)
        inspections.update(result.inspection)
        if result.selected:
            return _response(result.answer, result.model, inspections)
        normal = normal or result

    if normal is None:
        normal = _run_normal_chat(messages, model)
    return _response(normal.answer, normal.model, inspections)


def _expected_capabilities(value: str) -> set[str]:
    names = {name.strip() for name in value.split(",") if name.strip()}
    invalid = sorted(name for name in names if not name.isidentifier())
    if invalid:
        raise RuntimeError(
            f"{EXPECTED_CAPABILITIES_ENV} contains invalid names: {', '.join(invalid)}"
        )
    return names


def _run_unavailable_selector(
    messages: Sequence[ConversationMessage], model: str, names: Sequence[str]
) -> CapabilityRun:
    tools = [_unavailable_tool(name) for name in names]
    response = OpenAI().responses.create(
        model=model,
        input=cast(ResponseInputParam, list(messages)),
        instructions=(
            "For a request belonging to an unavailable RockyGPT campus capability, call the "
            "matching tool instead of answering. For any other request, answer normally. "
            "Do not invent facts for unavailable capabilities."
        ),
        tools=tools,
        tool_choice="auto",
        parallel_tool_calls=False,
        store=False,
        temperature=0,
    )
    calls = [item for item in response.output if item.type == "function_call"]
    selected_name = next(
        (
            name
            for name in names
            if any(call.name == _tool_name(name) for call in calls)
        ),
        None,
    )
    inspection = _unavailable_inspection(names, selected_name, response.model)
    if selected_name is None:
        return CapabilityRun(
            selected=False,
            answer=response.output_text,
            model=response.model,
            inspection=inspection,
        )
    return CapabilityRun(
        selected=True,
        answer=f"Campus {_display_name(selected_name)} is temporarily unavailable.",
        model=response.model,
        inspection=inspection,
    )


def _run_normal_chat(
    messages: Sequence[ConversationMessage], model: str
) -> CapabilityRun:
    response = OpenAI().responses.create(
        model=model,
        input=cast(ResponseInputParam, list(messages)),
        store=False,
    )
    return CapabilityRun(
        selected=False,
        answer=response.output_text,
        model=response.model,
        inspection={},
    )


def _unavailable_tool(name: str) -> FunctionToolParam:
    display_name = _display_name(name)
    return cast(
        FunctionToolParam,
        {
            "type": "function",
            "name": _tool_name(name),
            "description": (
                f"Select for a request about the unavailable RockyGPT campus {display_name} "
                "capability."
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


def _unavailable_inspection(
    names: Iterable[str], selected_name: str | None, model: str
) -> dict[str, object]:
    inspection: dict[str, object] = {}
    for name in names:
        selected = name == selected_name
        request = (
            {"kind": "unsupported", "reason": "capability_unavailable"}
            if selected
            else None
        )
        prefix = _lower_camel(name)
        inspection[f"{prefix}Interpretation"] = {
            "selected": selected,
            "request": request,
            "model": model,
        }
        inspection[f"{prefix}Result"] = (
            {
                "outcome": "unsupported",
                "request": request,
                "evaluated_at": datetime.now(_CAMPUS_TIME_ZONE).isoformat(),
                "query_results": [],
                "comparison": None,
                "candidates": [],
                "provenance": None,
            }
            if selected
            else None
        )
        inspection[f"{prefix}Provenance"] = None
    return inspection


def _tool_name(name: str) -> str:
    return f"campus_{name}"


def _display_name(name: str) -> str:
    return name.replace("_", " ")


def _lower_camel(name: str) -> str:
    first, *rest = name.split("_")
    return first + "".join(part.capitalize() for part in rest)


def _response(
    answer: str, model: str, inspection: dict[str, object]
) -> dict[str, object]:
    return {"answer": answer, "model": model, **inspection}
