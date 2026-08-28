import pytest
from pydantic import ValidationError

from zglab_rag.agent import (
    AgentPlan,
    AgentRequest,
    BoundedPlanner,
    PlanStatus,
    PlanStep,
    PlanStepType,
)


@pytest.mark.parametrize(
    ("question", "kind"),
    [
        ("我最近做了哪些项目？", PlanStepType.PERSONAL),
        ("Python current version", PlanStepType.WEB),
        ("普通问题", PlanStepType.PERSONAL),
    ],
)
def test_deterministic_routes(question, kind):
    plan = BoundedPlanner().plan(AgentRequest("r", question))
    assert plan.steps[0].type == kind


def test_explicit_tools_and_combined_plan_are_bounded():
    planner = BoundedPlanner()
    assert (
        planner.plan(AgentRequest("r", '把这个 JSON 格式化：{"a":1}')).steps[0].tool_id
        == "json_format"
    )
    assert planner.plan(AgentRequest("r", "Base64 解码：aGk=")).steps[0].tool_id == "base64_decode"
    plan = planner.plan(AgentRequest("r", "我的 RAG 项目和当前主流架构相比有什么区别？"))
    assert [step.type for step in plan.steps] == [PlanStepType.PERSONAL, PlanStepType.WEB]


def test_tool_needs_input_and_contract_rejects_invalid_plans():
    assert (
        BoundedPlanner().plan(AgentRequest("r", "把这个 JSON 格式化")).status
        == PlanStatus.NEEDS_INPUT
    )
    with pytest.raises(ValidationError):
        AgentPlan(
            request_id="r",
            reason_code="x",
            steps=(
                PlanStep(step_id="S1", type=PlanStepType.TOOL, intent="x", tool_id="shell_exec"),
            ),
        )
    with pytest.raises(ValidationError):
        AgentPlan(
            request_id="r",
            reason_code="x",
            steps=(
                PlanStep(step_id="S1", type=PlanStepType.PERSONAL, intent="x", depends_on=("S2",)),
            ),
        )
