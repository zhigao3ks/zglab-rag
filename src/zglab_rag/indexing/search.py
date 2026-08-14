from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

import numpy as np

from zglab_rag.indexing.errors import EmbeddingValidationError, IndexProfileMismatch
from zglab_rag.indexing.models import EmbeddingProfile
from zglab_rag.ingestion.contracts import EmbeddingProvider
from zglab_rag.storage.repositories import IndexRepository


@dataclass(frozen=True, slots=True)
class VectorSearchResult:
    distance: float
    chunk_id: str
    source_id: str
    source_path: str
    title: str
    section_path: list[str]
    content: str
    visibility: str


def search_public_vectors(
    connection: sqlite3.Connection,
    provider: EmbeddingProvider,
    profile: EmbeddingProfile,
    query: str,
    *,
    top_k: int,
) -> list[VectorSearchResult]:
    if top_k <= 0:
        raise ValueError("top_k must be positive")
    repository = IndexRepository(connection)
    active = repository.active_profile_id()
    if active != profile.profile_id:
        raise IndexProfileMismatch(
            f"Search profile does not match active index: database={active}, "
            f"requested={profile.profile_id}"
        )
    vector = np.asarray(provider.encode_queries([query]), dtype=np.float32)
    if vector.shape != (1, profile.dimension) or not np.isfinite(vector).all():
        raise EmbeddingValidationError(
            f"Query embedding has invalid shape or values: {vector.shape}"
        )
    rows = repository.public_vector_search(vector[0], top_k=top_k)
    return [
        VectorSearchResult(
            distance=float(row["distance"]),
            chunk_id=row["chunk_id"],
            source_id=row["source_id"],
            source_path=row["source_path"],
            title=row["title"],
            section_path=json.loads(row["section_path_json"]),
            content=row["content"],
            visibility=row["visibility"],
        )
        for row in rows
    ]
