from __future__ import annotations

import argparse
import sys
from pathlib import Path

from zglab_rag.config import Settings, get_settings
from zglab_rag.domain.models import Scope
from zglab_rag.embeddings.sentence_transformer import SentenceTransformerEmbeddingProvider
from zglab_rag.indexing.profile import load_active_embedding_profile
from zglab_rag.retrieval.config import VectorRetrievalConfig
from zglab_rag.retrieval.contracts import RetrievalFilter, RetrievalQuery
from zglab_rag.retrieval.vector import VectorRetriever
from zglab_rag.storage.database import Database


def retrieval_config(settings: Settings) -> VectorRetrievalConfig:
    return VectorRetrievalConfig(
        default_top_k=settings.retrieval_default_top_k,
        max_top_k=settings.retrieval_max_top_k,
        candidate_factor=settings.retrieval_candidate_factor,
        minimum_candidate_k=settings.retrieval_minimum_candidate_k,
        maximum_candidate_k=settings.retrieval_maximum_candidate_k,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Search the persistent public vector index")
    parser.add_argument("query")
    parser.add_argument("--top-k", type=int)
    parser.add_argument("--source", action="append", default=[], dest="source_ids")
    parser.add_argument("--scope", action="append", choices=tuple(Scope), default=[])
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--database", type=Path)
    parser.add_argument(
        "--models-config", type=Path, default=Path("config/embedding-models.yaml")
    )
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
        provider = SentenceTransformerEmbeddingProvider(
            model_config,
            device=args.device,
            batch_size=args.batch_size,
        )
        connection = database.connect(read_only=True, initialize=False)
        retriever = VectorRetriever(
            connection,
            provider,
            profile,
            model_config=model_config,
            config=retrieval_config(settings),
        )
        response = retriever.retrieve(
            RetrievalQuery(
                text=args.query,
                top_k=args.top_k,
                filters=RetrievalFilter(
                    source_ids=tuple(args.source_ids),
                    scopes=tuple(Scope(scope) for scope in args.scope),
                ),
            )
        )
        for result in response.results:
            section = " > ".join(result.section_path) or "(root)"
            preview = " ".join(result.content.split())[:120]
            print(
                f"{result.rank}. score={result.score:.6f} distance={result.distance:.6f}\n"
                f"   source={result.source_id}:{result.source_path}\n"
                f"   section={section}\n"
                f"   preview={preview}"
            )
        if args.debug:
            diagnostics = response.diagnostics
            filters = diagnostics.filters
            print("debug:")
            print(f"  query_embedding_latency_ms={diagnostics.query_embedding_latency_ms:.3f}")
            print(f"  vector_search_latency_ms={diagnostics.vector_search_latency_ms:.3f}")
            print(f"  total_retrieval_latency_ms={diagnostics.total_retrieval_latency_ms:.3f}")
            print(f"  candidate_count={diagnostics.candidate_count}")
            print(f"  filtered_count={diagnostics.filtered_count}")
            print(f"  returned_count={diagnostics.returned_count}")
            print(f"  top_k={diagnostics.top_k}")
            print(
                "  filters="
                f"visibility={filters.visibility.value},"
                f"sources={list(filters.source_ids)},"
                f"scopes={[scope.value for scope in filters.scopes]}"
            )
        return 0
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
