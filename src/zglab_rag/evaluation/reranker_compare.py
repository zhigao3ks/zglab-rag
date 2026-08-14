from __future__ import annotations

import argparse
import json
import resource
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import psutil
from pydantic import BaseModel

from zglab_rag.config import get_settings
from zglab_rag.embeddings.sentence_transformer import SentenceTransformerEmbeddingProvider
from zglab_rag.evaluation.dataset import EvaluationQuery, QueryCategory, load_evaluation_dataset
from zglab_rag.evaluation.retrieval import (
    DEFAULT_RECALL_CUTOFFS,
    compute_ranked_retrieval_metrics,
)
from zglab_rag.evaluation.vector_retrieval import (
    LatencyDistribution,
    MetricSummary,
    ScoreDistribution,
    _evidence,
    _latency_distribution,
    _metric_summary,
    _print_metrics,
    _score_distribution,
    select_queries,
)
from zglab_rag.indexing.profile import load_active_embedding_profile
from zglab_rag.reranking.config import RerankerModelRegistry
from zglab_rag.reranking.cross_encoder import CrossEncoderRerankerProvider
from zglab_rag.reranking.service import RerankedRetriever, RerankerRetrievalConfig
from zglab_rag.retrieval.cli import retrieval_config
from zglab_rag.retrieval.config import VectorRetrievalConfig
from zglab_rag.retrieval.contracts import RetrievalFilter, RetrievalQuery, RetrievalResult
from zglab_rag.retrieval.vector import VectorRetriever
from zglab_rag.storage.database import Database
from zglab_rag.storage.repositories import IndexRepository


class PromotionCase(BaseModel):
    query_id: str
    category: str
    chunk_id: str | None
    original_rank: int | None
    rerank_rank: int | None
    rank_change: int | None


class PromotionSummary(BaseModel):
    promoted: int
    unchanged: int
    demoted: int
    missing: int
    cases: list[PromotionCase]


class RerankerHardNegative(BaseModel):
    query_id: str
    top1_chunk_id: str | None
    top1_score: float | None
    top2_score: float | None
    top1_top2_margin: float | None


class CandidateEvaluation(BaseModel):
    candidate_k: int
    vector: MetricSummary
    reranked: MetricSummary
    vector_category_breakdown: dict[str, MetricSummary]
    reranked_category_breakdown: dict[str, MetricSummary]
    reranker_minus_vector: dict[str, float]
    recall_at_candidate_invariant: bool
    promotions: PromotionSummary
    relevant_reranker_scores: ScoreDistribution | None
    hard_negative_reranker_scores: ScoreDistribution | None
    hard_negatives: list[RerankerHardNegative]
    vector_latency: LatencyDistribution
    reranker_latency: LatencyDistribution
    total_latency: LatencyDistribution
    batch_size: int
    pairs_scored: int


class MemoryMeasurement(BaseModel):
    rss_before_reranker_load_mb: float
    rss_after_reranker_load_mb: float
    peak_rss_mb: float


class RerankerComparison(BaseModel):
    schema_version: int = 1
    timestamp: str
    dataset_version: int
    dataset_sha256: str
    embedding_profile_id: str
    reranker_model_id: str
    reranker_model_name: str
    backend: str
    device: str
    source_ids: list[str]
    embedding_model_load_seconds: float
    reranker_model_load_seconds: float
    memory: MemoryMeasurement
    experiments: dict[int, CandidateEvaluation]


def _matches(query: EvaluationQuery, result: RetrievalResult) -> bool:
    return any(
        target.source_id == result.source_id
        and target.source_path == result.source_path
        and (
            not target.section_path
            or result.section_path[: len(target.section_path)] == target.section_path
        )
        for target in query.relevant
    )


def _category_metrics(queries, rankings) -> dict[str, MetricSummary]:
    breakdown = {}
    for category in QueryCategory:
        pairs = [
            (query, ranking)
            for query, ranking in zip(queries, rankings, strict=True)
            if query.category == category
        ]
        if pairs:
            breakdown[category.value] = _metric_summary(
                compute_ranked_retrieval_metrics(
                    [query for query, _ranking in pairs],
                    [ranking for _query, ranking in pairs],
                )
            )
    return breakdown


