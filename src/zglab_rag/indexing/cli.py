from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from zglab_rag.config import get_settings
from zglab_rag.embeddings.config import EmbeddingModelConfig, EmbeddingModelRegistry
from zglab_rag.embeddings.sentence_transformer import SentenceTransformerEmbeddingProvider
from zglab_rag.evaluation.composition import TextComposition
from zglab_rag.indexing.indexer import KnowledgeIndexer, plan_sources
from zglab_rag.indexing.models import EmbeddingProfile, IndexPlan, SourceIndexInput
from zglab_rag.indexing.search import search_public_vectors
from zglab_rag.ingestion.chunking import ChunkingConfig, MarkdownHeadingChunker
from zglab_rag.ingestion.markdown import MarkdownDocumentParser
from zglab_rag.ingestion.pipeline import MarkdownSourceIngestionPipeline
from zglab_rag.sources.factory import create_source_adapter
from zglab_rag.sources.registry import SourceRegistry
from zglab_rag.storage.database import Database
from zglab_rag.storage.repositories import IndexRepository
from zglab_rag.storage.schema import VECTOR_DIMENSION

ACTIVE_MODEL_ID = "bge-small-zh-v1.5"
ACTIVE_COMPOSITION = TextComposition.CONTEXTUAL


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the persistent ZGLab knowledge index")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="Show persistent index status")
    _common_paths(status)

    for command in ("plan", "build", "rebuild"):
        child = subparsers.add_parser(command)
        child.add_argument("--source", action="append", required=True, dest="source_ids")
        child.add_argument("--batch-size", type=int, default=32)
        _common_paths(child)

    search = subparsers.add_parser("search", help="Run a sqlite-vec public KNN smoke search")
    search.add_argument("query")
    search.add_argument("--top-k", type=int, default=5)
    search.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    search.add_argument("--batch-size", type=int, default=32)
    _common_paths(search)
    return parser


def _common_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--database", type=Path)
    parser.add_argument("--sources-config", type=Path)
    parser.add_argument(
        "--models-config", type=Path, default=Path("config/embedding-models.yaml")
    )


def _profile(models_config: Path) -> tuple[EmbeddingProfile, EmbeddingModelConfig]:
    model = EmbeddingModelRegistry.from_yaml(models_config).get_enabled(ACTIVE_MODEL_ID)
    profile = EmbeddingProfile.create(
        model,
        dimension=VECTOR_DIMENSION,
        composition=ACTIVE_COMPOSITION,
    )
    return profile, model


def _acquire(source_ids: list[str], sources_config: Path) -> list[SourceIndexInput]:
    settings = get_settings()
    registry = SourceRegistry.from_yaml(sources_config)
    chunker = MarkdownHeadingChunker(
        ChunkingConfig(
            target_size=settings.chunk_target_size,
            max_size=settings.chunk_max_size,
            overlap=settings.chunk_overlap,
        )
    )
    results = []
    for source_id in dict.fromkeys(source_ids):
        source = registry.get_enabled(source_id)
        pipeline = MarkdownSourceIngestionPipeline(
            create_source_adapter(source, project_root=Path.cwd()),
            MarkdownDocumentParser(),
            chunker,
        )
        ingested = pipeline.ingest(source)
        results.append(
            SourceIndexInput(
                source=source,
                revision=ingested.revision,
                documents=ingested.documents,
                chunks=ingested.chunks,
            )
        )
    return results


def _print_plan(plan: IndexPlan) -> None:
    for key, value in plan.statistics().items():
        print(f"{key}: {value}")


