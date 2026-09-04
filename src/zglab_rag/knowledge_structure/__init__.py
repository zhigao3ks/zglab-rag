"""Deterministic Phase 16 knowledge structures and provenance graph."""

from zglab_rag.knowledge_structure.builder import (
    RETRIEVAL_STRUCTURE_VERSION,
    normalize_name,
    rebuild_knowledge_structure,
    section_id_for,
)

__all__ = [
    "RETRIEVAL_STRUCTURE_VERSION",
    "normalize_name",
    "rebuild_knowledge_structure",
    "section_id_for",
]
