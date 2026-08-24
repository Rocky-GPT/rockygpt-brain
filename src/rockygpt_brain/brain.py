"""The complete Hybrid V1 turn, intentionally readable as one small pipeline."""

from __future__ import annotations

import asyncio
import time as monotonic_time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from rockygpt_brain.capabilities import (
    CapabilityResult,
    ShuttleCapability,
    shuttle_communication,
    validate_shuttle_communication,
)
from rockygpt_brain.contracts import ChatRequest, ChatSuccess, UiAction, UiActionType
from rockygpt_brain.evidence import EvidenceRegistry, validate_draft
from rockygpt_brain.errors import (
    DataUnavailableError,
    GroundingError,
    ModelOutputError,
    ModelUnavailableError,
    ServiceError,
)
from rockygpt_brain.memory import AssistantClaim, MemorySnapshot
from rockygpt_brain.model import CommunicateInput, ModelPort, UnderstandInput
from rockygpt_brain.persistence import FailedAttempt, Repository, SuccessfulTurn
from rockygpt_brain.planning import AnswerDraft, RouteMode, RoutePlan
from rockygpt_brain.policy import preflight
from rockygpt_brain.security import redact_text
from rockygpt_brain.time_context import TimeContext


@dataclass(frozen=True, slots=True)
class TurnIdentity:
    request_id: str
    session_id: str
    visitor_id: str | None
    safety_identifier: str
    question_origin: str


