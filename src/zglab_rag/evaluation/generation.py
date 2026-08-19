from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from pydantic import BaseModel

from zglab_rag.config import get_settings
from zglab_rag.evaluation.dataset import RelevantTarget
from zglab_rag.evaluation.generation_dataset import (
    GenerationQuery,
    GenerationQueryCategory,
    load_generation_dataset,
)
from zglab_rag.generation.cli import build_generation_retriever, build_llm_provider
from zglab_rag.generation.context import ContextBudget
from zglab_rag.generation.contracts import GenerationResult, GenerationStatus
from zglab_rag.generation.service import GroundedAnswerService, GroundedGenerationConfig
from zglab_rag.retrieval.contracts import RetrievalQuery, RetrievalResult
from zglab_rag.storage.database import Database


def _matches_target(result: RetrievalResult, target: RelevantTarget) -> bool:
    section_matches = not target.section_path or (
        result.section_path[: len(target.section_path)] == target.section_path
    )
    return (
        result.source_id == target.source_id
        and result.source_path == target.source_path
        and section_matches
    )


class RetrievedEvidenceRecord(BaseModel):
    chunk_id: str
    source_id: str
    source_path: str
    score: float


class ClaimRecord(BaseModel):
    text: str
    citations: list[str]


class CitedSourceRecord(BaseModel):
    evidence_id: str
    source_id: str
    source_path: str
    section_path: list[str]


class HardNegativeGenerationRecord(BaseModel):
    query_id: str
    retrieved: list[RetrievedEvidenceRecord]
    generation_decision: str
    citations: list[str]
    answer_preview: str


class GenerationQueryRecord(BaseModel):
    query_id: str
    category: str
    should_answer_expected: bool
    retrieval_evidence_hit: bool | None
    retrieved_count: int
    generation_status: str | None
    answered_insufficient: bool | None
    citation_coverage: float | None
    should_answer_correct: bool | None
    repair_attempts: int | None
    failure_reason: str | None
    claims: list[ClaimRecord] = []
    cited_sources: list[CitedSourceRecord] = []
    answer_preview: str | None = None
    retrieval_latency_ms: float
    generation_latency_ms: float | None
    total_latency_ms: float | None


class GenerationMetrics(BaseModel):
    queries_total: int
    queries_with_expected: int
    evidence_hit_rate: float | None
    answered_count: int
    citation_validity_rate: float | None
    citation_coverage_mean: float | None
    should_answer_accuracy: float | None
    insufficient_correctness_for_refusal_queries: float | None


class GenerationEvaluation(BaseModel):
    schema_version: int = 1
    timestamp: str
    dataset_version: int
    dataset_sha256: str
    retrieval_mode: str
    generation_provider_configured: bool
    provider: str | None
    model: str | None
    metrics: GenerationMetrics
    category_breakdown: dict[str, dict[str, float | int | None]]
    records: list[GenerationQueryRecord]
    hard_negatives: list[HardNegativeGenerationRecord]


