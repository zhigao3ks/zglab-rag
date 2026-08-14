from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import numpy as np
from pydantic import BaseModel

from zglab_rag.config import get_settings
from zglab_rag.embeddings.sentence_transformer import SentenceTransformerEmbeddingProvider
from zglab_rag.evaluation.dataset import (
    EvaluationQuery,
    LoadedEvaluationDataset,
    QueryCategory,
    load_evaluation_dataset,
)
from zglab_rag.evaluation.retrieval import (
    DEFAULT_RECALL_CUTOFFS,
    RetrievalMetrics,
    compute_ranked_retrieval_metrics,
)
from zglab_rag.indexing.profile import load_active_embedding_profile
from zglab_rag.retrieval.cli import retrieval_config
from zglab_rag.retrieval.config import VectorRetrievalConfig
from zglab_rag.retrieval.contracts import RetrievalFilter, RetrievalQuery, RetrievalResult
from zglab_rag.retrieval.vector import VectorRetriever
from zglab_rag.storage.database import Database
from zglab_rag.storage.repositories import IndexRepository


class MetricSummary(BaseModel):
    query_count: int
    recall_at: dict[int, float]
    hit_rate_at: dict[int, float]
    mrr: float


class ScoreDistribution(BaseModel):
    minimum: float
    median: float
    maximum: float


class LatencyDistribution(BaseModel):
    mean_ms: float
    median_ms: float
    p95_ms: float
    max_ms: float


class HardNegativeDiagnostic(BaseModel):
    query_id: str
    top1_score: float | None
    top1_distance: float | None
    top2_score: float | None
    top1_top2_margin: float | None


class VectorEvaluationResult(BaseModel):
    schema_version: int = 1
    timestamp: str
    dataset_version: int
    dataset_sha256: str
    embedding_profile_id: str
    source_ids: list[str]
    overall: MetricSummary
    category_breakdown: dict[str, MetricSummary]
    positive_top1_score: ScoreDistribution
    hard_negative_top1_score: ScoreDistribution
    hard_negatives: list[HardNegativeDiagnostic]
    model_load_seconds: float
    query_embedding_latency: LatencyDistribution
    vector_search_latency: LatencyDistribution
    total_retrieval_latency: LatencyDistribution


def _metric_summary(metrics: RetrievalMetrics) -> MetricSummary:
    return MetricSummary(
        query_count=metrics.evaluated_queries,
        recall_at=metrics.recall_at,
        hit_rate_at=metrics.hit_rate_at,
        mrr=metrics.mrr,
    )


def _score_distribution(values: Sequence[float]) -> ScoreDistribution:
    if not values:
        raise ValueError("score distribution requires at least one value")
    array = np.asarray(values, dtype=np.float64)
    return ScoreDistribution(
        minimum=float(array.min()),
        median=float(np.median(array)),
        maximum=float(array.max()),
    )


def _latency_distribution(values: Sequence[float]) -> LatencyDistribution:
    if not values:
        raise ValueError("latency distribution requires at least one value")
    array = np.asarray(values, dtype=np.float64)
    return LatencyDistribution(
        mean_ms=float(array.mean()),
        median_ms=float(np.median(array)),
        p95_ms=float(np.percentile(array, 95)),
        max_ms=float(array.max()),
    )


def _target_exists(query: EvaluationQuery, evidence: set[tuple[str, str, tuple[str, ...]]]) -> bool:
    return all(
        any(
            source_id == target.source_id
            and source_path == target.source_path
            and section_path[: len(target.section_path)] == tuple(target.section_path)
            for source_id, source_path, section_path in evidence
        )
        for target in query.relevant
    )


def select_queries(
    dataset: LoadedEvaluationDataset,
    evidence: set[tuple[str, str, tuple[str, ...]]],
) -> tuple[list[EvaluationQuery], list[EvaluationQuery]]:
    scored = [
        query
        for query in dataset.dataset.queries
        if query.category != QueryCategory.HARD_NEGATIVE
        and not query.needs_review
        and query.relevant
        and _target_exists(query, evidence)
    ]
    hard_negatives = [
        query
        for query in dataset.dataset.queries
        if query.category == QueryCategory.HARD_NEGATIVE
    ]
    if not scored:
        raise ValueError("no scored evaluation queries match the persistent index")
    return scored, hard_negatives


