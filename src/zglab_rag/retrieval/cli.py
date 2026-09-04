from __future__ import annotations

import argparse
import sys
from pathlib import Path

from zglab_rag.config import Settings, get_settings
from zglab_rag.domain.models import Scope
from zglab_rag.embeddings.sentence_transformer import SentenceTransformerEmbeddingProvider
from zglab_rag.indexing.profile import load_active_embedding_profile
from zglab_rag.reranking.config import RerankerModelRegistry
from zglab_rag.reranking.cross_encoder import CrossEncoderRerankerProvider
from zglab_rag.reranking.service import RerankedRetriever, RerankerRetrievalConfig
from zglab_rag.retrieval.config import (
    GraphRetrievalConfig,
    HierarchicalRetrievalConfig,
    HybridRetrievalConfig,
    IntelligentRetrievalConfig,
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


def retrieval_config(settings: Settings) -> VectorRetrievalConfig:
    return VectorRetrievalConfig(
        default_top_k=settings.retrieval_default_top_k,
        max_top_k=settings.retrieval_max_top_k,
        candidate_factor=settings.retrieval_candidate_factor,
        minimum_candidate_k=settings.retrieval_minimum_candidate_k,
        maximum_candidate_k=settings.retrieval_maximum_candidate_k,
    )


def hybrid_config(settings: Settings) -> HybridRetrievalConfig:
    return HybridRetrievalConfig(
        default_top_k=settings.retrieval_default_top_k,
        max_top_k=settings.retrieval_max_top_k,
        vector_candidate_k=settings.hybrid_vector_candidate_k,
        lexical_candidate_k=settings.hybrid_lexical_candidate_k,
        rrf_k=settings.hybrid_rrf_k,
        vector_weight=settings.hybrid_vector_weight,
        lexical_weight=settings.hybrid_lexical_weight,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Search the persistent public index")
    subparsers = parser.add_subparsers(dest="command", required=True)
    search = subparsers.add_parser("search", help="search public knowledge chunks")
    search.add_argument("query")
    search.add_argument(
        "--mode",
        choices=(
            "vector",
            "lexical",
            "hybrid",
            "reranked",
            "hierarchical",
            "graph",
            "intelligent",
        ),
        default="vector",
    )
    search.add_argument("--top-k", type=int)
    search.add_argument("--candidate-k", type=int, choices=(10, 20, 30))
    search.add_argument("--source", action="append", default=[], dest="source_ids")
    search.add_argument("--scope", action="append", choices=tuple(Scope), default=[])
    search.add_argument("--debug", action="store_true")
    search.add_argument("--database", type=Path)
    search.add_argument(
        "--models-config", type=Path, default=Path("config/embedding-models.yaml")
    )
    search.add_argument(
        "--reranker-models-config",
        type=Path,
        default=Path("config/reranker-models.yaml"),
    )
    search.add_argument(
        "--reranker-model",
        default="mmarco-mMiniLMv2-L12-H384-v1",
    )
    search.add_argument("--reranker-model-path", type=Path)
    search.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    search.add_argument("--batch-size", type=int, default=32)
    return parser


def _build_retriever(args, connection, settings: Settings):
    lexical = LexicalRetriever(connection, config=retrieval_config(settings))
    hierarchical = HierarchicalRetriever(
        connection,
        lexical,
        config=HierarchicalRetrievalConfig(
            document_candidates=settings.hierarchical_document_candidates,
            section_candidates=settings.hierarchical_section_candidates,
            chunk_candidates=settings.hierarchical_chunk_candidates,
        ),
    )
    graph = GraphRetriever(
        connection,
        lexical,
        config=GraphRetrievalConfig(
            max_start_nodes=settings.graph_max_start_nodes,
            max_hops=settings.graph_max_hops,
            max_nodes=settings.graph_max_nodes,
            max_edges=settings.graph_max_edges,
            max_candidate_documents=settings.graph_max_candidate_documents,
        ),
    )
    if args.mode == "lexical":
        return lexical
    if args.mode == "hierarchical":
        return hierarchical
    if args.mode == "graph":
        return graph
    profile, model_config = load_active_embedding_profile(args.models_config)
    provider = SentenceTransformerEmbeddingProvider(
        model_config,
        device=args.device,
        batch_size=args.batch_size,
    )
    vector = VectorRetriever(
        connection,
        provider,
        profile,
        model_config=model_config,
        config=retrieval_config(settings),
    )
    if args.mode == "vector":
        return vector
    if args.mode == "hybrid":
        return HybridRetriever(vector, lexical, config=hybrid_config(settings))
    if args.mode == "intelligent":
        return IntelligentRetriever(
            HybridRetriever(vector, lexical, config=hybrid_config(settings)),
            hierarchical,
            graph,
            config=IntelligentRetrievalConfig(
                hybrid_weight=settings.intelligent_hybrid_weight,
                hierarchical_weight=settings.intelligent_hierarchical_weight,
                graph_weight=settings.intelligent_graph_weight,
                rrf_k=settings.intelligent_rrf_k,
            ),
        )
    candidate_k = args.candidate_k or settings.reranker_candidate_k
    reranker_model = RerankerModelRegistry.from_yaml(
        args.reranker_models_config
    ).get_enabled(args.reranker_model)
    reranker_provider = CrossEncoderRerankerProvider(
        reranker_model,
        device=args.device,
        model_path=args.reranker_model_path,
    )
    return RerankedRetriever(
        vector,
        reranker_provider,
        config=RerankerRetrievalConfig(
            default_top_k=min(settings.reranker_default_top_k, candidate_k),
            maximum_top_k=candidate_k,
            candidate_k=candidate_k,
        ),
    )


def _print_debug(response) -> None:
    diagnostics = response.diagnostics
    print("debug:")
    for name, value in diagnostics.model_dump(mode="python").items():
        if name == "filters":
            print(
                "  filters="
                f"visibility={value['visibility'].value},"
                f"sources={list(value['source_ids'])},"
                f"scopes={[scope.value for scope in value['scopes']]}"
            )
        elif isinstance(value, float):
            print(f"  {name}={value:.3f}")
        else:
            print(f"  {name}={value}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = get_settings()
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
        connection = database.connect(read_only=True, initialize=False)
        retriever = _build_retriever(args, connection, settings)
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
            print(f"{result.rank}. mode={result.retriever} score={result.score:.6f}")
            print(f"   source={result.source_id}:{result.source_path}")
            print(f"   section={section}")
            print(f"   preview={preview}")
            if args.debug and result.retriever == "hybrid":
                print(
                    f"   vector_rank={result.vector_rank} "
                    f"lexical_rank={result.lexical_rank} rrf_score={result.rrf_score:.6f}"
                )
            if result.retriever == "reranked":
                print(
                    f"   reranker_score={result.reranker_score:.6f} "
                    f"original_vector_rank={result.original_rank} "
                    f"vector_score={result.vector_score:.6f}"
                )
        if args.debug:
            _print_debug(response)
        return 0
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
