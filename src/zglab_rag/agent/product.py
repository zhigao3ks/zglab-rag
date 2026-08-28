"""Phase 14D internal product adapter for the bounded Agent runtime."""

from __future__ import annotations

import asyncio

from zglab_rag.agent import (
    AgentAnswer,
    AgentAnswerStatus,
    AgentRequest,
    AgentSynthesizer,
    BoundedAgentExecutor,
    BoundedPlanner,
)
from zglab_rag.capabilities.contracts import CapabilityResult, CapabilityStatus
from zglab_rag.generation.contracts import (
    EvidenceOrigin,
    GroundedAnswer,
    ProgressCallback,
    ProgressStage,
)
from zglab_rag.mcp.contracts import MCPToolResult


class _UnavailableWeb:
    def answer(self, *_args, **_kwargs) -> CapabilityResult:
        return CapabilityResult(
            "web_research",
            CapabilityStatus.FAILED,
            EvidenceOrigin.WEB,
            failure_reason="web unavailable",
        )


class _UnavailableTools:
    async def call_tool(self, tool_id: str, _arguments: dict) -> MCPToolResult:
        return MCPToolResult("error", tool_id, error_code="MCP_DISABLED", error_message="disabled")


class _DeterministicMultiSynthesis:
    """Safe fallback that combines already-validated capability answers.

    It never creates a URL, source or citation and never receives a mutable
    plan. A future LLM synthesizer may replace this injected boundary only
    while preserving the same source validation contract.
    """

    def synthesize(self, *, question, observations, allowed_sources) -> GroundedAnswer:
        del question, allowed_sources
        parts: list[str] = []
        for observation in observations:
            result = getattr(observation, "capability_result", None)
            generation = result.generation if result else None
            if generation is not None:
                parts.append(generation.answer.answer)
        return GroundedAnswer(answer="\n\n".join(parts) or "证据不足以完成回答。")


def execute_agent(
    runtime,
    question: str,
    progress: ProgressCallback | None = None,
    *,
    request_id: str,
    principal,
) -> AgentAnswer:
    """Run one frozen deterministic plan through existing bounded adapters."""
    request = AgentRequest(request_id=request_id, question=question, principal=principal)
    if progress is not None:
        progress(ProgressStage.PLANNING)
    plan = BoundedPlanner().plan(request)
    personal = runtime.capability_registry.get("personal_knowledge")
    web = getattr(runtime, "web_research_skill", None) or _UnavailableWeb()
    tools = getattr(runtime, "mcp_tool_runtime", None) or _UnavailableTools()
    executor = BoundedAgentExecutor(
        personal=personal,
        web=web,
        tools=tools,
        deadline_seconds=runtime.settings.agent_overall_deadline_seconds,
    )
    if progress is not None:
        progress(ProgressStage.EXECUTING)
    execution = asyncio.run(executor.execute(request, plan))
    if progress is not None:
        progress(ProgressStage.SYNTHESIZING)
    answer = AgentSynthesizer(_DeterministicMultiSynthesis()).synthesize(
        request, plan, execution.observations
    )
    if progress is not None:
        progress(ProgressStage.VALIDATING)
    if execution.error_code is not None and answer.status == AgentAnswerStatus.FAILED:
        return AgentAnswer(
            AgentAnswerStatus.FAILED,
            "请求超过 Agent 执行时限。",
            answer.observations,
            answer.sources,
            execution.error_code.value,
        )
    return answer
