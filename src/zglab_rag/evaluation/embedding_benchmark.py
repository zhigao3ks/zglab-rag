from __future__ import annotations

import argparse
import hashlib
import resource
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import psutil

from zglab_rag.config import get_settings
from zglab_rag.embeddings.config import EmbeddingModelConfig, EmbeddingModelRegistry
from zglab_rag.embeddings.sentence_transformer import (
    SentenceTransformerEmbeddingProvider,
    ensure_device_available,
)
from zglab_rag.evaluation.benchmark import (
    BenchmarkFailure,
    EmbeddingBenchmarkResult,
    run_embedding_benchmark,
    write_benchmark_artifact,
)
from zglab_rag.evaluation.composition import TextComposition
from zglab_rag.evaluation.dataset import load_evaluation_dataset
from zglab_rag.ingestion.chunking import ChunkingConfig, MarkdownHeadingChunker
from zglab_rag.ingestion.markdown import MarkdownDocumentParser
from zglab_rag.ingestion.pipeline import MarkdownSourceIngestionPipeline
from zglab_rag.sources.factory import create_source_adapter
from zglab_rag.sources.registry import SourceRegistry


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark local embedding retrieval")
    parser.add_argument("--source", action="append", required=True, dest="source_ids")
    parser.add_argument("--model")
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--composition", choices=tuple(TextComposition))
    parser.add_argument("--all", action="store_true", help="Run all enabled models/compositions")
    parser.add_argument("--models-config", type=Path, default=Path("config/embedding-models.yaml"))
    parser.add_argument("--sources-config", type=Path)
    parser.add_argument("--dataset", type=Path, default=Path("evaluation/retrieval.yaml"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/benchmarks"))
    parser.add_argument("--batch-size", type=int, default=32)
    return parser


def _validate_arguments(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.all:
        if args.model or args.composition:
            parser.error("--all cannot be combined with --model or --composition")
    elif not args.model or not args.composition:
        parser.error("--model and --composition are required unless --all is used")


def _load_chunks(source_ids: list[str], sources_config: Path):
    settings = get_settings()
    chunk_config = ChunkingConfig(
        target_size=settings.chunk_target_size,
        max_size=settings.chunk_max_size,
        overlap=settings.chunk_overlap,
    )
    registry = SourceRegistry.from_yaml(sources_config)
    chunks = []
    source_revisions: dict[str, str | None] = {}
    for source_id in dict.fromkeys(source_ids):
        source = registry.get_enabled(source_id)
        pipeline = MarkdownSourceIngestionPipeline(
            create_source_adapter(source, project_root=Path.cwd()),
            MarkdownDocumentParser(),
            MarkdownHeadingChunker(chunk_config),
        )
        result = pipeline.ingest(source)
        chunks.extend(result.chunks)
        if result.revision:
            source_revisions[source_id] = result.revision
        else:
            content_identity = "\x1f".join(
                f"{document.document_id}:{document.content_hash}" for document in result.documents
            )
            source_revisions[source_id] = (
                f"content-sha256:{hashlib.sha256(content_identity.encode()).hexdigest()}"
            )
    return chunks, source_revisions, chunk_config


def _peak_rss_mb() -> float:
    # Linux reports ru_maxrss in KiB.
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def _peak_cuda_mb(device: str) -> float | None:
    if device != "cuda":
        return None
    import torch

    return torch.cuda.max_memory_allocated() / (1024 * 1024)


def _run_one(
    *,
    model_config: EmbeddingModelConfig,
    device: str,
    composition: TextComposition,
    chunks,
    dataset,
    revisions,
    chunk_config: ChunkingConfig,
    batch_size: int,
) -> EmbeddingBenchmarkResult:
    if device == "cuda":
        import torch

        ensure_device_available(device)
        torch.cuda.reset_peak_memory_stats()
    process = psutil.Process()
    rss_before_mb = process.memory_info().rss / (1024 * 1024)
    load_start = perf_counter()
    provider = SentenceTransformerEmbeddingProvider(
        model_config,
        device=device,
        batch_size=batch_size,
    )
    model_load_seconds = perf_counter() - load_start
    result = run_embedding_benchmark(
        chunks=chunks,
        dataset=dataset,
        provider=provider,
        model_config=model_config,
        composition=composition,
        source_revisions=revisions,
        chunking_config={
            "target_size": chunk_config.target_size,
            "max_size": chunk_config.max_size,
            "overlap": chunk_config.overlap,
        },
        model_load_seconds=model_load_seconds,
        peak_rss_mb=None,
        peak_cuda_allocated_mb=None,
    )
    performance = result.performance.model_copy(
        update={
            "peak_rss_mb": max(_peak_rss_mb(), rss_before_mb),
            "peak_cuda_allocated_mb": _peak_cuda_mb(device),
        }
    )
    return result.model_copy(update={"performance": performance})


def _print_result(result: EmbeddingBenchmarkResult) -> None:
    quality = result.quality
    performance = result.performance
    print(
        f"{result.metadata.model_id} / {result.metadata.composition.value} / "
        f"{result.metadata.device}"
    )
    print(
        f"  chunks={result.metadata.chunk_count} queries={quality.evaluated_queries} "
        f"skipped={quality.skipped_queries} dimension={performance.embedding_dimension}"
    )
    print(
        f"  Recall@1={quality.recall_at_1:.4f} Recall@3={quality.recall_at_3:.4f} "
        f"Recall@5={quality.recall_at_5:.4f} Recall@10={quality.recall_at_10:.4f} "
        f"Recall@20={quality.recall_at_20:.4f} Recall@30={quality.recall_at_30:.4f} "
        f"MRR={quality.mrr:.4f}"
    )
    print("  category breakdown:")
    for category, metrics in quality.category_breakdown.items():
        print(
            f"    {category}: queries={metrics.query_count} "
            f"R@1={metrics.recall_at_1:.4f} R@5={metrics.recall_at_5:.4f} "
            f"R@10={metrics.recall_at_10:.4f} R@20={metrics.recall_at_20:.4f} "
            f"R@30={metrics.recall_at_30:.4f} MRR={metrics.mrr:.4f}"
        )
    print(
        f"  load={performance.model_load_seconds:.3f}s "
        f"documents={performance.document_embedding_seconds:.3f}s "
        f"query_mean={performance.query_latency_mean_ms:.3f}ms "
        f"query_p95={performance.query_latency_p95_ms:.3f}ms"
    )
    print(
        f"  peak_rss={performance.peak_rss_mb:.1f}MiB "
        f"peak_cuda={performance.peak_cuda_allocated_mb or 0.0:.1f}MiB"
    )


def main(argv: list[str] | None = None) -> int:
    parser = _argument_parser()
    args = parser.parse_args(argv)
    _validate_arguments(parser, args)
    settings = get_settings()
    sources_config = args.sources_config or settings.sources_config
    model_registry = EmbeddingModelRegistry.from_yaml(args.models_config)
    dataset = load_evaluation_dataset(args.dataset)
    chunks, revisions, chunk_config = _load_chunks(args.source_ids, sources_config)

    if args.all:
        run_matrix = [
            (model, composition)
            for model in model_registry.all()
            for composition in TextComposition
        ]
    else:
        run_matrix = [(model_registry.get_enabled(args.model), TextComposition(args.composition))]

    results = []
    failures = []
    for model_config, composition in run_matrix:
        try:
            result = _run_one(
                model_config=model_config,
                device=args.device,
                composition=composition,
                chunks=chunks,
                dataset=dataset,
                revisions=revisions,
                chunk_config=chunk_config,
                batch_size=args.batch_size,
            )
        except Exception as exc:
            failure = BenchmarkFailure(
                timestamp=datetime.now(UTC).isoformat(),
                model_id=model_config.id,
                model_name=model_config.model_name,
                device=args.device,
                composition=composition,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            failures.append(failure)
            print(
                f"FAILED {model_config.id} / {composition.value} / {args.device}: "
                f"{failure.error_type}: {failure.error}"
            )
        else:
            results.append(result)
            _print_result(result)

    artifact_path = write_benchmark_artifact(
        results,
        args.output_dir,
        failures=failures,
    )
    print(f"artifact: {artifact_path}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
