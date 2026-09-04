from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from pydantic import BaseModel

from zglab_rag.config import get_settings
from zglab_rag.embeddings.sentence_transformer import SentenceTransformerEmbeddingProvider
from zglab_rag.evaluation.dataset import QueryCategory, QuerySubset, load_evaluation_dataset
from zglab_rag.evaluation.retrieval import compute_ranked_retrieval_metrics
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
from zglab_rag.retrieval.cli import hybrid_config, retrieval_config
from zglab_rag.retrieval.config import (
    HybridRetrievalConfig,
    VectorRetrievalConfig,
)
from zglab_rag.retrieval.contracts import RetrievalFilter, RetrievalQuery
from zglab_rag.retrieval.graph import GraphRetriever
from zglab_rag.retrieval.hierarchical import HierarchicalRetriever
from zglab_rag.retrieval.hybrid import HybridRetriever
from zglab_rag.retrieval.intelligent import IntelligentRetriever
from zglab_rag.retrieval.lexical import LexicalRetriever
from zglab_rag.retrieval.vector import VectorRetriever
from zglab_rag.storage.database import Database
from zglab_rag.storage.repositories import IndexRepository


class HardNegativeDiagnostic(BaseModel):
    query_id: str
    top1_chunk_id: str | None
    top1_source_id: str | None
    top1_score: float | None
    top1_distance: float | None
    top1_raw_bm25: float | None
    top2_score: float | None
    top1_top2_margin: float | None


class ModeEvaluation(BaseModel):
    overall: MetricSummary
    category_breakdown: dict[str, MetricSummary]
    positive_top1_score: ScoreDistribution | None
    hard_negative_top1_score: ScoreDistribution | None
    hard_negatives: list[HardNegativeDiagnostic]
    latency: LatencyDistribution
    subset_breakdown: dict[str, MetricSummary]


class RetrievalComparison(BaseModel):
    schema_version: int = 1
    timestamp: str
    dataset_version: int
    dataset_sha256: str
    embedding_profile_id: str
    lexical_profile_id: str
    source_ids: list[str]
    model_load_seconds: float
    modes: dict[str, ModeEvaluation]
    hybrid_minus_vector: dict[str, float]
    mode_config: dict[str, Any] = {}
    recommendation: str = "KEEP VECTOR DEFAULT"
    corpus_snapshot: dict[str, Any] = {}


def _total_latency(response: Any) -> float:
    return float(response.diagnostics.total_retrieval_latency_ms)


def _optional_distribution(values: Sequence[float]) -> ScoreDistribution | None:
    return _score_distribution(values) if values else None


def evaluate_mode(
    retriever: Any,
    *,
    scored_queries,
    hard_negative_queries,
    filters: RetrievalFilter,
) -> ModeEvaluation:
    rankings = []
    positive_scores: list[float] = []
    hard_scores: list[float] = []
    latencies: list[float] = []
    ranking_depth = min(retriever.corpus_size, 10)
    for query in scored_queries:
        response = retriever.retrieve(
            RetrievalQuery(text=query.query, top_k=ranking_depth, filters=filters)
        )
        rankings.append(response.results)
        if response.results:
            positive_scores.append(response.results[0].score)
        latencies.append(_total_latency(response))

    overall = _metric_summary(compute_ranked_retrieval_metrics(scored_queries, rankings))
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

    subset_breakdown = {}
    for subset in QuerySubset:
        pairs = [
            (query, ranking)
            for query, ranking in zip(scored_queries, rankings, strict=True)
            if subset in query.subsets
        ]
        if pairs:
            subset_breakdown[subset.value] = _metric_summary(
                compute_ranked_retrieval_metrics(
                    [query for query, _ in pairs], [ranking for _, ranking in pairs]
                )
            )

    hard_diagnostics = []
    for query in hard_negative_queries:
        response = retriever.retrieve(RetrievalQuery(text=query.query, top_k=2, filters=filters))
        first = response.results[0] if response.results else None
        second = response.results[1] if len(response.results) > 1 else None
        if first is not None:
            hard_scores.append(first.score)
        hard_diagnostics.append(
            HardNegativeDiagnostic(
                query_id=query.id,
                top1_chunk_id=None if first is None else first.chunk_id,
                top1_source_id=None if first is None else first.source_id,
                top1_score=None if first is None else first.score,
                top1_distance=None if first is None else first.distance,
                top1_raw_bm25=None if first is None else first.raw_bm25,
                top2_score=None if second is None else second.score,
                top1_top2_margin=(
                    None if first is None or second is None else first.score - second.score
                ),
            )
        )
        latencies.append(_total_latency(response))
    return ModeEvaluation(
        overall=overall,
        category_breakdown=category_breakdown,
        positive_top1_score=_optional_distribution(positive_scores),
        hard_negative_top1_score=_optional_distribution(hard_scores),
        hard_negatives=hard_diagnostics,
        latency=_latency_distribution(latencies),
        subset_breakdown=subset_breakdown,
    )


