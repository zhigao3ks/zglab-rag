from __future__ import annotations

import asyncio

from zglab_rag.agent import (
    AgentCapabilityExecutor,
    AgentRequest,
    ObservationOrigin,
    ObservationStatus,
    PersonalKnowledgeObservation,
    ToolObservation,
    WebResearchObservation,
)
from zglab_rag.capabilities.contracts import (
    CapabilityMetadata,
    CapabilityResult,
    CapabilityStatus,
    EvidenceOrigin,
)
from zglab_rag.conversation.context import ConversationContext, ConversationContextMessage
from zglab_rag.conversation.models import MessageRole
from zglab_rag.mcp.contracts import MCPToolResult


def result(
    origin: EvidenceOrigin, status: CapabilityStatus = CapabilityStatus.SUCCESS
) -> CapabilityResult:
    return CapabilityResult("fake", status, origin, failure_reason="safe failure")


class FakePersonal:
    metadata = CapabilityMetadata("personal_knowledge", "Personal", "test")

    def __init__(self) -> None:
        self.context = None

    def execute(self, _request, context, **_kwargs):
        self.context = context.conversation_context
        return result(EvidenceOrigin.PERSONAL)


class FakeWeb:
    def answer(self, *_args, **_kwargs):
        return result(EvidenceOrigin.WEB, CapabilityStatus.INSUFFICIENT_EVIDENCE)


class FakeTool:
    def __init__(self, outcome: MCPToolResult) -> None:
        self.outcome = outcome

    async def call_tool(self, *_args, **_kwargs) -> MCPToolResult:
        return self.outcome


def test_explicit_adapters_preserve_origins_and_deterministic_ids() -> None:
    executor = AgentCapabilityExecutor()
    request = AgentRequest("request-1", "question")
    personal = executor.invoke_personal(request, FakePersonal())
    web = executor.invoke_web(request, FakeWeb())

    assert isinstance(personal, PersonalKnowledgeObservation)
    assert personal.observation_id == "O1"
    assert personal.origin == ObservationOrigin.PERSONAL
    assert personal.capability_result is not None
    assert isinstance(web, WebResearchObservation)
    assert web.observation_id == "O2"
    assert web.status == ObservationStatus.INSUFFICIENT_EVIDENCE


def test_tool_observation_never_becomes_evidence() -> None:
    executor = AgentCapabilityExecutor()
    observation = asyncio.run(
        executor.invoke_tool(
            AgentRequest("request-1", "ignored"),
            FakeTool(MCPToolResult("success", "json_format", output={"valid": True})),
            "json_format",
            {"text": "{}"},
        )
    )
    assert isinstance(observation, ToolObservation)
    assert observation.observation_id == "O1"
    assert observation.structured_result == {"valid": True}
    assert not hasattr(observation, "capability_result")
    assert not hasattr(observation, "sources")


def test_agent_context_is_server_derived_data_for_capabilities_only() -> None:
    context = ConversationContext(
        conversation_id=7,
        messages=(ConversationContextMessage(MessageRole.USER, "previous request"),),
        turn_count=0,
        char_count=16,
    )
    request = AgentRequest("request-1", "continue", conversation_context=context)
    personal_capability = FakePersonal()
    AgentCapabilityExecutor().invoke_personal(request, personal_capability)
    assert personal_capability.context == context


def test_tool_error_is_safe_failed_observation() -> None:
    observation = asyncio.run(
        AgentCapabilityExecutor().invoke_tool(
            AgentRequest("request-1", "ignored"),
            FakeTool(
                MCPToolResult(
                    "error", "json_format", error_code="MCP_INVALID_INPUT", error_message="bad"
                )
            ),
            "json_format",
            {},
        )
    )
    assert observation.status == ObservationStatus.FAILED
    assert observation.error_code == "MCP_INVALID_INPUT"
