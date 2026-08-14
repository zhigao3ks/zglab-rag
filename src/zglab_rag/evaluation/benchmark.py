from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import numpy as np
from pydantic import BaseModel

from zglab_rag.domain.models import KnowledgeChunk, Visibility
from zglab_rag.embeddings.config import EmbeddingModelConfig
from zglab_rag.evaluation.composition import TextComposition, compose_document_text
from zglab_rag.evaluation.dataset import EvaluationQuery, LoadedEvaluationDataset, QueryCategory
from zglab_rag.evaluation.retrieval import compute_retrieval_metrics, rank_by_cosine
from zglab_rag.ingestion.contracts import EmbeddingProvider


class BenchmarkMetadata(BaseModel):
    timestamp: str
    source_revisions: dict[str, str | None]
    model_id: str
    model_name: str
    embedding_model_config: dict[str, object]
    chunking_config: dict[str, int]
    dataset_version: int
    dataset_sha256: str
    device: str
    composition: TextComposition
    chunk_count: int


class CategoryQualityMetrics(BaseModel):
    query_count: int
    recall_at_1: float
    recall_at_5: float
    recall_at_10: float
    recall_at_20: float
    recall_at_30: float
    mrr: float


class QualityMetrics(BaseModel):
    recall_at_1: float
    recall_at_3: float
    recall_at_5: float
    recall_at_10: float
    recall_at_20: float
    recall_at_30: float
    mrr: float
    evaluated_queries: int
    skipped_queries: int
    skipped_query_ids: list[str]
    category_breakdown: dict[str, CategoryQualityMetrics]


class PerformanceMetrics(BaseModel):
    model_load_seconds: float
    document_embedding_seconds: float
    query_embedding_seconds: float
    query_latency_mean_ms: float
    query_latency_p50_ms: float
    query_latency_p95_ms: float
    embedding_dimension: int
    peak_rss_mb: float | None
    peak_cuda_allocated_mb: float | None


class EmbeddingBenchmarkResult(BaseModel):
    metadata: BenchmarkMetadata
    quality: QualityMetrics
    performance: PerformanceMetrics


class BenchmarkFailure(BaseModel):
    timestamp: str
    model_id: str
    model_name: str
    device: str
    composition: TextComposition
    error_type: str
    error: str


class EmbeddingBenchmarkArtifact(BaseModel):
    schema_version: int = 2
    results: list[EmbeddingBenchmarkResult]
    failures: list[BenchmarkFailure]


def _select_scored_queries(
    dataset: LoadedEvaluationDataset,
    chunks: list[KnowledgeChunk],
) -> tuple[list[EvaluationQuery], list[str]]:
    scored: list[EvaluationQuery] = []
    skipped: list[str] = []
    for query in dataset.dataset.queries:
        all_targets_exist = query.relevant and all(
            any(target.matches(chunk) for chunk in chunks) for target in query.relevant
        )
        if query.needs_review or not all_targets_exist:
            skipped.append(query.id)
        else:
            scored.append(query)
    return scored, skipped


def _compute_category_breakdown(
    queries: list[EvaluationQuery],
    rankings: list[list[int]],
    chunks: list[KnowledgeChunk],
) -> dict[str, CategoryQualityMetrics]:
    breakdown: dict[str, CategoryQualityMetrics] = {}
    for category in QueryCategory:
        category_pairs = [
            (query, ranking)
            for query, ranking in zip(queries, rankings, strict=True)
            if query.category == category
        ]
        if not category_pairs:
            continue
        category_queries = [query for query, _ranking in category_pairs]
        category_rankings = [ranking for _query, ranking in category_pairs]
        metrics = compute_retrieval_metrics(category_queries, category_rankings, chunks)
        breakdown[category.value] = CategoryQualityMetrics(
            query_count=metrics.evaluated_queries,
            recall_at_1=metrics.recall_at[1],
            recall_at_5=metrics.recall_at[5],
            recall_at_10=metrics.recall_at[10],
            recall_at_20=metrics.recall_at[20],
            recall_at_30=metrics.recall_at[30],
            mrr=metrics.mrr,
        )
    return breakdown


