"""Phase 14C final synthesis with strict evidence/tool boundaries."""

from __future__ import annotations

import json
from typing import Protocol

from zglab_rag.agent.contracts import (
    AgentAnswer,
    AgentAnswerStatus,
    AgentObservation,
    AgentRequest,
    ObservationStatus,
    PersonalKnowledgeObservation,
    ToolObservation,
    WebResearchObservation,
)
from zglab_rag.agent.planning import AgentPlan, PlanStatus
from zglab_rag.generation.contracts import AnswerSource, GroundedAnswer


class MultiCapabilitySynthesizer(Protocol):
    """Injected boundary for a future grounded final-generation provider."""

    def synthesize(
        self,
        *,
        question: str,
        observations: tuple[AgentObservation, ...],
        allowed_sources: tuple[AnswerSource, ...],
    ) -> GroundedAnswer: ...


class AgentSynthesizer:
    """Produces an internal answer without changing a frozen plan.

    Single Personal/Web answers and deterministic Tool output avoid an extra
    model call. Multi-capability plans use only the injected synthesis
    boundary; Tool observations are never promoted to evidence or citations.
    """

    def __init__(self, multi_capability: MultiCapabilitySynthesizer | None = None) -> None:
        self._multi_capability = multi_capability

    def synthesize(
        self,
        request: AgentRequest,
        plan: AgentPlan,
        observations: tuple[AgentObservation, ...],
    ) -> AgentAnswer:
        if not observations:
            status = (
                AgentAnswerStatus.NEEDS_INPUT
                if plan.status == PlanStatus.NEEDS_INPUT
                else AgentAnswerStatus.FAILED
            )
            return AgentAnswer(
                status,
                "需要更多明确输入。"
                if status == AgentAnswerStatus.NEEDS_INPUT
                else "无法完成请求。",
                observations,
            )
        if len(plan.steps) == 1 and len(observations) == 1:
            return self._single(observations[0])
        sources = self._sources(observations)
        if self._multi_capability is None:
            return AgentAnswer(
                AgentAnswerStatus.FAILED,
                "多能力结果暂不可合成。",
                observations,
                sources,
                "synthesis unavailable",
            )
        grounded = self._multi_capability.synthesize(
            question=request.question, observations=observations, allowed_sources=sources
        )
        if not self._citations_valid(grounded, sources):
            return AgentAnswer(
                AgentAnswerStatus.FAILED,
                "合成结果未通过引用校验。",
                observations,
                sources,
                "invalid citations",
            )
        status = (
            AgentAnswerStatus.INSUFFICIENT_EVIDENCE
            if grounded.insufficient_evidence
            else AgentAnswerStatus.ANSWERED
        )
        return AgentAnswer(status, grounded.answer, observations, sources)

    @staticmethod
    def _single(observation: AgentObservation) -> AgentAnswer:
        if isinstance(observation, ToolObservation):
            if observation.status != ObservationStatus.SUCCESS:
                return AgentAnswer(
                    AgentAnswerStatus.FAILED,
                    "工具执行失败。",
                    (observation,),
                    failure_reason=observation.summary,
                )
            rendered = json.dumps(
                observation.structured_result, ensure_ascii=False, indent=2, default=str
            )
            return AgentAnswer(AgentAnswerStatus.ANSWERED, rendered, (observation,))
        if isinstance(observation, (PersonalKnowledgeObservation, WebResearchObservation)):
            result = observation.capability_result
            generation = result.generation if result else None
            if generation is None:
                return AgentAnswer(
                    AgentAnswerStatus.FAILED,
                    "能力执行失败。",
                    (observation,),
                    failure_reason=observation.summary,
                )
            status = (
                AgentAnswerStatus.INSUFFICIENT_EVIDENCE
                if generation.answer.insufficient_evidence
                else AgentAnswerStatus.ANSWERED
            )
            return AgentAnswer(
                status,
                generation.answer.answer,
                (observation,),
                tuple(generation.answer.sources),
                generation.failure_reason,
            )
        return AgentAnswer(
            AgentAnswerStatus.FAILED,
            "能力执行失败。",
            (observation,),
            failure_reason=observation.summary,
        )

    @staticmethod
    def _sources(observations: tuple[AgentObservation, ...]) -> tuple[AnswerSource, ...]:
        sources: list[AnswerSource] = []
        for observation in observations:
            if isinstance(observation, (PersonalKnowledgeObservation, WebResearchObservation)):
                generation = (
                    observation.capability_result.generation
                    if observation.capability_result
                    else None
                )
                if generation is not None:
                    sources.extend(generation.answer.sources)
        return tuple(sources)

    @staticmethod
    def _citations_valid(answer: GroundedAnswer, sources: tuple[AnswerSource, ...]) -> bool:
        allowed = {source.evidence_id for source in sources}
        citations = {citation for claim in answer.claims for citation in claim.citations}
        return citations <= allowed