def _evaluate_query(
    query: GenerationQuery,
    *,
    retriever,
    service: GroundedAnswerService | None,
    top_k: int,
) -> tuple[GenerationQueryRecord, HardNegativeGenerationRecord | None]:
    retrieval_started = perf_counter()
    response = retriever.retrieve(RetrievalQuery(text=query.query, top_k=top_k))
    retrieval_ms = (perf_counter() - retrieval_started) * 1000

    evidence_hit = None
    if query.expected_evidence:
        evidence_hit = any(
            _matches_target(result, target)
            for result in response.results
            for target in query.expected_evidence
        )

    result: GenerationResult | None = None
    if service is not None:
        result = service.answer(query.query)

    coverage = None
    answered_insufficient = None
    should_answer_correct = None
    if result is not None:
        answered_insufficient = result.status == GenerationStatus.INSUFFICIENT_EVIDENCE
        if result.status == GenerationStatus.ANSWERED and query.expected_evidence:
            matched = sum(
                1
                for target in query.expected_evidence
                if any(_matches_source(source, target) for source in result.answer.sources)
            )
            coverage = matched / len(query.expected_evidence)
        expected_answer = query.should_answer
        actually_answered = result.status == GenerationStatus.ANSWERED
        should_answer_correct = expected_answer == actually_answered

    hard_negative = None
    if query.category == GenerationQueryCategory.HARD_NEGATIVE:
        hard_negative = HardNegativeGenerationRecord(
            query_id=query.id,
            retrieved=[
                RetrievedEvidenceRecord(
                    chunk_id=item.chunk_id,
                    source_id=item.source_id,
                    source_path=item.source_path,
                    score=item.score,
                )
                for item in response.results
            ],
            generation_decision="skipped" if result is None else result.status.value,
            citations=(
                []
                if result is None
                else [source.evidence_id for source in result.answer.sources]
            ),
            answer_preview="" if result is None else result.answer.answer[:120],
        )

    return (
        GenerationQueryRecord(
            query_id=query.id,
            category=query.category.value,
            should_answer_expected=query.should_answer,
            retrieval_evidence_hit=evidence_hit,
            retrieved_count=len(response.results),
            generation_status=None if result is None else result.status.value,
            answered_insufficient=answered_insufficient,
            citation_coverage=coverage,
            should_answer_correct=should_answer_correct,
            repair_attempts=None if result is None else result.diagnostics.repair_attempts,
            failure_reason=None if result is None else result.failure_reason,
            claims=(
                []
                if result is None
                else [
                    ClaimRecord(text=claim.text, citations=list(claim.citations))
                    for claim in result.answer.claims
                ]
            ),
            cited_sources=(
                []
                if result is None
                else [
                    CitedSourceRecord(
                        evidence_id=source.evidence_id,
                        source_id=source.source_id,
                        source_path=source.source_path,
                        section_path=list(source.section_path),
                    )
                    for source in result.answer.sources
                ]
            ),
            answer_preview=None if result is None else result.answer.answer[:200],
            retrieval_latency_ms=retrieval_ms,
            generation_latency_ms=(
                None if result is None else result.diagnostics.generation_latency_ms
            ),
            total_latency_ms=None if result is None else result.diagnostics.total_latency_ms,
        ),
        hard_negative,
    )


def _matches_source(source, target: RelevantTarget) -> bool:
    section_matches = not target.section_path or (
        source.section_path[: len(target.section_path)] == target.section_path
    )
    return (
        source.source_id == target.source_id
        and source.source_path == target.source_path
        and section_matches
    )


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def _metrics(records: list[GenerationQueryRecord]) -> GenerationMetrics:
    with_expected = [record for record in records if record.retrieval_evidence_hit is not None]
    hits = [record for record in with_expected if record.retrieval_evidence_hit]
    answered = [
        record for record in records if record.generation_status == GenerationStatus.ANSWERED.value
    ]
    generated = [record for record in records if record.generation_status is not None]
    refusal = [record for record in records if not record.should_answer_expected]
    judged = [record for record in generated if record.should_answer_correct is not None]
    validation_rejected = [
        record
        for record in records
        if record.generation_status == GenerationStatus.INSUFFICIENT_EVIDENCE.value
        and (record.failure_reason or "").startswith(
            ("CitationValidationFailure", "InvalidStructuredOutput")
        )
    ]
    coverages = [record.citation_coverage for record in answered]
    return GenerationMetrics(
        queries_total=len(records),
        queries_with_expected=len(with_expected),
        evidence_hit_rate=_ratio(len(hits), len(with_expected)),
        answered_count=len(answered),
        citation_validity_rate=_ratio(
            len(answered), len(answered) + len(validation_rejected)
        ),
        citation_coverage_mean=(
            None if not coverages else sum(coverages) / len(coverages)
        ),
        should_answer_accuracy=_ratio(
            sum(1 for record in judged if record.should_answer_correct), len(judged)
        ),
        insufficient_correctness_for_refusal_queries=_ratio(
            sum(
                1
                for record in refusal
                if record.generation_status == GenerationStatus.INSUFFICIENT_EVIDENCE.value
            ),
            sum(1 for record in refusal if record.generation_status is not None),
        ),
    )


def _category_breakdown(
    records: list[GenerationQueryRecord],
) -> dict[str, dict[str, float | int | None]]:
    breakdown = {}
    for category in GenerationQueryCategory:
        subset = [record for record in records if record.category == category.value]
        if not subset:
            continue
        metrics = _metrics(subset)
        breakdown[category.value] = {
            "queries": len(subset),
            "evidence_hit_rate": metrics.evidence_hit_rate,
            "answered": metrics.answered_count,
            "citation_coverage_mean": metrics.citation_coverage_mean,
            "should_answer_accuracy": metrics.should_answer_accuracy,
        }
    return breakdown


