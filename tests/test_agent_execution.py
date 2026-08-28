from __future__ import annotations

import asyncio
import time

from zglab_rag.agent import (
    AgentAnswerStatus,
    AgentCapabilityExecutor,
    AgentPlan,
    AgentRequest,
    AgentSynthesizer,
    BoundedAgentExecutor,
    BoundedPlanner,
    ExecutionStatus,
    ObservationStatus,
    PlanStatus,
    PlanStep,
    PlanStepType,
)
from zglab_rag.capabilities.contracts import (
    CapabilityMetadata,
    CapabilityResult,
    CapabilityStatus,
    EvidenceOrigin,
)
from zglab_rag.generation.contracts import (
    AnswerSource,
    GeneratedClaim,
    GenerationDiagnostics,
    GenerationResult,
    GenerationStatus,
    GroundedAnswer,
)
from zglab_rag.mcp.contracts import MCPToolResult


def generation(question: str, origin: EvidenceOrigin, text: str) -> GenerationResult:
    source = AnswerSource(
        evidence_id="E1" if origin == EvidenceOrigin.PERSONAL else "E2",
        source_id="source",
        title="source",
        source_path="https://example.test/source",
        origin=origin,
        url="https://example.test/source" if origin == EvidenceOrigin.WEB else None,
        domain="example.test" if origin == EvidenceOrigin.WEB else None,
    )
    return GenerationResult(
        status=GenerationStatus.ANSWERED,
        question=question,
        answer=GroundedAnswer(
            answer=text,
            claims=[GeneratedClaim(text=text, citations=[source.evidence_id])],
            sources=[source],
        ),
        diagnostics=GenerationDiagnostics(
            retrieval_mode="fake",
            retrieval_top_k=1,
            evidence_count=1,
            retrieval_latency_ms=0,
            total_latency_ms=0,
        ),
    )


class FakePersonal:
    metadata = CapabilityMetadata("personal_knowledge", "Personal", "test")

    def __init__(self, *, fail: bool = False, delay: float = 0) -> None:
        self.calls = 0
        self.fail = fail
        self.delay = delay

    def execute(self, request, _context, **_kwargs):
        self.calls += 1
        time.sleep(self.delay)
        return CapabilityResult(
            "personal_knowledge",
            CapabilityStatus.FAILED if self.fail else CapabilityStatus.SUCCESS,
            EvidenceOrigin.PERSONAL,
            None
            if self.fail
            else generation(request.question, EvidenceOrigin.PERSONAL, "个人回答"),
            "safe failure" if self.fail else None,
        )


class FakeWeb:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def answer(self, request, _context, **_kwargs):
        self.calls += 1
        return CapabilityResult(
            "web_research",
            CapabilityStatus.FAILED if self.fail else CapabilityStatus.SUCCESS,
            EvidenceOrigin.WEB,
            None if self.fail else generation(request.question, EvidenceOrigin.WEB, "网页回答"),
            "safe failure" if self.fail else None,
        )


class FakeTools:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    async def call_tool(self, tool_id, arguments):
        self.calls += 1
        if self.fail:
            return MCPToolResult("error", tool_id, error_code="MCP_FAILED", error_message="safe")
        return MCPToolResult("success", tool_id, output={"result": arguments["text"]})


class FakeMultiSynthesis:
    def __init__(self) -> None:
        self.calls = 0

    def synthesize(self, *, question, observations, allowed_sources):
        self.calls += 1
        assert question
        assert len(observations) == 2
        return GroundedAnswer(
            answer="合并回答",
            claims=[
                GeneratedClaim(
                    text="合并回答", citations=[source.evidence_id for source in allowed_sources]
                )
            ],
        )


def executor(personal=None, web=None, tools=None, deadline=1) -> BoundedAgentExecutor:
    return BoundedAgentExecutor(
        personal=personal or FakePersonal(),
        web=web or FakeWeb(),
        tools=tools or FakeTools(),
        adapter=AgentCapabilityExecutor(),
        deadline_seconds=deadline,
    )


