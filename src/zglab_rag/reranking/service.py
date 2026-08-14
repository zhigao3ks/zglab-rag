from __future__ import annotations

from time import perf_counter

import numpy as np
from pydantic import BaseModel

from zglab_rag.reranking.contracts import RerankerProvider
from zglab_rag.reranking.passage import compose_passage_context
from zglab_rag.retrieval.contracts import RetrievalFilter, RetrievalQuery, RetrievalResult


class RerankerRetrievalConfig(BaseModel):
    default_top_k: int = 5
    maximum_top_k: int = 20
    candidate_k: int = 20

    def model_post_init(self, _context) -> None:
        if self.candidate_k not in (10, 20, 30):
            raise ValueError("candidate_k must be one of 10, 20 or 30")
        if self.maximum_top_k > self.candidate_k:
            raise ValueError("maximum_top_k must not exceed candidate_k")
        if self.default_top_k <= 0 or self.default_top_k > self.maximum_top_k:
            raise ValueError("default_top_k must be between 1 and maximum_top_k")

    def validate_top_k(self, requested: int | None) -> int:
        top_k = self.default_top_k if requested is None else requested
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        if top_k > self.candidate_k:
            raise ValueError("top_k must not exceed candidate_k")
        if top_k > self.maximum_top_k:
            raise ValueError(f"top_k must not exceed {self.maximum_top_k}")
        return top_k


class RerankedDiagnostics(BaseModel):
    vector_retrieval_latency_ms: float
    reranker_latency_ms: float
    total_retrieval_latency_ms: float
    candidate_k: int
    batch_size: int
    pairs_scored: int
    candidate_count: int
    returned_count: int
    top_k: int
    filters: RetrievalFilter


class RerankedResponse(BaseModel):
    results: list[RetrievalResult]
    diagnostics: RerankedDiagnostics


class RerankedRetriever:
    def __init__(
        self,
        vector_retriever,
        provider: RerankerProvider,
        *,
        config: RerankerRetrievalConfig | None = None,
    ) -> None:
        self.vector_retriever = vector_retriever
        self.provider = provider
        self.config = config or RerankerRetrievalConfig()

    @property
    def corpus_size(self) -> int:
        return self.vector_retriever.corpus_size

    def retrieve(self, query: RetrievalQuery) -> RerankedResponse:
        started = perf_counter()
        top_k = self.config.validate_top_k(query.top_k)
        vector_started = perf_counter()
        vector_response = self.vector_retriever.retrieve(
            query.model_copy(update={"top_k": self.config.candidate_k})
        )
        vector_ms = (perf_counter() - vector_started) * 1000
        candidates = vector_response.results
        if not candidates:
            return RerankedResponse(
                results=[],
                diagnostics=self._diagnostics(
                    query,
                    vector_ms=vector_ms,
                    reranker_ms=0.0,
                    candidate_count=0,
                    returned_count=0,
                    top_k=top_k,
                    total_ms=(perf_counter() - started) * 1000,
                ),
            )

        reranker_started = perf_counter()
        passages = [compose_passage_context(candidate) for candidate in candidates]
        scores = np.asarray(self.provider.score(query.text, passages), dtype=np.float32)
        reranker_ms = (perf_counter() - reranker_started) * 1000
        if scores.shape != (len(candidates),) or not np.isfinite(scores).all():
            raise ValueError(
                f"Reranker returned invalid scores: shape={scores.shape}, "
                f"expected={(len(candidates),)}"
            )

        scored = [
            (
                float(score),
                candidate.rank,
                candidate.chunk_id,
                candidate.model_copy(
                    update={
                        "rank": 1,
                        "score": float(score),
                        "retriever": "reranked",
                        "original_rank": candidate.rank,
                        "rerank_rank": 1,
                        "vector_score": candidate.score,
                        "reranker_score": float(score),
                    }
                ),
            )
            for candidate, score in zip(candidates, scores, strict=True)
        ]
        scored.sort(key=lambda item: (-item[0], item[1], item[2]))
        reranked = [
            item[3].model_copy(update={"rank": rank, "rerank_rank": rank})
            for rank, item in enumerate(scored, start=1)
        ]
        results = reranked[:top_k]
        return RerankedResponse(
            results=results,
            diagnostics=self._diagnostics(
                query,
                vector_ms=vector_ms,
                reranker_ms=reranker_ms,
                candidate_count=len(candidates),
                returned_count=len(results),
                top_k=top_k,
                total_ms=(perf_counter() - started) * 1000,
            ),
        )

    def _diagnostics(
        self,
        query: RetrievalQuery,
        *,
        vector_ms: float,
        reranker_ms: float,
        candidate_count: int,
        returned_count: int,
        top_k: int,
        total_ms: float,
    ) -> RerankedDiagnostics:
        return RerankedDiagnostics(
            vector_retrieval_latency_ms=vector_ms,
            reranker_latency_ms=reranker_ms,
            total_retrieval_latency_ms=total_ms,
            candidate_k=self.config.candidate_k,
            batch_size=self.provider.batch_size,
            pairs_scored=candidate_count,
            candidate_count=candidate_count,
            returned_count=returned_count,
            top_k=top_k,
            filters=query.filters,
        )