class Brain:
    def __init__(
        self,
        *,
        model: ModelPort,
        shuttle: ShuttleCapability,
        repository: Repository,
        campus_timezone: str = "America/New_York",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._model = model
        self._shuttle = shuttle
        self._repository = repository
        self._campus_timezone = campus_timezone
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def answer(self, request: ChatRequest, identity: TurnIdentity) -> ChatSuccess:
        started = monotonic_time.monotonic()
        route: str | None = None
        try:
            async with asyncio.timeout(55.0):
                response, route = await self._run(request, identity, started)
                return response
        except ServiceError as exc:
            route = getattr(exc, "brain_route", route)
            await self._record_failure(identity.request_id, exc.code, route, started)
            raise
        except DataUnavailableError as exc:
            route = getattr(exc, "brain_route", route)
            await self._record_failure(
                identity.request_id, "DATASET_UNAVAILABLE", route, started
            )
            raise ServiceError(
                503,
                "DATASET_UNAVAILABLE",
                "Campus data is temporarily unavailable.",
                retryable=True,
            ) from exc
        except (ModelUnavailableError, ModelOutputError, TimeoutError) as exc:
            route = getattr(exc, "brain_route", route)
            await self._record_failure(
                identity.request_id, "SERVICE_UNAVAILABLE", route, started
            )
            raise ServiceError(
                503,
                "SERVICE_UNAVAILABLE",
                "The answer service is temporarily unavailable.",
                retryable=True,
            ) from exc
        except ValueError as exc:
            route = getattr(exc, "brain_route", route)
            await self._record_failure(identity.request_id, "INVALID_REQUEST", route, started)
            raise ServiceError(400, "INVALID_REQUEST", "The request could not be interpreted.") from exc
        except Exception as exc:
            route = getattr(exc, "brain_route", route)
            await self._record_failure(identity.request_id, "INTERNAL_ERROR", route, started)
            raise ServiceError(500, "INTERNAL_ERROR", "An unexpected service error occurred.") from exc

    async def _run(
        self,
        request: ChatRequest,
        identity: TurnIdentity,
        started: float,
    ) -> tuple[ChatSuccess, str]:
        # 1. VALIDATE TIME + LOAD SERVER MEMORY.
        turn_time = TimeContext.create(
            pinned_now=request.now,
            requested_timezone=request.timezone,
            campus_timezone=self._campus_timezone,
            clock=self._clock,
        )
        memory = await self._repository.load_memory(identity.session_id)
        registry = EvidenceRegistry()
        memory.register_conversation_evidence(registry)

        # 2. DETERMINISTIC SAFETY/POLICY PREFLIGHT.
        policy = preflight(request.message, registry)
        if policy.handled and policy.draft is not None:
            return await self._finish(
                request,
                identity,
                policy.draft,
                RoutePlan(mode=RouteMode.POLICY),
                turn_time,
                memory,
                registry,
                capability_results=[],
                started=started,
            )

        # 3. AI #1 — UNDERSTAND. One typed plan, one repair, no tool loop.
        understand_input = UnderstandInput(
            message=request.message,
            client_history=tuple(request.history),
            memory=memory,
            time=turn_time,
            safety_identifier=identity.safety_identifier,
        )
        async with asyncio.timeout(8.0):
            try:
                plan = await self._model.understand(understand_input)
            except ModelOutputError as exc:
                plan = await self._model.understand(understand_input, repair_error=str(exc))

        # 4. EXPLICIT HYBRID DISPATCH — CODE, RAG, MEMORY, GENERAL, OR CLARIFY.
        results: list[dict[str, Any]] = []
        capabilities: list[CapabilityResult] = []
        try:
            if plan.mode in {RouteMode.CAPABILITY, RouteMode.COMPOSITE}:
                async with asyncio.timeout(5.0):
                    for operation in plan.operations:
                        if operation.name != "shuttle":
                            raise ValueError("operation is not allowlisted")
                        result = await self._shuttle.execute(operation.arguments, turn_time, registry)
                        if result.outcome in {"unavailable", "error"}:
                            raise DataUnavailableError("DATA did not complete the shuttle operation")
                        if result.outcome in {"success", "empty", "no_match"} and not result.grounded:
                            raise DataUnavailableError(
                                "authoritative shuttle result omitted source evidence"
                            )
                        capabilities.append(result)
                        required = shuttle_communication(result)
                        results.append(
                            {
                                "kind": "code",
                                "result": result.prompt_payload(),
                                "requiredCommunication": {
                                    "answer": required.answer,
                                    "evidenceIds": list(required.evidence_ids),
                                },
                            }
                        )
            elif plan.mode == RouteMode.CONVERSATION:
                results.append(
                    {
                        "kind": "memory",
                        "outcome": "success" if memory.claims else "no_server_record",
                        "claims": memory.prompt_payload()["assistantClaims"],
                    }
                )
            elif plan.mode == RouteMode.GENERAL:
                results.append({"kind": "general", "scope": "confirmed_non_campus"})
            elif plan.mode == RouteMode.RAG:
                # RAG is intentionally unavailable in this milestone. Do not ask AI #2 to fill the gap.
                draft = AnswerDraft(
                    answer="I can’t verify that policy or document question in this milestone.",
                    route="ungrounded",
                    claims=[],
                    citationEvidenceIds=[],
                    uiActions=[],
                    suggestedQuestions=[],
                )
                return await self._finish(
                    request,
                    identity,
                    draft,
                    plan,
                    turn_time,
                    memory,
                    registry,
                    capability_results=[],
                    started=started,
                )
            elif plan.mode == RouteMode.CLARIFY:
                results.append({"kind": "clarification", "question": plan.clarification})
            elif plan.mode == RouteMode.POLICY:
                draft = AnswerDraft(
                    answer="I can’t help with that request.",
                    route="policy",
                    claims=[],
                    citationEvidenceIds=[],
                    uiActions=[],
                    suggestedQuestions=[],
                )
                return await self._finish(
                    request,
                    identity,
                    draft,
                    plan,
                    turn_time,
                    memory,
                    registry,
                    capability_results=[],
                    started=started,
                )
            else:
                raise ValueError("unsupported route plan")
        except Exception as exc:
            setattr(exc, "brain_route", plan.mode.value)
            raise

        # 5. AI #2 — COMMUNICATE THE TYPED RESULT. It never executes or recomputes CODE.
        communicate_input = CommunicateInput(
            message=request.message,
            plan=plan,
            typed_results=tuple(results),
            evidence=tuple(registry.prompt_payload()),
            memory=memory,
            style_mode=request.style_mode,
            response_mode=request.response_mode,
            safety_identifier=identity.safety_identifier,
        )
        require_grounding = plan.mode in {
            RouteMode.CAPABILITY,
            RouteMode.COMPOSITE,
            RouteMode.CONVERSATION,
        } and not (
            plan.mode == RouteMode.CONVERSATION and not memory.claims
        )
        async with asyncio.timeout(24.0):
            correction: str | None = None
            for attempt in range(2):
                try:
                    draft = await self._model.communicate(
                        communicate_input,
                        correction_error=correction,
                    )
                    validate_draft(
                        draft,
                        registry,
                        plan.mode,
                        require_grounding=require_grounding,
                    )
                    for capability_result in capabilities:
                        if capability_result.name == "shuttle":
                            validate_shuttle_communication(
                                draft, capability_result, registry
                            )
                    break
                except (GroundingError, ModelOutputError) as exc:
                    if attempt == 1:
                        if isinstance(exc, GroundingError):
                            draft = AnswerDraft(
                                answer=(
                                    "I couldn’t produce a fully verified answer from the available "
                                    "campus evidence."
                                ),
                                route="ungrounded",
                                claims=[],
                                citationEvidenceIds=[],
                                uiActions=[],
                                suggestedQuestions=[],
                            )
                            break
                        raise
                    correction = str(exc)

        return await self._finish(
            request,
            identity,
            draft,
            plan,
            turn_time,
            memory,
            registry,
            capability_results=capabilities,
            started=started,
        )

    async def _finish(
        self,
        request: ChatRequest,
        identity: TurnIdentity,
        draft: AnswerDraft,
        plan: RoutePlan,
        turn_time: TimeContext,
        memory: MemorySnapshot,
        registry: EvidenceRegistry,
        *,
        capability_results: list[CapabilityResult],
        started: float,
    ) -> tuple[ChatSuccess, str]:
        del memory  # State was read before planning; only the new successful turn is appended below.
        citations = registry.citations(draft.citation_evidence_ids)
        actions = list(draft.ui_actions)
        shuttle_succeeded = any(
            result.name == "shuttle" and result.outcome in {"success", "empty", "no_match"}
            for result in capability_results
        )
        if shuttle_succeeded and not any(action.type == UiActionType.VIEW_BUS for action in actions):
            actions.append(UiAction(type=UiActionType.VIEW_BUS))

        response = ChatSuccess(
            requestId=identity.request_id,
            answer=draft.answer,
            route=draft.route,
            citations=citations,
            uiActions=actions,
            suggestedQuestions=draft.suggested_questions,
        )
        created_at = turn_time.instant
        claims = tuple(
            AssistantClaim(
                claim_id=f"{identity.request_id}:{index}",
                request_id=identity.request_id,
                text=redact_text(claim.text) or "[REDACTED]",
                evidence_ids=tuple(claim.evidence_ids),
                created_at=created_at,
            )
            for index, claim in enumerate(draft.claims)
        )
        tool_arguments = {
            operation.name: sorted(
                operation.arguments.model_dump(exclude_none=True, by_alias=True).keys()
            )
            for operation in plan.operations
        }
        elapsed_ms = max(0, int((monotonic_time.monotonic() - started) * 1000))
        successful = SuccessfulTurn(
            request_id=identity.request_id,
            session_id=identity.session_id,
            visitor_id=identity.visitor_id,
            user_message=redact_text(request.message) or "[REDACTED]",
            assistant_message=redact_text(draft.answer) or "[REDACTED]",
            route=draft.route,
            question_origin=identity.question_origin,
            tools_invoked=tuple(operation.name for operation in plan.operations),
            tool_arguments=tool_arguments,
            citations=tuple(citations),
            claims=claims,
            evidence_snapshot=tuple(registry.prompt_payload()),
            latency_ms=elapsed_ms,
            created_at=created_at,
        )
        # Repository implementations must commit answer log, evidence snapshot, and memory atomically.
        async with asyncio.timeout(3.0):
            await self._repository.commit_success(successful)
        return response, draft.route

    async def _record_failure(
        self,
        request_id: str,
        code: str,
        route: str | None,
        started: float,
    ) -> None:
        attempt = FailedAttempt(
            request_id=request_id,
            safe_error_code=code,
            route=route,
            latency_ms=max(0, int((monotonic_time.monotonic() - started) * 1000)),
            created_at=self._clock().astimezone(timezone.utc),
        )
        try:
            async with asyncio.timeout(3.0):
                await self._repository.record_failure(attempt)
        except Exception:
            # Failure logging cannot replace the original safe public error.
            return
