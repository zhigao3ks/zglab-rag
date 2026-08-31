"""Phase 14A framework-free Agent request and observation contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from zglab_rag.auth.models import AuthenticatedPrincipal
from zglab_rag.capabilities.contracts import CapabilityResult
from zglab_rag.conversation.context import ConversationContext
from zglab_rag.generation.contracts import AnswerSource


class ObservationOrigin(StrEnum):
    PERSONAL = "personal"
    WEB = "web"
    TOOL = "tool"


class ObservationStatus(StrEnum):
    SUCCESS = "success"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    FAILED = "failed"
    DISABLED = "disabled"
    BLOCKED = "blocked"


class AgentAnswerStatus(StrEnum):
    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    FAILED = "failed"
    NEEDS_INPUT = "needs_input"


@dataclass(frozen=True, slots=True)
class AgentRequest:
    """A request supplied to an explicit future-agent invocation.

    This is intentionally not a plan: it contains no selected capability,
    steps, reasoning or mutable user policy.
    """

    request_id: str
    question: str
    principal: AuthenticatedPrincipal | None = None
    conversation_context: ConversationContext | None = None


@dataclass(frozen=True, slots=True)
class AgentObservation:
    """Base observation. IDs are request-scoped O1, O2… and never Evidence IDs."""

    observation_id: str
    origin: ObservationOrigin
    status: ObservationStatus
    summary: str | None = None
    step_id: str | None = None


@dataclass(frozen=True, slots=True)
class PersonalKnowledgeObservation(AgentObservation):
    capability_result: CapabilityResult | None = None


@dataclass(frozen=True, slots=True)
class WebResearchObservation(AgentObservation):
    capability_result: CapabilityResult | None = None


@dataclass(frozen=True, slots=True)
class ToolObservation(AgentObservation):
    tool_id: str = ""
    structured_result: object | None = None
    error_code: str | None = None
    error_message: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AgentAnswer:
    """Internal final answer; sources are only existing grounded provenance."""

    status: AgentAnswerStatus
    answer: str
    observations: tuple[AgentObservation, ...]
    sources: tuple[AnswerSource, ...] = ()
    failure_reason: str | None = None
