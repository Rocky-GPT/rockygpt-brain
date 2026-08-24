"""The complete Hybrid V1 turn, intentionally readable as one small pipeline."""

from __future__ import annotations

import asyncio
import time as monotonic_time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from rockygpt_brain.capabilities import (
    CapabilityResult,
    ShuttleCapability,
    combined_shuttle_communication,
    shuttle_communication,
    validate_shuttle_communication,
)
from rockygpt_brain.contracts import ChatRequest, ChatSuccess, UiAction, UiActionType
from rockygpt_brain.errors import (
    DataUnavailableError,
    GroundingError,
    ModelOutputError,
    ModelUnavailableError,
    ServiceError,
)
from rockygpt_brain.evidence import EvidenceRegistry, validate_draft
from rockygpt_brain.memory import AssistantClaim, MemorySnapshot, is_conversation_recall
from rockygpt_brain.model import CommunicateInput, ModelPort, UnderstandInput
from rockygpt_brain.persistence import FailedAttempt, Repository, SuccessfulTurn
from rockygpt_brain.planning import AnswerDraft, RouteMode, RoutePlan
from rockygpt_brain.policy import preflight, validate_post_generation
from rockygpt_brain.security import redact_text
from rockygpt_brain.time_context import TimeContext

TURN_BUDGET_SECONDS = 55.0
UNDERSTAND_BUDGET_SECONDS = 8.0
CAPABILITY_BUDGET_SECONDS = 5.0
COMMUNICATE_BUDGET_SECONDS = 24.0
PERSISTENCE_BUDGET_SECONDS = 3.0


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
        self._clock = clock or (lambda: datetime.now(UTC))

    async def answer(self, request: ChatRequest, identity: TurnIdentity) -> ChatSuccess:
        started = monotonic_time.monotonic()
        route: str | None = None
        try:
            async with asyncio.timeout(TURN_BUDGET_SECONDS):
                response, route = await self._run(request, identity, started)
                return response
        except ServiceError as exc:
            route = getattr(exc, "brain_route", route)
            await self._record_failure(identity.request_id, exc.code, route, started)
            raise
        except DataUnavailableError as exc:
            route = getattr(exc, "brain_route", route)
            await self._record_failure(identity.request_id, "DATASET_UNAVAILABLE", route, started)
            raise ServiceError(
                503,
                "DATASET_UNAVAILABLE",
                "Campus data is temporarily unavailable.",
                retryable=True,
            ) from exc
        except (ModelUnavailableError, ModelOutputError, TimeoutError) as exc:
            route = getattr(exc, "brain_route", route)
            await self._record_failure(identity.request_id, "SERVICE_UNAVAILABLE", route, started)
            raise ServiceError(
                503,
                "SERVICE_UNAVAILABLE",
                "The answer service is temporarily unavailable.",
                retryable=True,
            ) from exc
        except ValueError as exc:
            route = getattr(exc, "brain_route", route)
            await self._record_failure(identity.request_id, "INVALID_REQUEST", route, started)
            raise ServiceError(
                400, "INVALID_REQUEST", "The request could not be interpreted."
            ) from exc
        except Exception as exc:
            route = getattr(exc, "brain_route", route)
            await self._record_failure(identity.request_id, "INTERNAL_ERROR", route, started)
            raise ServiceError(
                500, "INTERNAL_ERROR", "An unexpected service error occurred."
            ) from exc

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
        async with asyncio.timeout(UNDERSTAND_BUDGET_SECONDS):
            try:
                plan = await self._model.understand(understand_input)
            except ModelOutputError as exc:
                plan = await self._model.understand(understand_input, repair_error=str(exc))
        # Exact questions about Rocky's prior utterance are ledger reads. This deterministic
        # validator prevents a plausible but incorrect AI #1 capability route from querying
        # current campus data instead of answering conversation truth.
        if memory.claims and is_conversation_recall(request.message):
            plan = RoutePlan(
                mode=RouteMode.CONVERSATION,
                contextReferences=[memory.claims[-1].claim_id],
            )

        # 4. EXPLICIT HYBRID DISPATCH — CODE, RAG, MEMORY, GENERAL, OR CLARIFY.
        results: list[dict[str, Any]] = []
        capabilities: list[CapabilityResult] = []
        communication_memory = MemorySnapshot()
        try:
            if plan.mode in {RouteMode.CAPABILITY, RouteMode.COMPOSITE}:
                async with asyncio.timeout(CAPABILITY_BUDGET_SECONDS):
                    for operation in plan.operations:
                        if operation.name != "shuttle":
                            raise ValueError("operation is not allowlisted")
                        result = await self._shuttle.execute(
                            operation.arguments, turn_time, registry
                        )
                        if result.outcome in {"unavailable", "error"}:
                            raise DataUnavailableError(
                                "DATA did not complete the shuttle operation"
                            )
                        if (
                            result.outcome in {"success", "empty", "no_match"}
                            and not result.grounded
                        ):
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
                communication_memory = memory.select_for_communication(plan.context_references)
                communication_memory.register_conversation_evidence(registry)
                results.append(
                    {
                        "kind": "memory",
                        "outcome": (
                            "success" if communication_memory.claims else "no_server_record"
                        ),
                        "claims": communication_memory.prompt_payload()["assistantClaims"],
                    }
                )
            elif plan.mode == RouteMode.GENERAL:
                results.append({"kind": "general", "scope": "confirmed_non_campus"})
            elif plan.mode == RouteMode.RAG:
                # RAG is unavailable in this milestone; AI #2 must not fill the gap.
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
            exc.__dict__["brain_route"] = plan.mode.value
            raise

        # 5. AI #2 — COMMUNICATE THE TYPED RESULT. It never executes or recomputes CODE.
        communicate_input = CommunicateInput(
            message=request.message,
            plan=plan,
            typed_results=tuple(results),
            evidence=tuple(registry.prompt_payload()),
            memory=communication_memory,
            style_mode=request.style_mode,
            response_mode=request.response_mode,
            safety_identifier=identity.safety_identifier,
        )
        require_grounding = plan.mode in {
            RouteMode.CAPABILITY,
            RouteMode.COMPOSITE,
            RouteMode.CONVERSATION,
        } and not (plan.mode == RouteMode.CONVERSATION and not communication_memory.claims)
        async with asyncio.timeout(COMMUNICATE_BUDGET_SECONDS):
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
                    validate_post_generation(draft)
                    shuttle_results = [item for item in capabilities if item.name == "shuttle"]
                    if shuttle_results:
                        validate_shuttle_communication(draft, shuttle_results, registry)
                    break
                except (GroundingError, ModelOutputError) as exc:
                    if attempt == 1:
                        if capabilities:
                            required = combined_shuttle_communication(capabilities)
                            public_ids = [
                                evidence_id
                                for evidence_id in required.evidence_ids
                                if (item := registry.get(evidence_id)) is not None
                                and item.url is not None
                            ]
                            draft = AnswerDraft(
                                answer=required.answer,
                                route="standard",
                                claims=[
                                    {
                                        "text": required.answer,
                                        "kind": "campus",
                                        "evidenceIds": list(required.evidence_ids),
                                    }
                                ],
                                citationEvidenceIds=public_ids[:3],
                                uiActions=[],
                                suggestedQuestions=[],
                            )
                            validate_draft(
                                draft,
                                registry,
                                plan.mode,
                                require_grounding=True,
                            )
                            validate_post_generation(draft)
                            validate_shuttle_communication(draft, capabilities, registry)
                            break
                        draft = AnswerDraft(
                            answer="I couldn’t produce a fully verified answer.",
                            route="ungrounded",
                            claims=[],
                            citationEvidenceIds=[],
                            uiActions=[],
                            suggestedQuestions=[],
                        )
                        break
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
        del (
            memory
        )  # State was read before planning; only the new successful turn is appended below.
        citations = registry.citations(draft.citation_evidence_ids)
        shuttle_succeeded = any(
            result.name == "shuttle" and result.outcome in {"success", "empty", "no_match"}
            for result in capability_results
        )
        actions = (
            [UiAction(type=UiActionType.VIEW_BUS)] if shuttle_succeeded else list(draft.ui_actions)
        )

        response = ChatSuccess(
            request_id=identity.request_id,
            answer=draft.answer,
            route=draft.route,
            citations=citations,
            ui_actions=actions,
            suggested_questions=draft.suggested_questions,
        )
        # Pinned request time controls campus semantics only. Retention and log ordering
        # always use the trusted server clock so callers cannot extend or erase storage.
        created_at = self._clock().astimezone(UTC)
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
        tool_arguments: dict[str, Any] = {
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
        # The repository commits answer log, evidence snapshot, and memory atomically.
        async with asyncio.timeout(PERSISTENCE_BUDGET_SECONDS):
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
            created_at=self._clock().astimezone(UTC),
        )
        try:
            async with asyncio.timeout(PERSISTENCE_BUDGET_SECONDS):
                await self._repository.record_failure(attempt)
        except Exception:
            # Failure logging cannot replace the original safe public error.
            return
