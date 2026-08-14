from __future__ import annotations

import hashlib
from collections.abc import Iterable

from zglab_rag.domain.models import KnowledgeChunk
from zglab_rag.evaluation.composition import compose_document_text
from zglab_rag.indexing.models import (
    EmbeddingProfile,
    IndexPlan,
    PlannedChunk,
    StoredChunkState,
)


def embedding_input_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_index_plan(
    chunks: Iterable[KnowledgeChunk],
    stored_states: Iterable[StoredChunkState],
    profile: EmbeddingProfile,
    *,
    force_rebuild: bool = False,
) -> IndexPlan:
    stored = {state.chunk_id: state for state in stored_states}
    current: dict[str, PlannedChunk] = {}
    plan = IndexPlan()

    for chunk in chunks:
        if chunk.chunk_id in current:
            raise ValueError(f"Duplicate chunk_id in current ingestion: {chunk.chunk_id}")
        text = compose_document_text(chunk, profile.composition)
        item = PlannedChunk(
            chunk=chunk,
            embedding_text=text,
            embedding_input_hash=embedding_input_hash(text),
        )
        current[chunk.chunk_id] = item
        previous = stored.get(chunk.chunk_id)
        if previous is None:
            plan.new.append(item)
        elif force_rebuild:
            plan.changed.append(item)
        elif (
            previous.embedding_profile_id == profile.profile_id
            and previous.embedding_input_hash == item.embedding_input_hash
        ):
            plan.unchanged.append(item)
        else:
            plan.changed.append(item)

    plan.deleted.extend(state for chunk_id, state in stored.items() if chunk_id not in current)
    return plan
