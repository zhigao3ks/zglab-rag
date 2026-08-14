from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from time import perf_counter

import numpy as np

from zglab_rag.domain.lexical import DEFAULT_LEXICAL_PROFILE
from zglab_rag.indexing.errors import (
    EmbeddingValidationError,
    IndexProfileMismatch,
    RebuildScopeError,
)
from zglab_rag.indexing.models import (
    EmbeddingProfile,
    IndexPlan,
    IndexRunResult,
    SourceIndexInput,
)
from zglab_rag.indexing.planner import build_index_plan
from zglab_rag.ingestion.contracts import EmbeddingProvider
from zglab_rag.storage.repositories import IndexRepository


def ensure_profile_compatible(
    repository: IndexRepository,
    profile: EmbeddingProfile,
    *,
    rebuild: bool,
    source_ids: set[str],
) -> bool:
    active_profile_id = repository.active_profile_id()
    if active_profile_id is None or active_profile_id == profile.profile_id:
        return False
    if not rebuild:
        raise IndexProfileMismatch(
            "Active embedding profile does not match the requested profile: "
            f"database={active_profile_id}, requested={profile.profile_id}. "
            "Run the explicit rebuild command to replace the index."
        )
    omitted = repository.indexed_source_ids() - source_ids
    if omitted:
        raise RebuildScopeError(
            "A profile-changing rebuild must include every indexed source; omitted: "
            + ", ".join(sorted(omitted))
        )
    return True


def plan_sources(
    repository: IndexRepository | None,
    sources: Sequence[SourceIndexInput],
    profile: EmbeddingProfile,
    *,
    rebuild: bool = False,
) -> IndexPlan:
    source_ids = [item.source.id for item in sources]
    stored_states = [] if repository is None else repository.stored_chunk_states(source_ids)
    if repository is not None:
        ensure_profile_compatible(
            repository,
            profile,
            rebuild=rebuild,
            source_ids=set(source_ids),
        )
    return build_index_plan(
        (chunk for item in sources for chunk in item.chunks),
        stored_states,
        profile,
        force_rebuild=rebuild,
    )


class KnowledgeIndexer:
    def __init__(
        self,
        connection: sqlite3.Connection,
        provider: EmbeddingProvider | None,
        profile: EmbeddingProfile,
        *,
        batch_size: int = 32,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if provider is not None and provider.dimension != profile.dimension:
            raise EmbeddingValidationError(
                f"Provider dimension {provider.dimension} does not match profile "
                f"dimension {profile.dimension}"
            )
        if provider is not None and provider.model_name != profile.model_name:
            raise EmbeddingValidationError(
                f"Provider model {provider.model_name!r} does not match profile "
                f"model {profile.model_name!r}"
            )
        self.repository = IndexRepository(connection)
        self.provider = provider
        self.profile = profile
        self.batch_size = batch_size

    def build(
        self,
        sources: Sequence[SourceIndexInput],
        *,
        rebuild: bool = False,
    ) -> IndexRunResult:
        started = perf_counter()
        self.repository.validate_lexical_profile(DEFAULT_LEXICAL_PROFILE)
        source_ids = {item.source.id for item in sources}
        reset_all_vectors = ensure_profile_compatible(
            self.repository,
            self.profile,
            rebuild=rebuild,
            source_ids=source_ids,
        )
        plan = build_index_plan(
            (chunk for item in sources for chunk in item.chunks),
            self.repository.stored_chunk_states(sorted(source_ids)),
            self.profile,
            force_rebuild=rebuild,
        )
        run_id = self.repository.begin_run(self.profile, sources, plan)
        try:
            embeddings = self._embed(plan)
            self.repository.apply(
                run_id=run_id,
                profile=self.profile,
                sources=sources,
                plan=plan,
                embeddings=embeddings,
                reset_all_vectors=reset_all_vectors,
            )
        except Exception as exc:
            self.repository.mark_run_failed(run_id, exc)
            raise
        return IndexRunResult(
            run_id=run_id,
            document_count=sum(len(item.documents) for item in sources),
            plan=plan,
            embedded_chunks=len(embeddings),
            elapsed_seconds=perf_counter() - started,
        )

    def _embed(self, plan: IndexPlan) -> dict[str, np.ndarray]:
        items = plan.needs_embedding
        provider = self.provider
        if items and provider is None:
            raise EmbeddingValidationError("An embedding provider is required for changed chunks")
        result: dict[str, np.ndarray] = {}
        for start in range(0, len(items), self.batch_size):
            batch = items[start : start + self.batch_size]
            vectors = np.asarray(
                provider.encode_documents([item.embedding_text for item in batch]),
                dtype=np.float32,
            )
            expected_shape = (len(batch), self.profile.dimension)
            if vectors.shape != expected_shape:
                raise EmbeddingValidationError(
                    f"Embedding provider returned shape {vectors.shape}; expected {expected_shape}"
                )
            if not np.isfinite(vectors).all():
                raise EmbeddingValidationError("Embedding provider returned non-finite values")
            result.update(
                (item.chunk.chunk_id, vector) for item, vector in zip(batch, vectors, strict=True)
            )
        return result
