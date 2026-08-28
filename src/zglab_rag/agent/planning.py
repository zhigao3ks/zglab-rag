"""Phase 14B deterministic, bounded planning. It never executes a step."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from zglab_rag.agent.contracts import AgentRequest
from zglab_rag.capabilities.selection import _contains_any
from zglab_rag.mcp.policy import MCP_TOOL_ALLOWLIST


class PlanStepType(StrEnum):
    PERSONAL = "personal"
    WEB = "web"
    TOOL = "tool"


class PlanStatus(StrEnum):
    READY = "ready"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    step_id: str = Field(pattern=r"^S[1-4]$")
    type: PlanStepType
    intent: str
    tool_id: str | None = None
    tool_input: dict | None = None
    depends_on: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_tool(self) -> PlanStep:
        if self.type == PlanStepType.TOOL:
            if self.tool_id not in MCP_TOOL_ALLOWLIST:
                raise ValueError("tool_id is not in the host allowlist")
        elif self.tool_id is not None or self.tool_input is not None:
            raise ValueError("only tool steps may carry tool fields")
        return self


class AgentPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request_id: str
    status: PlanStatus = PlanStatus.READY
    reason_code: str
    steps: tuple[PlanStep, ...]

    @model_validator(mode="after")
    def validate_bounds(self) -> AgentPlan:
        if not self.steps or len(self.steps) > 4:
            raise ValueError("plan must contain 1 to 4 steps")
        ids = tuple(step.step_id for step in self.steps)
        if ids != tuple(f"S{i}" for i in range(1, len(ids) + 1)):
            raise ValueError("step ids must be sequential")
        if sum(step.type == PlanStepType.PERSONAL for step in self.steps) > 1:
            raise ValueError("at most one personal step is allowed")
        if sum(step.type == PlanStepType.WEB for step in self.steps) > 1:
            raise ValueError("at most one web step is allowed")
        if sum(step.type == PlanStepType.TOOL for step in self.steps) > 3:
            raise ValueError("at most three tool steps are allowed")
        known: set[str] = set()
        for step in self.steps:
            if any(dependency not in known for dependency in step.depends_on):
                raise ValueError("dependencies must refer to previous steps")
            known.add(step.step_id)
        return self


_SELF = ("我", "本人", "黄志高", "志高", "简历", "履历", "我的项目", "我的经历")
_CURRENT = ("最新", "当前", "今天", "最近", "latest", "current")
_TOOLS = (
    ("json_format", ("json格式化", "json 格式化", "format json")),
    ("base64_decode", ("base64解码", "base64 解码", "decode base64")),
    ("text_count", ("统计这段文字", "文本统计", "text count")),
    ("timestamp_convert", ("时间戳", "timestamp")),
)


class BoundedPlanner:
    """Auditable deterministic router; a Phase 14C executor is deliberately absent."""

    def plan(self, request: AgentRequest) -> AgentPlan:
        question = request.question.strip()
        personal = _contains_any(question, _SELF)
        current = _contains_any(question, _CURRENT)
        tool_id = next((tool for tool, markers in _TOOLS if _contains_any(question, markers)), None)
        if tool_id:
            return self._tool(request, tool_id, question)
        if personal and current and ("比较" in question or "相比" in question):
            return AgentPlan(
                request_id=request.request_id,
                reason_code="personal_current_comparison",
                steps=(
                    PlanStep(step_id="S1", type=PlanStepType.PERSONAL, intent="personal facts"),
                    PlanStep(
                        step_id="S2", type=PlanStepType.WEB, intent="current external context"
                    ),
                ),
            )
        if personal:
            return self._single(request, PlanStepType.PERSONAL, "personal_integrity")
        if current:
            return self._single(request, PlanStepType.WEB, "current_information")
        return self._single(request, PlanStepType.PERSONAL, "default_personal")

    @staticmethod
    def _single(request: AgentRequest, kind: PlanStepType, reason: str) -> AgentPlan:
        return AgentPlan(
            request_id=request.request_id,
            reason_code=reason,
            steps=(PlanStep(step_id="S1", type=kind, intent=reason),),
        )

    @staticmethod
    def _tool(request: AgentRequest, tool_id: str, question: str) -> AgentPlan:
        marker = question.split("：", 1)
        if len(marker) == 1:
            return AgentPlan(
                request_id=request.request_id,
                status=PlanStatus.NEEDS_INPUT,
                reason_code="tool_input_required",
                steps=(
                    PlanStep(
                        step_id="S1",
                        type=PlanStepType.TOOL,
                        intent="explicit tool",
                        tool_id=tool_id,
                    ),
                ),
            )
        return AgentPlan(
            request_id=request.request_id,
            reason_code="explicit_tool",
            steps=(
                PlanStep(
                    step_id="S1",
                    type=PlanStepType.TOOL,
                    intent="explicit tool",
                    tool_id=tool_id,
                    tool_input={"text": marker[1].strip()},
                ),
            ),
        )
