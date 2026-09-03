"""PersonalKnowledgeSkill (Phase 12A).

The first real capability: a stable adapter that wraps the EXISTING
GroundedAnswerService pipeline (Retrieval -> Evidence -> Generation ->
Citation Validation) behind the Capability contract. It reuses the RAG
stack verbatim — nothing is rewritten or copied into the skill.

Invariants preserved:
- public-only knowledge policy: the skill never touches visibility; it
  runs whatever GroundedAnswerService the runtime builds, which stays
  public-only downstream. Logging in controls who may CONSUME the
  capability, never unlocks private knowledge (Phase 16 territory);
- citation contract unchanged: the GenerationResult (answer + sources +
  validated claims) is carried through the result untouched;
- progress semantics unchanged: the callback is forwarded as-is, so SSE
  stages keep the Phase 9 order accepted/retrieving/generating/validating.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol

from zglab_rag.capabilities.contracts import (
    PERSONAL_KNOWLEDGE_CAPABILITY_ID,
    CapabilityContext,
    CapabilityMetadata,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
    EvidenceOrigin,
)
from zglab_rag.capabilities.errors import CapabilityTechnicalError
from zglab_rag.capabilities.registry import CapabilityRegistry
from zglab_rag.conversation.resources import knowledge_snapshot_fingerprint
from zglab_rag.generation.contracts import ProgressCallback


class KnowledgePipelineRuntime(Protocol):
    """Structural view of the runtime the skill needs.

    Satisfied by ProductionRuntime / ApplicationRuntime / test fakes:
    a request-scoped read-only connection factory plus the existing
    GroundedAnswerService factory. The skill never opens connections or
    builds providers itself.
    """

    def request_connection(self) -> AbstractContextManager: ...

    def create_service(self, connection): ...


PERSONAL_KNOWLEDGE_METADATA = CapabilityMetadata(
    id=PERSONAL_KNOWLEDGE_CAPABILITY_ID,
    name="Personal Knowledge",
    description=(
        "Evidence-grounded answers over the owner's registered public "
        "knowledge sources, with citation validation."
    ),
    requires_auth=True,
    network_access=False,
)


class PersonalKnowledgeSkill:
    """Capability adapter around the existing grounded answer pipeline."""

    metadata: CapabilityMetadata = PERSONAL_KNOWLEDGE_METADATA

    def __init__(self, runtime: KnowledgePipelineRuntime) -> None:
        self._runtime = runtime

    def execute(
        self,
        request: CapabilityRequest,
        context: CapabilityContext,
        *,
        progress: ProgressCallback | None = None,
    ) -> CapabilityResult:
        """Run the grounded pipeline and map the outcome.

        INSUFFICIENT_EVIDENCE is a business result, not an exception; only
        infrastructure failures raise (wrapped so the API can restore the
        exact Phase 9 error mapping).
        """
        try:
            with self._runtime.request_connection() as connection:
                service = self._runtime.create_service(connection)
                reuse_kwargs = {}
                if context.session_workspace is not None:
                    reuse_kwargs = {
                        "session_workspace": context.session_workspace,
                        "knowledge_snapshot_fingerprint": knowledge_snapshot_fingerprint(
                            connection
                        ),
                        "request_id": context.request_id,
                    }
                if context.conversation_context is None:
                    generation = service.answer(
                        request.question,
                        progress=progress,
                        **reuse_kwargs,
                    )
                else:
                    generation = service.answer(
                        request.question,
                        progress=progress,
                        conversation_context=context.conversation_context,
                        **reuse_kwargs,
                    )
        except Exception as exc:
            raise CapabilityTechnicalError(
                f"personal knowledge capability failed: {exc}", original=exc
            ) from exc

        status = CapabilityStatus.from_generation_status(generation.status)
        failure_reason = generation.failure_reason if status == CapabilityStatus.FAILED else None
        return CapabilityResult(
            capability_id=PERSONAL_KNOWLEDGE_CAPABILITY_ID,
            status=status,
            origin=EvidenceOrigin.PERSONAL,
            generation=generation,
            failure_reason=failure_reason,
        )


def build_capability_registry(runtime: KnowledgePipelineRuntime) -> CapabilityRegistry:
    """Construct the app-scoped registry for a runtime.

    Phase 12A registers exactly one real capability. Future ids
    (web_research, mcp_tool) are documented plans, never phantom
    registrations: the runtime only lists abilities that really exist.
    """
    registry = CapabilityRegistry()
    registry.register(PersonalKnowledgeSkill(runtime))
    return registry
