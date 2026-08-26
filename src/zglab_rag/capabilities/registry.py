"""Deterministic capability registry (Phase 12A).

Only registration, lookup and listing. The registry is NOT a planner: it
never selects a capability from a prompt, never calls an LLM, never falls
back and never loops. Capability selection policy belongs to a later
phase; today the API asks for ``personal_knowledge`` explicitly.
"""

from __future__ import annotations

from zglab_rag.capabilities.contracts import Capability, CapabilityMetadata
from zglab_rag.capabilities.errors import CapabilityNotFoundError, DuplicateCapabilityError


class CapabilityRegistry:
    """Id-keyed registry of capability instances."""

    def __init__(self) -> None:
        self._capabilities: dict[str, Capability] = {}

    def register(self, capability: Capability) -> None:
        capability_id = capability.metadata.id
        if capability_id in self._capabilities:
            raise DuplicateCapabilityError(f"Capability '{capability_id}' already registered")
        self._capabilities[capability_id] = capability

    def get(self, capability_id: str) -> Capability:
        try:
            return self._capabilities[capability_id]
        except KeyError:
            raise CapabilityNotFoundError(
                f"Capability '{capability_id}' is not registered"
            ) from None

    def list_metadata(self) -> list[CapabilityMetadata]:
        """Stable, insertion-ordered metadata snapshot (no execution)."""
        return [capability.metadata for capability in self._capabilities.values()]
