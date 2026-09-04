from __future__ import annotations

import json
import sqlite3
from time import perf_counter

import numpy as np

from zglab_rag.embeddings.config import EmbeddingModelConfig
from zglab_rag.indexing.errors import EmbeddingValidationError, IndexProfileMismatch
from zglab_rag.indexing.models import EmbeddingProfile
from zglab_rag.ingestion.contracts import EmbeddingProvider
from zglab_rag.retrieval.config import VectorRetrievalConfig
from zglab_rag.retrieval.contracts import (
    RetrievalDiagnostics,
    RetrievalQuery,
    RetrievalResponse,
    RetrievalResult,
)
from zglab_rag.storage.repositories import IndexRepository


class VectorRetriever:
    """Read-only, public-by-default retrieval over the active sqlite-vec index."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        provider: EmbeddingProvider,
        profile: EmbeddingProfile,
        *,
        model_config: EmbeddingModelConfig | None = None,
        config: VectorRetrievalConfig | None = None,
    ) -> None:
        self.repository = IndexRepository(connection)
        self.provider = provider
        self.profile = profile
        self.model_config = model_config
        self.config = config or VectorRetrievalConfig()
        self._validate_profile()

    @property
    def corpus_size(self) -> int:
        return self.repository.vector_count()

    def retrieve(self, query: RetrievalQuery) -> RetrievalResponse:
        started = perf_counter()
        top_k = self.config.validate_top_k(query.top_k)
        self._validate_profile()

        embedding_started = perf_counter()
        matrix = np.asarray(self.provider.encode_queries([query.text]), dtype=np.float32)
        embedding_ms = (perf_counter() - embedding_started) * 1000
        if matrix.shape != (1, self.profile.dimension) or not np.isfinite(matrix).all():
            raise EmbeddingValidationError(
                f"Query embedding has invalid shape or values: {matrix.shape}"
            )

        vector_started = perf_counter()
        rows, candidate_count, filtered_count = self._search_candidates(
            matrix[0], query, top_k
        )
        vector_ms = (perf_counter() - vector_started) * 1000
        results = [
            RetrievalResult(
                chunk_id=row["chunk_id"],
                document_id=row["document_id"],
                source_id=row["source_id"],
                source_path=row["source_path"],
                scope=row["scope"],
                title=row["title"],
                section_path=json.loads(row["section_path_json"]),
                content=row["content"],
                visibility=row["visibility"],
                revision=row["revision"],
                rank=rank,
                distance=distance,
                score=1.0 - distance,
            )
            for rank, (distance, row) in enumerate(rows[:top_k], start=1)
        ]
        total_ms = (perf_counter() - started) * 1000
        return RetrievalResponse(
            results=results,
            diagnostics=RetrievalDiagnostics(
                query_embedding_latency_ms=embedding_ms,
                vector_search_latency_ms=vector_ms,
                total_retrieval_latency_ms=total_ms,
                candidate_count=candidate_count,
                filtered_count=filtered_count,
                returned_count=len(results),
                top_k=top_k,
                filters=query.filters,
            ),
        )

    def _search_candidates(
        self,
        query_vector: np.ndarray,
        query: RetrievalQuery,
        top_k: int,
    ) -> tuple[list[tuple[float, sqlite3.Row]], int, int]:
        vector_count = self.corpus_size
        if vector_count == 0:
            return [], 0, 0
        maximum = min(vector_count, self.config.maximum_candidate_k)
        candidate_k = min(
            maximum,
            max(top_k * self.config.candidate_factor, self.config.minimum_candidate_k),
        )
        allowed: list[tuple[float, sqlite3.Row]] = []
        candidates = []
        while True:
            candidates = self.repository.vector_candidates(
                query_vector,
                candidate_k=candidate_k,
            )
            hydrated = self.repository.hydrate_filtered_candidates(
                [int(candidate["rowid"]) for candidate in candidates],
                visibility=query.filters.visibility.value,
                source_ids=query.filters.source_ids,
                scopes=[scope.value for scope in query.filters.scopes],
                document_ids=query.filters.document_ids,
                section_ids=query.filters.section_ids,
            )
            allowed = [
                (float(candidate["distance"]), hydrated[int(candidate["rowid"])])
                for candidate in candidates
                if int(candidate["rowid"]) in hydrated
            ]
            allowed.sort(key=lambda item: (item[0], item[1]["chunk_id"]))
            if len(allowed) >= top_k or candidate_k >= maximum:
                break
            candidate_k = min(
                maximum,
                max(candidate_k + 1, candidate_k * self.config.candidate_factor),
            )
        return allowed, len(candidates), len(candidates) - len(allowed)

    def _validate_profile(self) -> None:
        if self.provider.model_name != self.profile.model_name:
            raise IndexProfileMismatch(
                f"Query provider model mismatch: provider={self.provider.model_name}, "
                f"profile={self.profile.model_name}"
            )
        if self.provider.dimension != self.profile.dimension:
            raise IndexProfileMismatch(
                f"Query provider dimension mismatch: provider={self.provider.dimension}, "
                f"profile={self.profile.dimension}"
            )
        active_id = self.repository.active_profile_id()
        if active_id != self.profile.profile_id:
            raise IndexProfileMismatch(
                f"Retriever profile does not match active index: database={active_id}, "
                f"requested={self.profile.profile_id}"
            )
        row = self.repository.profile(active_id)
        if row is None:
            raise IndexProfileMismatch(f"Active embedding profile is missing: {active_id}")
        expected = {
            "model_id": self.profile.model_id,
            "model_name": self.profile.model_name,
            "dimension": self.profile.dimension,
            "composition": self.profile.composition.value,
            "normalize": int(self.profile.normalize),
            "query_mode": self.profile.query_mode,
        }
        mismatches = [key for key, value in expected.items() if row[key] != value]
        if mismatches:
            raise IndexProfileMismatch(
                "Active embedding profile metadata mismatch: " + ", ".join(mismatches)
            )
        provider_config = getattr(self.provider, "config", self.model_config)
        if provider_config is not None and (
            provider_config.model_name != self.profile.model_name
            or provider_config.normalize != self.profile.normalize
            or provider_config.query_mode.value != self.profile.query_mode
        ):
            raise IndexProfileMismatch("Query provider configuration mismatches active profile")