def run_retrieval_comparison(
    retrievers: Mapping[str, Any],
    *,
    dataset,
    evidence,
    source_ids: Sequence[str],
    embedding_profile_id: str,
    lexical_profile_id: str,
    model_load_seconds: float = 0.0,
    corpus_snapshot: Mapping[str, Any] | None = None,
    mode_config: Mapping[str, Any] | None = None,
) -> RetrievalComparison:
    scored, hard = select_queries(dataset, evidence)
    filters = RetrievalFilter(source_ids=tuple(source_ids))
    modes = {
        name: evaluate_mode(
            retriever,
            scored_queries=scored,
            hard_negative_queries=hard,
            filters=filters,
        )
        for name, retriever in retrievers.items()
    }
    vector = modes["vector"].overall
    hybrid = modes["hybrid"].overall
    deltas = {
        f"recall_at_{cutoff}": hybrid.recall_at[cutoff] - vector.recall_at[cutoff]
        for cutoff in (1, 5, 10, 20)
    }
    deltas["mrr"] = hybrid.mrr - vector.mrr
    intelligent = modes.get("intelligent")
    recommendation = "KEEP VECTOR DEFAULT"
    if intelligent is not None:
        vector_subsets = modes["vector"].subset_breakdown
        targeted_gain = False
        for name, metric in intelligent.subset_breakdown.items():
            baseline = vector_subsets.get(name)
            if baseline is not None and (
                metric.recall_at.get(5, 0.0) - baseline.recall_at.get(5, 0.0) >= 0.05
                or metric.mrr - baseline.mrr >= 0.05
            ):
                targeted_gain = True
        if (
            intelligent.overall.recall_at.get(5, 0.0) >= vector.recall_at.get(5, 0.0) - 0.01
            and intelligent.overall.mrr >= vector.mrr - 0.01
            and targeted_gain
            and intelligent.latency.p95_ms <= max(250.0, modes["vector"].latency.p95_ms * 5)
        ):
            recommendation = "INTELLIGENT IS A PRODUCTION CANDIDATE"
    return RetrievalComparison(
        timestamp=datetime.now(UTC).isoformat(),
        dataset_version=dataset.dataset.version,
        dataset_sha256=dataset.sha256,
        embedding_profile_id=embedding_profile_id,
        lexical_profile_id=lexical_profile_id,
        source_ids=sorted(set(source_ids)),
        model_load_seconds=model_load_seconds,
        modes=modes,
        hybrid_minus_vector=deltas,
        recommendation=recommendation,
        corpus_snapshot=dict(corpus_snapshot or {}),
        mode_config=dict(mode_config or {}),
    )


