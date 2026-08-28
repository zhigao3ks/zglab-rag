"""Phase 14C bounded, sequential AgentPlan execution."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from enum import StrEnum
from time import monotonic

from pydantic import ValidationError

from zglab_rag.agent.contracts import (
    AgentObservation,
    AgentRequest,
    ObservationOrigin,
    ObservationStatus,
    ToolObservation,
)
from zglab_rag.agent.errors import AgentErrorCode
from zglab_rag.agent.observations import (
    AgentCapabilityExecutor,
    ToolCapability,
    WebAnswerCapability,
)
from zglab_rag.agent.planning import AgentPlan, PlanStatus, PlanStep, PlanStepType
from zglab_rag.capabilities.contracts import Capability
from zglab_rag.mcp.policy import MCP_TOOL_ALLOWLIST


class ExecutionStatus(StrEnum):
    COMPLETED = "completed"
    NEEDS_INPUT = "needs_input"
    INVALID_PLAN = "invalid_plan"
    DEADLINE_EXCEEDED = "deadline_exceeded"


@dataclass(frozen=True, slots=True)
class ExecutionTrace:
    """Safe audit data only: no prompts, evidence bodies or tool payloads."""

    plan_id: str
    step_id: str
    capability: PlanStepType
    status: ObservationStatus
    duration_ms: int
    observation_id: str
    tool_id: str | None = None


@dataclass(frozen=True, slots=True)
class AgentExecution:
    status: ExecutionStatus
    observations: tuple[AgentObservation, ...]
    trace: tuple[ExecutionTrace, ...]
    error_code: AgentErrorCode | None = None


class BoundedAgentExecutor:
    """Executes one frozen plan once through Phase 14A adapters.

    This class deliberately has no routing, retrieval, search, retry or
    replanning behaviour. It repeats planner limits at its trust boundary.
    """

    def __init__(
        self,
        *,
        personal: Capability,
        web: WebAnswerCapability,
        tools: ToolCapability,
        adapter: AgentCapabilityExecutor | None = None,
        deadline_seconds: float = 30.0,
    ) -> None:
        if deadline_seconds <= 0:
            raise ValueError("deadline_seconds must be positive")
        self._personal = personal
        self._web = web
        self._tools = tools
        self._adapter = adapter or AgentCapabilityExecutor()
        self._deadline_seconds = deadline_seconds

    @staticmethod
    def _validated_plan(plan: AgentPlan, request: AgentRequest) -> AgentPlan | None:
        """Reject model_construct and cross-request plans before any execution."""
        try:
            validated = AgentPlan.model_validate(plan.model_dump())
        except (AttributeError, ValidationError, TypeError, ValueError):
            return None
        if validated.request_id != request.request_id:
            return None
        return validated

    @staticmethod
    def _blocked(step: PlanStep, observation_id: str) -> AgentObservation:
        origin = {
            PlanStepType.PERSONAL: ObservationOrigin.PERSONAL,
            PlanStepType.WEB: ObservationOrigin.WEB,
            PlanStepType.TOOL: ObservationOrigin.TOOL,
        }[step.type]
        if step.type == PlanStepType.TOOL:
            return ToolObservation(
                observation_id,
                origin,
                ObservationStatus.BLOCKED,
                summary="dependency did not succeed",
                step_id=step.step_id,
                tool_id=step.tool_id or "",
            )
        return AgentObservation(
            observation_id,
            origin,
            ObservationStatus.BLOCKED,
            summary="dependency did not succeed",
            step_id=step.step_id,
        )

    async def execute(self, request: AgentRequest, plan: AgentPlan) -> AgentExecution:
        validated = self._validated_plan(plan, request)
        if validated is None:
            return AgentExecution(
                ExecutionStatus.INVALID_PLAN, (), (), AgentErrorCode.INVALID_OBSERVATION
            )
        if validated.status == PlanStatus.NEEDS_INPUT:
            return AgentExecution(ExecutionStatus.NEEDS_INPUT, (), ())
        if validated.status != PlanStatus.READY:
            return AgentExecution(
                ExecutionStatus.INVALID_PLAN, (), (), AgentErrorCode.INVALID_OBSERVATION
            )

        observations: list[AgentObservation] = []
        trace: list[ExecutionTrace] = []
        by_step: dict[str, AgentObservation] = {}
        started = monotonic()
        plan_id = validated.request_id

        for step in validated.steps:
            elapsed = monotonic() - started
            if elapsed >= self._deadline_seconds:
                return AgentExecution(
                    ExecutionStatus.DEADLINE_EXCEEDED,
                    tuple(observations),
                    tuple(trace),
                    AgentErrorCode.DEADLINE_EXCEEDED,
                )
            step_started = monotonic()
            if any(
                by_step[dependency].status != ObservationStatus.SUCCESS
                for dependency in step.depends_on
            ):
                observation = self._blocked(step, f"O{len(observations) + 1}")
            else:
                remaining = self._deadline_seconds - elapsed
                try:
                    observation = await asyncio.wait_for(
                        self._execute_step(request, step), timeout=remaining
                    )
                except TimeoutError:
                    return AgentExecution(
                        ExecutionStatus.DEADLINE_EXCEEDED,
                        tuple(observations),
                        tuple(trace),
                        AgentErrorCode.DEADLINE_EXCEEDED,
                    )
            observation = replace(observation, step_id=step.step_id)
            observations.append(observation)
            by_step[step.step_id] = observation
            trace.append(
                ExecutionTrace(
                    plan_id=plan_id,
                    step_id=step.step_id,
                    capability=step.type,
                    tool_id=step.tool_id,
                    status=observation.status,
                    duration_ms=int((monotonic() - step_started) * 1000),
                    observation_id=observation.observation_id,
                )
            )
        return AgentExecution(ExecutionStatus.COMPLETED, tuple(observations), tuple(trace))

    async def _execute_step(self, request: AgentRequest, step: PlanStep) -> AgentObservation:
        if step.type == PlanStepType.PERSONAL:
            return await asyncio.to_thread(self._adapter.invoke_personal, request, self._personal)
        if step.type == PlanStepType.WEB:
            return await asyncio.to_thread(self._adapter.invoke_web, request, self._web)
        if step.tool_id not in MCP_TOOL_ALLOWLIST or step.tool_input is None:
            return ToolObservation(
                "",
                ObservationOrigin.TOOL,
                ObservationStatus.FAILED,
                summary="tool input is required",
                tool_id=step.tool_id or "",
            )
        return await self._adapter.invoke_tool(request, self._tools, step.tool_id, step.tool_input)