def _status(database: Database) -> int:
    print(f"database: {database.path}")
    if not database.path.is_file():
        print("status: not initialized")
        return 0
    connection = database.connect(read_only=True, initialize=False)
    try:
        versions = database.versions(connection)
        repository = IndexRepository(connection)
        print(f"sqlite version: {versions.sqlite}")
        print(f"sqlite-vec version: {versions.sqlite_vec}")
        print(f"schema version: {versions.schema}")
        active_id = repository.active_profile_id()
        if active_id:
            profile = repository.profile(active_id)
            print(f"embedding profile: {active_id}")
            if profile:
                print(
                    "profile config: "
                    f"model={profile['model_id']} dimension={profile['dimension']} "
                    f"composition={profile['composition']} normalize={bool(profile['normalize'])} "
                    f"query_mode={profile['query_mode']}"
                )
        else:
            print("embedding profile: none")
        counts = repository.counts()
        print(" ".join(f"{key}={value}" for key, value in counts.items()))
        for source in repository.source_snapshots():
            print(
                f"source {source['source_id']}: documents={source['document_count']} "
                f"chunks={source['chunk_count']} revision={source['revision']}"
            )
        last_run = repository.last_run()
        print(
            "last index run: none"
            if last_run is None
            else "last index run: "
            f"{last_run['run_id']} status={last_run['status']} "
            f"embedded={last_run['embedded_chunks']} started={last_run['started_at']}"
        )
    finally:
        connection.close()
    return 0


def _plan(
    database: Database,
    sources: Sequence[SourceIndexInput],
    profile: EmbeddingProfile,
) -> int:
    if database.path.is_file():
        connection = database.connect(read_only=True, initialize=False)
        try:
            plan = plan_sources(IndexRepository(connection), sources, profile)
        finally:
            connection.close()
    else:
        plan = plan_sources(None, sources, profile)
    _print_plan(plan)
    return 0


def _build(
    database: Database,
    sources: Sequence[SourceIndexInput],
    profile: EmbeddingProfile,
    model_config: EmbeddingModelConfig,
    *,
    rebuild: bool,
    batch_size: int,
) -> int:
    connection = database.connect()
    try:
        versions = database.versions(connection)
        print(f"sqlite version: {versions.sqlite}")
        print(f"sqlite-vec version: {versions.sqlite_vec}")
        print(f"schema version: {versions.schema}")
        preliminary = plan_sources(IndexRepository(connection), sources, profile, rebuild=rebuild)
        provider = None
        if preliminary.needs_embedding:
            provider = SentenceTransformerEmbeddingProvider(
                model_config,
                device="cpu",
                batch_size=batch_size,
            )
        result = KnowledgeIndexer(
            connection,
            provider,
            profile,
            batch_size=batch_size,
        ).build(sources, rebuild=rebuild)
        print(f"run_id: {result.run_id}")
        print(f"documents: {result.document_count}")
        _print_plan(result.plan)
        print(f"embedded: {result.embedded_chunks}")
        print(f"elapsed_seconds: {result.elapsed_seconds:.3f}")
        print(f"database_bytes: {database.path.stat().st_size}")
    finally:
        connection.close()
    return 0


def _search(
    database: Database,
    profile: EmbeddingProfile,
    model_config: EmbeddingModelConfig,
    args: argparse.Namespace,
) -> int:
    connection = database.connect(read_only=True, initialize=False)
    try:
        provider = SentenceTransformerEmbeddingProvider(
            model_config,
            device=args.device,
            batch_size=args.batch_size,
        )
        results = search_public_vectors(
            connection,
            provider,
            profile,
            args.query,
            top_k=args.top_k,
        )
        for rank, result in enumerate(results, start=1):
            section = " > ".join(result.section_path) or "(root)"
            preview = " ".join(result.content.split())[:120]
            print(
                f"{rank}. distance={result.distance:.6f} chunk_id={result.chunk_id}\n"
                f"   source={result.source_id}:{result.source_path}\n"
                f"   section={section}\n"
                f"   preview={preview}"
            )
    finally:
        connection.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    settings = get_settings()
    database = Database(args.database or settings.database_path)
    try:
        if args.command == "status":
            return _status(database)
        profile, model_config = _profile(args.models_config)
        if args.command == "search":
            return _search(database, profile, model_config, args)
        sources_config = args.sources_config or settings.sources_config
        sources = _acquire(args.source_ids, sources_config)
        if args.command == "plan":
            return _plan(database, sources, profile)
        return _build(
            database,
            sources,
            profile,
            model_config,
            rebuild=args.command == "rebuild",
            batch_size=args.batch_size,
        )
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
