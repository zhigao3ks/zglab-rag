from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from zglab_rag.domain.models import KnowledgeChunk
from zglab_rag.evaluation.dataset import EvaluationQuery, RelevantTarget

DEFAULT_RECALL_CUTOFFS = (1, 3, 5, 10, 20, 30)


def cosine_similarity_matrix(
    queries: NDArray[np.floating],
    documents: NDArray[np.floating],
) -> NDArray[np.float32]:
    query_matrix = np.asarray(queries, dtype=np.float32)
    document_matrix = np.asarray(documents, dtype=np.float32)
    if query_matrix.ndim != 2 or document_matrix.ndim != 2:
        raise ValueError("query and document embeddings must be two-dimensional")
    if query_matrix.shape[1] != document_matrix.shape[1]:
        raise ValueError("query and document embedding dimensions must match")

    query_norms = np.linalg.norm(query_matrix, axis=1, keepdims=True)
    document_norms = np.linalg.norm(document_matrix, axis=1, keepdims=True)
    safe_queries = np.divide(
        query_matrix,
        query_norms,
        out=np.zeros_like(query_matrix),
        where=query_norms != 0,
    )
    safe_documents = np.divide(
        document_matrix,
        document_norms,
        out=np.zeros_like(document_matrix),
        where=document_norms != 0,
    )
    return np.asarray(safe_queries @ safe_documents.T, dtype=np.float32)


def rank_by_cosine(
    query_embeddings: NDArray[np.floating],
    document_embeddings: NDArray[np.floating],
    chunks: list[KnowledgeChunk],
) -> list[list[int]]:
    if document_embeddings.shape[0] != len(chunks):
        raise ValueError("document embedding count must equal chunk count")
    similarities = cosine_similarity_matrix(query_embeddings, document_embeddings)
    stable_keys = np.asarray([chunk.chunk_id for chunk in chunks], dtype=str)
    return [np.lexsort((stable_keys, -scores)).astype(int).tolist() for scores in similarities]


@dataclass(frozen=True, slots=True)
class RetrievalMetrics:
    recall_at: dict[int, float]
    mrr: float
    evaluated_queries: int


def _target_is_retrieved(
    target: RelevantTarget,
    ranked_indices: list[int],
    chunks: list[KnowledgeChunk],
) -> bool:
    return any(target.matches(chunks[index]) for index in ranked_indices)


def compute_retrieval_metrics(
    queries: list[EvaluationQuery],
    rankings: list[list[int]],
    chunks: list[KnowledgeChunk],
    *,
    cutoffs: tuple[int, ...] = DEFAULT_RECALL_CUTOFFS,
) -> RetrievalMetrics:
    if len(queries) != len(rankings):
        raise ValueError("query count must equal ranking count")
    if not queries:
        raise ValueError("at least one scored query is required")

    recall_sums = {cutoff: 0.0 for cutoff in cutoffs}
    reciprocal_rank_sum = 0.0
    for query, ranking in zip(queries, rankings, strict=True):
        if not query.relevant:
            raise ValueError(f"query '{query.id}' has no relevant targets")
        for cutoff in cutoffs:
            retrieved = sum(
                _target_is_retrieved(target, ranking[:cutoff], chunks) for target in query.relevant
            )
            recall_sums[cutoff] += retrieved / len(query.relevant)

        first_relevant_rank = next(
            (
                rank
                for rank, chunk_index in enumerate(ranking, start=1)
                if any(target.matches(chunks[chunk_index]) for target in query.relevant)
            ),
            None,
        )
        if first_relevant_rank is not None:
            reciprocal_rank_sum += 1.0 / first_relevant_rank

    query_count = len(queries)
    return RetrievalMetrics(
        recall_at={cutoff: recall_sums[cutoff] / query_count for cutoff in cutoffs},
        mrr=reciprocal_rank_sum / query_count,
        evaluated_queries=query_count,
    )