def run_generation_evaluation(
    retriever,
    *,
    dataset,
    service: GroundedAnswerService | None,
    provider_configured: bool,
    retrieval_mode: str,
    top_k: int,
) -> GenerationEvaluation:
    records: list[GenerationQueryRecord] = []
    hard_negatives: list[HardNegativeGenerationRecord] = []
    for query in dataset.dataset.queries:
        record, hard_negative = _evaluate_query(
            query, retriever=retriever, service=service, top_k=top_k
        )
        records.append(record)
        if hard_negative is not None:
            hard_negatives.append(hard_negative)
    return GenerationEvaluation(
        timestamp=datetime.now(UTC).isoformat(),
        dataset_version=dataset.dataset.version,
        dataset_sha256=dataset.sha256,
        retrieval_mode=retrieval_mode,
        generation_provider_configured=provider_configured,
        provider=None if service is None else service.provider.name,
        model=None if service is None else service.provider.model,
        metrics=_metrics(records),
        category_breakdown=_category_breakdown(records),
        records=records,
        hard_negatives=hard_negatives,
    )


def _write_artifact(result: GenerationEvaluation, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    path = output_dir / f"generation-eval-{stamp}.json"
    path.write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _optional(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4f}"


def _print_result(result: GenerationEvaluation) -> None:
    metrics = result.metrics
    print(f"retrieval_mode={result.retrieval_mode}")
    print(f"generation_provider_configured={result.generation_provider_configured}")
    if not result.generation_provider_configured:
        print("generation metrics skipped: no LLM provider configured")
    print(f"queries_total={metrics.queries_total}")
    print(f"evidence_hit_rate={_optional(metrics.evidence_hit_rate)}")
    print(f"answered_count={metrics.answered_count}")
    print(f"citation_validity_rate={_optional(metrics.citation_validity_rate)}")
    print(f"citation_coverage_mean={_optional(metrics.citation_coverage_mean)}")
    print(f"should_answer_accuracy={_optional(metrics.should_answer_accuracy)}")
    print(
        "insufficient_correctness_for_refusal_queries="
        f"{_optional(metrics.insufficient_correctness_for_refusal_queries)}"
    )
    for category, values in result.category_breakdown.items():
        print(f"  {category}: {values}")
    for item in result.hard_negatives:
        print(
            f"hard-negative {item.query_id}: decision={item.generation_decision} "
            f"citations={item.citations} retrieved={len(item.retrieved)}"
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate grounded generation with deterministic metrics"
    )
    parser.add_argument("--dataset", type=Path, default=Path("evaluation/generation.yaml"))
    parser.add_argument("--database", type=Path)
    parser.add_argument("--mode", choices=("vector", "reranked"), default="vector")
    parser.add_argument("--candidate-k", type=int, choices=(10, 20, 30))
    parser.add_argument(
        "--models-config", type=Path, default=Path("config/embedding-models.yaml")
    )
    parser.add_argument(
        "--reranker-models-config",
        type=Path,
        default=Path("config/reranker-models.yaml"),
    )
    parser.add_argument("--reranker-model", default="mmarco-mMiniLMv2-L12-H384-v1")
    parser.add_argument("--reranker-model-path", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/evaluation"))
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument(
        "--retrieval-only",
        action="store_true",
        help="evaluate retrieval evidence hits without calling the LLM provider",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = get_settings()
    provider_configured = settings.llm_provider_configured and not args.retrieval_only
    database = Database(args.database or settings.database_path)
    if not database.path.is_file():
        print(
            f"error: persistent index does not exist at {database.path}; "
            "run the indexing build first",
            file=sys.stderr,
        )
        return 1
    connection = None
    try:
        dataset = load_generation_dataset(args.dataset)
        connection = database.connect(read_only=True, initialize=False)
        retriever = build_generation_retriever(
            args.mode,
            connection=connection,
            settings=settings,
            models_config=args.models_config,
            device=args.device,
            batch_size=args.batch_size,
            candidate_k=args.candidate_k,
            reranker_models_config=args.reranker_models_config,
            reranker_model=args.reranker_model,
            reranker_model_path=args.reranker_model_path,
        )
        service = None
        if provider_configured:
            service = GroundedAnswerService(
                retriever,
                build_llm_provider(settings),
                config=GroundedGenerationConfig(
                    retrieval_mode=args.mode,
                    retrieval_top_k=settings.generation_retrieval_top_k,
                    budget=ContextBudget(
                        max_evidence_items=settings.generation_max_evidence_items,
                        max_context_chars=settings.generation_max_context_chars,
                    ),
                ),
            )
        else:
            print(
                "Generation provider not configured; reporting retrieval evidence hits only.",
                file=sys.stderr,
            )
        result = run_generation_evaluation(
            retriever,
            dataset=dataset,
            service=service,
            provider_configured=provider_configured,
            retrieval_mode=args.mode,
            top_k=settings.generation_retrieval_top_k,
        )
        _print_result(result)
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