def _promotion_case(
    query: EvaluationQuery,
    original: list[RetrievalResult],
    reranked: list[RetrievalResult],
) -> PromotionCase:
    original_hit = next((item for item in original if _matches(query, item)), None)
    reranked_hit = next((item for item in reranked if _matches(query, item)), None)
    original_rank = None if original_hit is None else original_hit.rank
    rerank_rank = None if reranked_hit is None else reranked_hit.rank
    change = (
        None
        if original_rank is None or rerank_rank is None
        else original_rank - rerank_rank
    )
    return PromotionCase(
        query_id=query.id,
        category=query.category.value,
        chunk_id=None if reranked_hit is None else reranked_hit.chunk_id,
        original_rank=original_rank,
        rerank_rank=rerank_rank,
        rank_change=change,
    )


def _promotion_summary(cases: list[PromotionCase]) -> PromotionSummary:
    ranked_cases = [case for case in cases if case.rank_change is not None]
    selected = sorted(
        ranked_cases,
        key=lambda case: (-abs(case.rank_change or 0), case.query_id),
    )[:5]
    return PromotionSummary(
        promoted=sum((case.rank_change or 0) > 0 for case in ranked_cases),
        unchanged=sum(case.rank_change == 0 for case in ranked_cases),
        demoted=sum((case.rank_change or 0) < 0 for case in ranked_cases),
        missing=len(cases) - len(ranked_cases),
        cases=selected,
    )


def _optional_distribution(values: Sequence[float]) -> ScoreDistribution | None:
    return _score_distribution(values) if values else None


def evaluate_candidate_k(
    retriever: RerankedRetriever,
    *,
    dataset,
    evidence,
    source_ids: Sequence[str],
) -> CandidateEvaluation:
    scored_queries, hard_queries = select_queries(dataset, evidence)
    filters = RetrievalFilter(source_ids=tuple(source_ids))
    vector_rankings: list[list[RetrievalResult]] = []
    reranked_rankings: list[list[RetrievalResult]] = []
    cases: list[PromotionCase] = []
    relevant_scores: list[float] = []
    vector_latencies: list[float] = []
    reranker_latencies: list[float] = []
    total_latencies: list[float] = []
    pairs_scored = 0
    candidate_k = retriever.config.candidate_k

    for query in scored_queries:
        response = retriever.retrieve(
            RetrievalQuery(text=query.query, top_k=candidate_k, filters=filters)
        )
        reranked = response.results
        original = sorted(reranked, key=lambda item: (item.original_rank or 0, item.chunk_id))
        original = [
            item.model_copy(update={"rank": index})
            for index, item in enumerate(original, 1)
        ]
        vector_rankings.append(original)
        reranked_rankings.append(reranked)
        cases.append(_promotion_case(query, original, reranked))
        relevant_scores.extend(
            item.reranker_score
            for item in reranked
            if item.reranker_score is not None and _matches(query, item)
        )
        vector_latencies.append(response.diagnostics.vector_retrieval_latency_ms)
        reranker_latencies.append(response.diagnostics.reranker_latency_ms)
        total_latencies.append(response.diagnostics.total_retrieval_latency_ms)
        pairs_scored += response.diagnostics.pairs_scored

    vector_metrics = compute_ranked_retrieval_metrics(scored_queries, vector_rankings)
    reranked_metrics = compute_ranked_retrieval_metrics(scored_queries, reranked_rankings)
    invariant_cutoff = min(candidate_k, max(DEFAULT_RECALL_CUTOFFS))
    invariant = (
        vector_metrics.recall_at[invariant_cutoff]
        == reranked_metrics.recall_at[invariant_cutoff]
    )
    if not invariant:
        raise RuntimeError(f"Recall@{invariant_cutoff} changed during candidate reordering")

    hard_diagnostics = []
    hard_scores: list[float] = []
    for query in hard_queries:
        response = retriever.retrieve(
            RetrievalQuery(text=query.query, top_k=candidate_k, filters=filters)
        )
        first = response.results[0] if response.results else None
        second = response.results[1] if len(response.results) > 1 else None
        if first is not None and first.reranker_score is not None:
            hard_scores.append(first.reranker_score)
        hard_diagnostics.append(
            RerankerHardNegative(
                query_id=query.id,
                top1_chunk_id=None if first is None else first.chunk_id,
                top1_score=None if first is None else first.reranker_score,
                top2_score=None if second is None else second.reranker_score,
                top1_top2_margin=(
                    None
                    if first is None or second is None
                    else first.score - second.score
                ),
            )
        )
        vector_latencies.append(response.diagnostics.vector_retrieval_latency_ms)
        reranker_latencies.append(response.diagnostics.reranker_latency_ms)
        total_latencies.append(response.diagnostics.total_retrieval_latency_ms)
        pairs_scored += response.diagnostics.pairs_scored

    vector_summary = _metric_summary(vector_metrics)
    reranked_summary = _metric_summary(reranked_metrics)
    deltas = {
        f"recall_at_{cutoff}": (
            reranked_summary.recall_at[cutoff] - vector_summary.recall_at[cutoff]
        )
        for cutoff in (1, 3, 5)
    }
    deltas["mrr"] = reranked_summary.mrr - vector_summary.mrr
    return CandidateEvaluation(
        candidate_k=candidate_k,
        vector=vector_summary,
        reranked=reranked_summary,
        vector_category_breakdown=_category_metrics(scored_queries, vector_rankings),
        reranked_category_breakdown=_category_metrics(scored_queries, reranked_rankings),
        reranker_minus_vector=deltas,
        recall_at_candidate_invariant=invariant,
        promotions=_promotion_summary(cases),
        relevant_reranker_scores=_optional_distribution(relevant_scores),
        hard_negative_reranker_scores=_optional_distribution(hard_scores),
        hard_negatives=hard_diagnostics,
        vector_latency=_latency_distribution(vector_latencies),
        reranker_latency=_latency_distribution(reranker_latencies),
        total_latency=_latency_distribution(total_latencies),
        batch_size=retriever.provider.batch_size,
        pairs_scored=pairs_scored,
    )


