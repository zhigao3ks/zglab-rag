"""Capability Foundation (Phase 12A).

A minimal, framework-free boundary that wraps the existing grounded RAG
pipeline as an addressable capability. This layer defines *what controlled
abilities the system exposes and how they are invoked uniformly*; it is
deliberately NOT an agent runtime (no planner, no routing, no loops).
"""

from zglab_rag.capabilities.contracts import (
    PERSONAL_KNOWLEDGE_CAPABILITY_ID,
    Capability,
    CapabilityContext,
    CapabilityMetadata,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
    EvidenceOrigin,
)
from zglab_rag.capabilities.errors import (
    CapabilityError,
    CapabilityNotFoundError,
    CapabilityPolicyError,
    CapabilityTechnicalError,
    DuplicateCapabilityError,
)
from zglab_rag.capabilities.personal_knowledge import PersonalKnowledgeSkill
from zglab_rag.capabilities.registry import CapabilityRegistry

__all__ = [
    "PERSONAL_KNOWLEDGE_CAPABILITY_ID",
    "Capability",
    "CapabilityContext",
    "CapabilityError",
    "CapabilityMetadata",
    "CapabilityNotFoundError",
    "CapabilityPolicyError",
    "CapabilityRegistry",
    "CapabilityRequest",
    "CapabilityResult",
    "CapabilityStatus",
    "CapabilityTechnicalError",
    "DuplicateCapabilityError",
    "EvidenceOrigin",
    "PersonalKnowledgeSkill",
]