def run_vector_retrieval_evaluation(
    *,
    retriever: VectorRetriever,
    dataset: LoadedEvaluationDataset,
    evidence: set[tuple[str, str, tuple[str, ...]]],
    source_ids: Sequence[str],
    embedding_profile_id: str,
    model_load_seconds: float = 0.0,
) -> VectorEvaluationResult:
    scored_queries, hard_negative_queries = select_queries(dataset, evidence)
    filters = RetrievalFilter(source_ids=tuple(source_ids))
    rankings: list[list[RetrievalResult]] = []
    positive_scores: list[float] = []
    embedding_latencies: list[float] = []
    search_latencies: list[float] = []
    total_latencies: list[float] = []
    ranking_depth = retriever.corpus_size

    for query in scored_queries:
        response = retriever.retrieve(
            RetrievalQuery(text=query.query, top_k=ranking_depth, filters=filters)
        )
        rankings.append(response.results)
        if response.results:
            positive_scores.append(response.results[0].score)
        embedding_latencies.append(response.diagnostics.query_embedding_latency_ms)
        search_latencies.append(response.diagnostics.vector_search_latency_ms)
        total_latencies.append(response.diagnostics.total_retrieval_latency_ms)

    metrics = compute_ranked_retrieval_metrics(scored_queries, rankings)
    category_breakdown = {}
    for category in QueryCategory:
        pairs = [
            (query, ranking)
            for query, ranking in zip(scored_queries, rankings, strict=True)
            if query.category == category
        ]
        if pairs:
            category_breakdown[category.value] = _metric_summary(
                compute_ranked_retrieval_metrics(
                    [query for query, _ranking in pairs],
                    [ranking for _query, ranking in pairs],
                )
            )

    hard_diagnostics = []
    hard_scores = []
    for query in hard_negative_queries:
        response = retriever.retrieve(RetrievalQuery(text=query.query, top_k=2, filters=filters))
        first = response.results[0] if response.results else None
        second = response.results[1] if len(response.results) > 1 else None
        if first is not None:
            hard_scores.append(first.score)
        hard_diagnostics.append(
            HardNegativeDiagnostic(
                query_id=query.id,
                top1_score=None if first is None else first.score,
                top1_distance=None if first is None else first.distance,
                top2_score=None if second is None else second.score,
                top1_top2_margin=(
                    None if first is None or second is None else first.score - second.score
                ),
            )
        )
        embedding_latencies.append(response.diagnostics.query_embedding_latency_ms)
        search_latencies.append(response.diagnostics.vector_search_latency_ms)
        total_latencies.append(response.diagnostics.total_retrieval_latency_ms)

    return VectorEvaluationResult(
        timestamp=datetime.now(UTC).isoformat(),
        dataset_version=dataset.dataset.version,
        dataset_sha256=dataset.sha256,
        embedding_profile_id=embedding_profile_id,
        source_ids=sorted(set(source_ids)),
        overall=_metric_summary(metrics),
        category_breakdown=category_breakdown,
        positive_top1_score=_score_distribution(positive_scores),
        hard_negative_top1_score=_score_distribution(hard_scores),
        hard_negatives=hard_diagnostics,
        model_load_seconds=model_load_seconds,
        query_embedding_latency=_latency_distribution(embedding_latencies),
        vector_search_latency=_latency_distribution(search_latencies),
        total_retrieval_latency=_latency_distribution(total_latencies),
    )


def _evidence(connection, source_ids: Sequence[str]):
    placeholders = ",".join("?" for _ in source_ids)
    rows = connection.execute(
        f"""
        SELECT source_id, source_path, section_path_json
        FROM chunks
        WHERE visibility='public' AND source_id IN ({placeholders})
        """,
        tuple(source_ids),
    ).fetchall()
    return {
        (row["source_id"], row["source_path"], tuple(json.loads(row["section_path_json"])))
        for row in rows
    }