def _print_experiment(experiment: CandidateEvaluation) -> None:
    print(f"candidate_k={experiment.candidate_k}")
    _print_metrics("  vector", experiment.vector)
    _print_metrics("  reranked", experiment.reranked)
    print("  Reranker - Vector:")
    for metric, value in experiment.reranker_minus_vector.items():
        print(f"    {metric}={value:+.4f}")
    print("  category breakdown:")
    for category in experiment.vector_category_breakdown:
        _print_metrics(f"    vector/{category}", experiment.vector_category_breakdown[category])
        _print_metrics(
            f"    reranked/{category}",
            experiment.reranked_category_breakdown[category],
        )
    print(f"  promotions: {experiment.promotions.model_dump(mode='json')}")
    hard_negatives = [
        item.model_dump(mode="json") for item in experiment.hard_negatives
    ]
    print(f"  hard negatives: {hard_negatives}")
    for label, latency in (
        ("vector", experiment.vector_latency),
        ("reranker", experiment.reranker_latency),
        ("total", experiment.total_latency),
    ):
        print(
            f"  {label} ms mean/median/p95/max={latency.mean_ms:.3f}/"
            f"{latency.median_ms:.3f}/{latency.p95_ms:.3f}/{latency.max_ms:.3f}"
        )
    print(
        f"  batch_size={experiment.batch_size} pairs_scored={experiment.pairs_scored} "
        f"recall_invariant={experiment.recall_at_candidate_invariant}"
    )


