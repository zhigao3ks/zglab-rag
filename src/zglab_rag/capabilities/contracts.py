"""Capability Foundation contracts (Phase 12A).

Minimal, framework-free boundary between the API layer and the controlled
abilities of the system. Deliberately small: this layer answers "what
capabilities exist and how are they invoked uniformly", NOT "how does a
model decide which capability to run" (that belongs to the future Agent
Orchestrator, Phase 14).

Dependency direction (frozen):

    API -> Capability -> Application / Generation / Retrieval

The capability layer never imports FastAPI / Starlette / Vue, never parses
cookies or sessions, and never opens SQLite connections itself; those stay
in the Phase 11 security gateway and the existing runtime factories.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from zglab_rag.auth.models import AuthenticatedPrincipal
from zglab_rag.conversation.context import ConversationContext
from zglab_rag.conversation.resources import SessionWorkspaceProtocol
from zglab_rag.generation.contracts import (
    EvidenceOrigin,
    GenerationResult,
    GenerationStatus,
)

PERSONAL_KNOWLEDGE_CAPABILITY_ID = "personal_knowledge"

# EvidenceOrigin moved to zglab_rag.generation.contracts in Phase 12C so the
# generation layer can mark PERSONAL vs WEB evidence; re-exported here so all
# existing capability/research import paths keep working.


class CapabilityStatus(StrEnum):
    """Uniform outcome reported by any capability to upper layers.

    INSUFFICIENT_EVIDENCE is a normal business result (Phase 8 semantics),
    NOT a technical failure: future policy may decide to try another
    capability (e.g. web research), while FAILED must never trigger such a
    fallback because it means the capability itself broke.
    """

    SUCCESS = "success"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    FAILED = "failed"

    @classmethod
    def from_generation_status(cls, status: GenerationStatus) -> CapabilityStatus:
        if status == GenerationStatus.ANSWERED:
            return cls.SUCCESS
        if status == GenerationStatus.INSUFFICIENT_EVIDENCE:
            return cls.INSUFFICIENT_EVIDENCE
        return cls.FAILED


@dataclass(frozen=True, slots=True)
class CapabilityMetadata:
    """Lightweight, deterministic description of a capability.

    No permission DSL, no capability graph: just enough for a registry to
    list what exists and for future policy layers to reason about cost and
    network access.
    """

    id: str
    name: str
    description: str
    requires_auth: bool = True
    network_access: bool = False


@dataclass(frozen=True, slots=True)
class CapabilityRequest:
    """Intentionally narrow input contract.

    Clients may only supply the question. Retrieval mode, top_k,
    visibility, provider, model and every other control surface remain
    server-side configuration / policy.
    """

    question: str


@dataclass(frozen=True, slots=True)
class CapabilityContext:
    """Request-scoped information shared by all capabilities.

    Carries only framework-neutral values: the request id and the
    authenticated principal already resolved by the Phase 11 security
    gateway. Never HTTP requests, headers, cookies or DB connections.
    """

    request_id: str
    principal: AuthenticatedPrincipal | None = None
    # Server-derived, low-trust reference data. It cannot select a capability,
    # alter policy, or become evidence/citations.
    conversation_context: ConversationContext | None = None
    session_workspace: SessionWorkspaceProtocol | None = None


@dataclass(frozen=True, slots=True)
class CapabilityResult:
    """Uniform capability outcome.

    The original GenerationResult is carried through unchanged so the API
    keeps its exact public envelope (request_id / status / answer /
    sources) and the Phase 8 citation semantics stay untouched.
    """

    capability_id: str
    status: CapabilityStatus
    origin: EvidenceOrigin
    generation: GenerationResult | None = None
    failure_reason: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


class Capability(Protocol):
    """Uniform invocation contract for every controlled ability."""

    metadata: CapabilityMetadata

    def execute(
        self,
        request: CapabilityRequest,
        context: CapabilityContext,
        *,
        progress=None,
    ) -> CapabilityResult: ...