def test_internal_harness_single_personal_web_and_tool_reuse_results() -> None:
    request = AgentRequest("r1", "我最近做了哪些项目？")
    personal_execution = asyncio.run(executor().execute(request, BoundedPlanner().plan(request)))
    personal_answer = AgentSynthesizer().synthesize(
        request, BoundedPlanner().plan(request), personal_execution.observations
    )
    assert personal_execution.status == ExecutionStatus.COMPLETED
    assert personal_answer.status == AgentAnswerStatus.ANSWERED
    assert personal_answer.answer == "个人回答"

    web_request = AgentRequest("r2", "Python current version")
    web_plan = BoundedPlanner().plan(web_request)
    web_answer = AgentSynthesizer().synthesize(
        web_request,
        web_plan,
        asyncio.run(executor().execute(web_request, web_plan)).observations,
    )
    assert web_answer.answer == "网页回答"
    assert web_answer.sources[0].url == "https://example.test/source"

    tool_request = AgentRequest("r3", "JSON 格式化：hello")
    tool_plan = BoundedPlanner().plan(tool_request)
    tool_answer = AgentSynthesizer().synthesize(
        tool_request,
        tool_plan,
        asyncio.run(executor().execute(tool_request, tool_plan)).observations,
    )
    assert '"result": "hello"' in tool_answer.answer
    assert tool_answer.sources == ()


def test_personal_then_web_is_sequential_and_only_multi_uses_synthesis() -> None:
    request = AgentRequest("r", "我的项目和当前主流架构相比有什么区别？")
    plan = BoundedPlanner().plan(request)
    execution = asyncio.run(executor().execute(request, plan))
    synthesis = FakeMultiSynthesis()
    answer = AgentSynthesizer(synthesis).synthesize(request, plan, execution.observations)
    assert [item.step_id for item in execution.observations] == ["S1", "S2"]
    assert [item.observation_id for item in execution.observations] == ["O1", "O2"]
    assert synthesis.calls == 1
    assert answer.answer == "合并回答"
    assert {source.evidence_id for source in answer.sources} == {"E1", "E2"}


def test_executor_blocks_dependencies_and_does_not_fallback_or_retry() -> None:
    personal = FakePersonal(fail=True)
    web = FakeWeb()
    tools = FakeTools(fail=True)
    plan = AgentPlan(
        request_id="r",
        reason_code="test",
        steps=(
            PlanStep(step_id="S1", type=PlanStepType.PERSONAL, intent="x"),
            PlanStep(step_id="S2", type=PlanStepType.WEB, intent="x", depends_on=("S1",)),
        ),
    )
    execution = asyncio.run(executor(personal, web, tools).execute(AgentRequest("r", "x"), plan))
    assert personal.calls == 1
    assert web.calls == 0
    assert execution.observations[1].status == ObservationStatus.BLOCKED

    tool_request = AgentRequest("tool", "x")
    tool_plan = AgentPlan(
        request_id="tool",
        reason_code="x",
        steps=(
            PlanStep(
                step_id="S1",
                type=PlanStepType.TOOL,
                intent="x",
                tool_id="json_format",
                tool_input={"text": "x"},
            ),
        ),
    )
    tool_execution = asyncio.run(executor(tools=tools).execute(tool_request, tool_plan))
    assert tool_execution.observations[0].status == ObservationStatus.FAILED
    assert tools.calls == 1


def test_executor_rejects_constructed_invalid_plan_and_deadline_stops_following_steps() -> None:
    invalid = AgentPlan.model_construct(
        request_id="r",
        status=PlanStatus.READY,
        reason_code="bad",
        steps=tuple(
            PlanStep.model_construct(step_id=f"S{i}", type=PlanStepType.PERSONAL, intent="x")
            for i in range(1, 6)
        ),
    )
    invalid_execution = asyncio.run(executor().execute(AgentRequest("r", "x"), invalid))
    assert invalid_execution.status == ExecutionStatus.INVALID_PLAN

    slow = FakePersonal(delay=0.05)
    tools = FakeTools()
    plan = AgentPlan(
        request_id="r",
        reason_code="x",
        steps=(
            PlanStep(step_id="S1", type=PlanStepType.PERSONAL, intent="x"),
            PlanStep(
                step_id="S2",
                type=PlanStepType.TOOL,
                intent="x",
                tool_id="json_format",
                tool_input={"text": "x"},
            ),
        ),
    )
    timed = asyncio.run(
        executor(personal=slow, tools=tools, deadline=0.001).execute(AgentRequest("r", "x"), plan)
    )
    assert timed.status == ExecutionStatus.DEADLINE_EXCEEDED
    assert tools.calls == 0