def _print_metrics(label: str, metrics: MetricSummary) -> None:
    print(f"{label}: queries={metrics.query_count}")
    print(
        "  "
        + " ".join(
            f"Recall@{cutoff}={metrics.recall_at[cutoff]:.4f}"
            for cutoff in DEFAULT_RECALL_CUTOFFS
        )
    )
    print(
        "  "
        + " ".join(
            f"HitRate@{cutoff}={metrics.hit_rate_at[cutoff]:.4f}"
            for cutoff in (1, 3, 5, 10, 20)
        )
        + f" MRR={metrics.mrr:.4f}"
    )


def _write_artifact(result: VectorEvaluationResult, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    path = output_dir / f"vector-retrieval-{stamp}.json"
    path.write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate persistent sqlite-vec retrieval")
    parser.add_argument("--source", action="append", required=True, dest="source_ids")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--dataset", type=Path, default=Path("evaluation/retrieval.yaml"))
    parser.add_argument(
        "--models-config", type=Path, default=Path("config/embedding-models.yaml")
    )
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/evaluation"))
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--batch-size", type=int, default=32)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = get_settings()
    database = Database(args.database or settings.database_path)
    if not database.path.is_file():
        print(
            f"error: persistent index does not exist at {database.path}; "
            "run the Phase 4 indexing build first",
            file=sys.stderr,
        )
        return 1
    connection = None
    try:
        profile, model_config = load_active_embedding_profile(args.models_config)
        load_started = perf_counter()
        provider = SentenceTransformerEmbeddingProvider(
            model_config,
            device=args.device,
            batch_size=args.batch_size,
        )
        model_load_seconds = perf_counter() - load_started
        connection = database.connect(read_only=True, initialize=False)
        base_config = retrieval_config(settings)
        corpus_size = IndexRepository(connection).vector_count()
        evaluation_config = VectorRetrievalConfig(
            default_top_k=base_config.default_top_k,
            max_top_k=max(base_config.max_top_k, corpus_size),
            candidate_factor=base_config.candidate_factor,
            minimum_candidate_k=base_config.minimum_candidate_k,
            maximum_candidate_k=max(base_config.maximum_candidate_k, corpus_size),
        )
        retriever = VectorRetriever(
            connection,
            provider,
            profile,
            model_config=model_config,
            config=evaluation_config,
        )
        result = run_vector_retrieval_evaluation(
            retriever=retriever,
            dataset=load_evaluation_dataset(args.dataset),
            evidence=_evidence(connection, args.source_ids),
            source_ids=args.source_ids,
            embedding_profile_id=profile.profile_id,
            model_load_seconds=model_load_seconds,
        )
        _print_metrics("overall", result.overall)
        print("category breakdown:")
        for category, metrics in result.category_breakdown.items():
            _print_metrics(f"  {category}", metrics)
        print(
            "positive top1 score: "
            f"min={result.positive_top1_score.minimum:.4f} "
            f"median={result.positive_top1_score.median:.4f} "
            f"max={result.positive_top1_score.maximum:.4f}"
        )
        print(
            "hard-negative top1 score: "
            f"min={result.hard_negative_top1_score.minimum:.4f} "
            f"median={result.hard_negative_top1_score.median:.4f} "
            f"max={result.hard_negative_top1_score.maximum:.4f}"
        )
        for item in result.hard_negatives:
            print(
                f"  {item.query_id}: top1_score={item.top1_score:.4f} "
                f"top1_distance={item.top1_distance:.4f} top2_score={item.top2_score:.4f} "
                f"margin={item.top1_top2_margin:.4f}"
            )
        for label, latency in (
            ("query embedding", result.query_embedding_latency),
            ("sqlite-vec search", result.vector_search_latency),
            ("total retrieval", result.total_retrieval_latency),
        ):
            print(
                f"{label} latency ms: mean={latency.mean_ms:.3f} "
                f"median={latency.median_ms:.3f} p95={latency.p95_ms:.3f} "
                f"max={latency.max_ms:.3f}"
            )
        artifact = _write_artifact(result, args.output_dir)
        print(f"artifact: {artifact}")
        return 0
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