def _write_artifact(result: RetrievalComparison, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    path = output_dir / f"retrieval-compare-{stamp}.json"
    path.write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _write_phase16_report(result: RetrievalComparison, json_path: Path, md_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Phase 16 Retrieval Evaluation",
        "",
        f"Generated by the benchmark command at `{result.timestamp}`.",
        f"Dataset SHA-256: `{result.dataset_sha256}`; "
        f"embedding profile: `{result.embedding_profile_id}`.",
        "",
        "| Mode | Queries | R@1 | R@3 | R@5 | R@10 | MRR | Mean ms | p50 ms | p95 ms |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, mode in result.modes.items():
        metric = mode.overall
        latency = mode.latency
        lines.append(
            f"| {name} | {metric.query_count} | {metric.recall_at.get(1, 0):.3f} | "
            f"{metric.recall_at.get(3, 0):.3f} | {metric.recall_at.get(5, 0):.3f} | "
            f"{metric.recall_at.get(10, 0):.3f} | {metric.mrr:.3f} | {latency.mean_ms:.2f} | "
            f"{latency.median_ms:.2f} | {latency.p95_ms:.2f} |"
        )
    lines.extend(["", "## Phase 16 subsets", ""])
    for name, mode in result.modes.items():
        for subset, metric in mode.subset_breakdown.items():
            lines.append(
                f"- {name}/{subset}: Recall@5={metric.recall_at.get(5, 0):.3f}, "
                f"MRR={metric.mrr:.3f} (n={metric.query_count})"
            )
    lines.extend(
        [
            "",
            "## Recommendation",
            "",
            f"**{result.recommendation}**. Production remains configured for vector retrieval; "
            "any mode switch requires a separate production acceptance.",
            "",
        ]
    )
    md_path.write_text("\n".join(lines), encoding="utf-8")


def _print_result(result: RetrievalComparison) -> None:
    for mode, evaluation in result.modes.items():
        _print_metrics(f"{mode} overall", evaluation.overall)
        for category, metrics in evaluation.category_breakdown.items():
            _print_metrics(f"  {mode}/{category}", metrics)
        score = evaluation.positive_top1_score
        hard_score = evaluation.hard_negative_top1_score
        if score is not None:
            print(
                f"  positive score min/median/max="
                f"{score.minimum:.6f}/{score.median:.6f}/{score.maximum:.6f}"
            )
        if hard_score is not None:
            print(
                f"  hard-negative score min/median/max="
                f"{hard_score.minimum:.6f}/{hard_score.median:.6f}/{hard_score.maximum:.6f}"
            )
        for item in evaluation.hard_negatives:
            print(f"  hard-negative {item.model_dump(mode='json')}")
        latency = evaluation.latency
        print(
            f"  latency ms mean/median/p95/max={latency.mean_ms:.3f}/"
            f"{latency.median_ms:.3f}/{latency.p95_ms:.3f}/{latency.max_ms:.3f}"
        )
    print("Hybrid - Vector:")
    for metric, value in result.hybrid_minus_vector.items():
        print(f"  {metric}={value:+.4f}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare vector, lexical and hybrid retrieval")
    parser.add_argument("--source", action="append", default=[], dest="source_ids")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--dataset", type=Path, default=Path("evaluation/retrieval.yaml"))
    parser.add_argument(
        "--models-config", type=Path, default=Path("config/embedding-models.yaml")
    )
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/evaluation"))
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--phase16-json", type=Path)
    parser.add_argument("--phase16-markdown", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = get_settings()
    database = Database(args.database or settings.database_path)
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
        repository = IndexRepository(connection)
        source_ids = args.source_ids or [
            row["source_id"]
            for row in repository.source_snapshots()
            if row["visibility"] == "public"
        ]
        corpus_size = repository.vector_count()
        base_vector = retrieval_config(settings)
        evaluation_vector = VectorRetrievalConfig(
            default_top_k=base_vector.default_top_k,
            max_top_k=max(base_vector.max_top_k, corpus_size),
            candidate_factor=base_vector.candidate_factor,
            minimum_candidate_k=base_vector.minimum_candidate_k,
            maximum_candidate_k=max(base_vector.maximum_candidate_k, corpus_size),
        )
        vector = VectorRetriever(
            connection,
            provider,
            profile,
            model_config=model_config,
            config=evaluation_vector,
        )
        lexical = LexicalRetriever(connection, config=evaluation_vector)
        base_hybrid = hybrid_config(settings)
        hybrid = HybridRetriever(
            vector,
            lexical,
            config=HybridRetrievalConfig(
                default_top_k=base_hybrid.default_top_k,
                max_top_k=max(base_hybrid.max_top_k, corpus_size),
                vector_candidate_k=corpus_size,
                lexical_candidate_k=corpus_size,
                rrf_k=base_hybrid.rrf_k,
                vector_weight=base_hybrid.vector_weight,
                lexical_weight=base_hybrid.lexical_weight,
            ),
        )
        hierarchical = HierarchicalRetriever(connection, lexical)
        graph = GraphRetriever(connection, lexical)
        intelligent = IntelligentRetriever(hybrid, hierarchical, graph)
        result = run_retrieval_comparison(
            {
                "vector": vector,
                "lexical": lexical,
                "hybrid": hybrid,
                "hierarchical": hierarchical,
                "graph": graph,
                "intelligent": intelligent,
            },
            dataset=load_evaluation_dataset(args.dataset),
            evidence=_evidence(connection, source_ids),
            source_ids=source_ids,
            embedding_profile_id=profile.profile_id,
            lexical_profile_id=lexical.profile.profile_id,
            model_load_seconds=model_load_seconds,
            corpus_snapshot={
                "database_schema_version": Database.versions(connection).schema,
                "retrieval_structure_version": connection.execute(
                    "SELECT value FROM index_metadata WHERE key='retrieval_structure_version'"
                ).fetchone()[0],
                "retrieval_structure_snapshot": connection.execute(
                    "SELECT value FROM index_metadata WHERE key='retrieval_structure_snapshot'"
                ).fetchone()[0],
                "knowledge_graph_catalog_version": connection.execute(
                    "SELECT value FROM index_metadata "
                    "WHERE key='knowledge_graph_catalog_version'"
                ).fetchone()[0],
                "documents": repository.counts()["documents"],
                "chunks": repository.counts()["chunks"],
            },
            mode_config={
                "top_k": 10,
                "vector": asdict(evaluation_vector),
                "hybrid": asdict(hybrid.config),
                "hierarchical": asdict(hierarchical.config),
                "graph": asdict(graph.config),
                "intelligent": asdict(intelligent.config),
            },
        )
        _print_result(result)
        print(f"model load seconds: {model_load_seconds:.3f}")
        print(f"artifact: {_write_artifact(result, args.output_dir)}")
        if args.phase16_json and args.phase16_markdown:
            _write_phase16_report(result, args.phase16_json, args.phase16_markdown)
        return 0
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