def run_embedding_benchmark(
    *,
    chunks: list[KnowledgeChunk],
    dataset: LoadedEvaluationDataset,
    provider: EmbeddingProvider,
    model_config: EmbeddingModelConfig,
    composition: TextComposition,
    source_revisions: dict[str, str | None],
    chunking_config: dict[str, int],
    model_load_seconds: float = 0.0,
    peak_rss_mb: float | None = None,
    peak_cuda_allocated_mb: float | None = None,
    timestamp: str | None = None,
) -> EmbeddingBenchmarkResult:
    if not chunks:
        raise ValueError("benchmark requires at least one chunk")
    private_chunks = [chunk.chunk_id for chunk in chunks if chunk.visibility != Visibility.PUBLIC]
    if private_chunks:
        raise ValueError("benchmark corpus contains non-public chunks")

    scored_queries, skipped_query_ids = _select_scored_queries(dataset, chunks)
    if not scored_queries:
        raise ValueError("no evaluation queries match the selected source corpus")

    document_texts = [compose_document_text(chunk, composition) for chunk in chunks]
    document_start = perf_counter()
    document_embeddings = provider.encode_documents(document_texts)
    document_embedding_seconds = perf_counter() - document_start

    query_embeddings = []
    query_latencies = []
    for query in scored_queries:
        query_start = perf_counter()
        embedding = provider.encode_queries([query.query])
        query_latencies.append(perf_counter() - query_start)
        if embedding.shape != (1, provider.dimension):
            raise ValueError(
                f"query provider returned shape {embedding.shape}; "
                f"expected {(1, provider.dimension)}"
            )
        query_embeddings.append(embedding[0])
    query_matrix = np.asarray(query_embeddings, dtype=np.float32)

    rankings = rank_by_cosine(query_matrix, document_embeddings, chunks)
    metrics = compute_retrieval_metrics(scored_queries, rankings, chunks)
    category_breakdown = _compute_category_breakdown(scored_queries, rankings, chunks)
    latencies_ms = np.asarray(query_latencies, dtype=np.float64) * 1000

    return EmbeddingBenchmarkResult(
        metadata=BenchmarkMetadata(
            timestamp=timestamp or datetime.now(UTC).isoformat(),
            source_revisions=source_revisions,
            model_id=model_config.id,
            model_name=provider.model_name,
            embedding_model_config=model_config.model_dump(mode="json"),
            chunking_config=chunking_config,
            dataset_version=dataset.dataset.version,
            dataset_sha256=dataset.sha256,
            device=provider.device,
            composition=composition,
            chunk_count=len(chunks),
        ),
        quality=QualityMetrics(
            recall_at_1=metrics.recall_at[1],
            recall_at_3=metrics.recall_at[3],
            recall_at_5=metrics.recall_at[5],
            recall_at_10=metrics.recall_at[10],
            recall_at_20=metrics.recall_at[20],
            recall_at_30=metrics.recall_at[30],
            mrr=metrics.mrr,
            evaluated_queries=metrics.evaluated_queries,
            skipped_queries=len(skipped_query_ids),
            skipped_query_ids=skipped_query_ids,
            category_breakdown=category_breakdown,
        ),
        performance=PerformanceMetrics(
            model_load_seconds=model_load_seconds,
            document_embedding_seconds=document_embedding_seconds,
            query_embedding_seconds=float(sum(query_latencies)),
            query_latency_mean_ms=float(latencies_ms.mean()),
            query_latency_p50_ms=float(np.percentile(latencies_ms, 50)),
            query_latency_p95_ms=float(np.percentile(latencies_ms, 95)),
            embedding_dimension=provider.dimension,
            peak_rss_mb=peak_rss_mb,
            peak_cuda_allocated_mb=peak_cuda_allocated_mb,
        ),
    )


def write_benchmark_artifact(
    results: Sequence[EmbeddingBenchmarkResult],
    output_dir: str | Path,
    *,
    failures: Sequence[BenchmarkFailure] = (),
) -> Path:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    output_path = directory / f"embedding-{stamp}.json"
    artifact = EmbeddingBenchmarkArtifact(results=list(results), failures=list(failures))
    output_path.write_text(
        json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output_path