def _write_artifact(result: RerankerComparison, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    path = output_dir / f"reranker-compare-{stamp}.json"
    path.write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare Vector and Vector + Reranker")
    parser.add_argument("--candidate-k", type=int, choices=(10, 20, 30), action="append")
    parser.add_argument("--source", action="append", default=[], dest="source_ids")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--dataset", type=Path, default=Path("evaluation/retrieval.yaml"))
    parser.add_argument(
        "--embedding-models-config",
        type=Path,
        default=Path("config/embedding-models.yaml"),
    )
    parser.add_argument(
        "--reranker-models-config",
        type=Path,
        default=Path("config/reranker-models.yaml"),
    )
    parser.add_argument(
        "--reranker-model",
        default="mmarco-mMiniLMv2-L12-H384-v1",
    )
    parser.add_argument("--reranker-model-path", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/evaluation"))
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--embedding-batch-size", type=int, default=32)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    candidate_values = sorted(set(args.candidate_k or [10, 20]))
    settings = get_settings()
    database = Database(args.database or settings.database_path)
    connection = None
    try:
        profile, embedding_config = load_active_embedding_profile(
            args.embedding_models_config
        )
        embedding_started = perf_counter()
        embedding_provider = SentenceTransformerEmbeddingProvider(
            embedding_config,
            device=args.device,
            batch_size=args.embedding_batch_size,
        )
        embedding_load_seconds = perf_counter() - embedding_started
        reranker_config = RerankerModelRegistry.from_yaml(
            args.reranker_models_config
        ).get_enabled(args.reranker_model)
        process = psutil.Process()
        rss_before = process.memory_info().rss / (1024 * 1024)
        reranker_started = perf_counter()
        reranker_provider = CrossEncoderRerankerProvider(
            reranker_config,
            device=args.device,
            model_path=args.reranker_model_path,
        )
        reranker_load_seconds = perf_counter() - reranker_started
        rss_after = process.memory_info().rss / (1024 * 1024)

        connection = database.connect(read_only=True, initialize=False)
        repository = IndexRepository(connection)
        source_ids = args.source_ids or [
            row["source_id"]
            for row in repository.source_snapshots()
            if row["visibility"] == "public"
        ]
        base_vector = retrieval_config(settings)
        maximum_candidate = max(candidate_values)
        vector = VectorRetriever(
            connection,
            embedding_provider,
            profile,
            model_config=embedding_config,
            config=VectorRetrievalConfig(
                default_top_k=base_vector.default_top_k,
                max_top_k=max(base_vector.max_top_k, maximum_candidate),
                candidate_factor=base_vector.candidate_factor,
                minimum_candidate_k=base_vector.minimum_candidate_k,
                maximum_candidate_k=max(base_vector.maximum_candidate_k, maximum_candidate),
            ),
        )
        dataset = load_evaluation_dataset(args.dataset)
        evidence = _evidence(connection, source_ids)
        experiments = {}
        for candidate_k in candidate_values:
            retriever = RerankedRetriever(
                vector,
                reranker_provider,
                config=RerankerRetrievalConfig(
                    default_top_k=min(settings.reranker_default_top_k, candidate_k),
                    maximum_top_k=candidate_k,
                    candidate_k=candidate_k,
                ),
            )
            experiments[candidate_k] = evaluate_candidate_k(
                retriever,
                dataset=dataset,
                evidence=evidence,
                source_ids=source_ids,
            )
        peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        result = RerankerComparison(
            timestamp=datetime.now(UTC).isoformat(),
            dataset_version=dataset.dataset.version,
            dataset_sha256=dataset.sha256,
            embedding_profile_id=profile.profile_id,
            reranker_model_id=reranker_config.id,
            reranker_model_name=reranker_config.model_name,
            backend=reranker_config.backend.value,
            device=args.device,
            source_ids=source_ids,
            embedding_model_load_seconds=embedding_load_seconds,
            reranker_model_load_seconds=reranker_load_seconds,
            memory=MemoryMeasurement(
                rss_before_reranker_load_mb=rss_before,
                rss_after_reranker_load_mb=rss_after,
                peak_rss_mb=peak_rss,
            ),
            experiments=experiments,
        )
        for experiment in experiments.values():
            _print_experiment(experiment)
        print(f"embedding model load seconds={embedding_load_seconds:.3f}")
        print(f"reranker model load seconds={reranker_load_seconds:.3f}")
        print(f"memory MB={result.memory.model_dump(mode='json')}")
        print(f"artifact: {_write_artifact(result, args.output_dir)}")
        return 0
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
