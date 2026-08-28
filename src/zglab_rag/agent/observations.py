"""Explicit Phase 14A adapters from existing capabilities to observations."""

from __future__ import annotations

from typing import Protocol

from zglab_rag.agent.contracts import (
    AgentRequest,
    ObservationOrigin,
    ObservationStatus,
    PersonalKnowledgeObservation,
    ToolObservation,
    WebResearchObservation,
)
from zglab_rag.capabilities.contracts import (
    Capability,
    CapabilityContext,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
)
from zglab_rag.mcp.contracts import MCPToolResult


class WebAnswerCapability(Protocol):
    def answer(
        self, request: CapabilityRequest, context: CapabilityContext, *, progress=None
    ) -> CapabilityResult: ...


class ToolCapability(Protocol):
    async def call_tool(self, tool_id: str, arguments: dict) -> MCPToolResult: ...


def _status(result: CapabilityResult) -> ObservationStatus:
    return {
        CapabilityStatus.SUCCESS: ObservationStatus.SUCCESS,
        CapabilityStatus.INSUFFICIENT_EVIDENCE: ObservationStatus.INSUFFICIENT_EVIDENCE,
        CapabilityStatus.FAILED: ObservationStatus.FAILED,
    }[result.status]


class AgentCapabilityExecutor:
    """A deterministic adapter facade; callers select every invocation explicitly."""

    def __init__(self) -> None:
        self._next_id = 1

    def _id(self) -> str:
        value = f"O{self._next_id}"
        self._next_id += 1
        return value

    @staticmethod
    def _context(request: AgentRequest) -> CapabilityContext:
        return CapabilityContext(request_id=request.request_id, principal=request.principal)

    def invoke_personal(
        self, request: AgentRequest, capability: Capability
    ) -> PersonalKnowledgeObservation:
        try:
            result = capability.execute(CapabilityRequest(request.question), self._context(request))
        except Exception:
            return PersonalKnowledgeObservation(
                self._id(), ObservationOrigin.PERSONAL, ObservationStatus.FAILED,
                summary="personal capability failed",
            )
        return PersonalKnowledgeObservation(
            self._id(), ObservationOrigin.PERSONAL, _status(result),
            summary=result.failure_reason, capability_result=result,
        )

    def invoke_web(
        self, request: AgentRequest, capability: WebAnswerCapability
    ) -> WebResearchObservation:
        try:
            result = capability.answer(CapabilityRequest(request.question), self._context(request))
        except Exception:
            return WebResearchObservation(
                self._id(), ObservationOrigin.WEB, ObservationStatus.FAILED,
                summary="web capability failed",
            )
        return WebResearchObservation(
            self._id(), ObservationOrigin.WEB, _status(result),
            summary=result.failure_reason, capability_result=result,
        )

    async def invoke_tool(
        self, request: AgentRequest, capability: ToolCapability, tool_id: str, arguments: dict
    ) -> ToolObservation:
        del request  # Tool input is explicit; no routing or question-derived arguments.
        try:
            result = await capability.call_tool(tool_id, arguments)
        except Exception:
            return ToolObservation(
                self._id(), ObservationOrigin.TOOL, ObservationStatus.FAILED,
                summary="tool capability failed", tool_id=tool_id,
            )
        if result.status == "success":
            return ToolObservation(
                self._id(), ObservationOrigin.TOOL, ObservationStatus.SUCCESS,
                tool_id=tool_id, structured_result=result.output,
            )
        return ToolObservation(
            self._id(), ObservationOrigin.TOOL, ObservationStatus.FAILED,
            summary="tool returned a safe error", tool_id=tool_id,
            error_code=result.error_code, error_message=result.error_message,
        )
