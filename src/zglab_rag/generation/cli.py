from __future__ import annotations

import argparse
import sys
from pathlib import Path

from zglab_rag.config import Settings, get_settings
from zglab_rag.embeddings.sentence_transformer import SentenceTransformerEmbeddingProvider
from zglab_rag.generation.context import ContextBudget
from zglab_rag.generation.contracts import GenerationResult, GenerationStatus
from zglab_rag.generation.errors import GenerationError
from zglab_rag.generation.openai_provider import (
    OpenAICompatibleConfig,
    OpenAICompatibleProvider,
)
from zglab_rag.generation.service import GroundedAnswerService, GroundedGenerationConfig
from zglab_rag.indexing.profile import load_active_embedding_profile
from zglab_rag.reranking.config import RerankerModelRegistry
from zglab_rag.reranking.cross_encoder import CrossEncoderRerankerProvider
from zglab_rag.reranking.service import RerankedRetriever, RerankerRetrievalConfig
from zglab_rag.retrieval.cli import retrieval_config
from zglab_rag.retrieval.vector import VectorRetriever
from zglab_rag.storage.database import Database


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ask the grounded public knowledge assistant")
    subparsers = parser.add_subparsers(dest="command", required=True)
    ask = subparsers.add_parser("ask", help="answer a question from public evidence")
    ask.add_argument("query")
    ask.add_argument("--mode", choices=("vector", "reranked"), default="vector")
    ask.add_argument("--candidate-k", type=int, choices=(10, 20, 30))
    ask.add_argument("--debug", action="store_true")
    ask.add_argument("--database", type=Path)
    ask.add_argument(
        "--models-config", type=Path, default=Path("config/embedding-models.yaml")
    )
    ask.add_argument(
        "--reranker-models-config",
        type=Path,
        default=Path("config/reranker-models.yaml"),
    )
    ask.add_argument("--reranker-model", default="mmarco-mMiniLMv2-L12-H384-v1")
    ask.add_argument("--reranker-model-path", type=Path)
    ask.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    ask.add_argument("--batch-size", type=int, default=32)
    return parser


def build_generation_retriever(
    mode: str,
    *,
    connection,
    settings: Settings,
    models_config: Path,
    device: str = "cpu",
    batch_size: int = 32,
    candidate_k: int | None = None,
    reranker_models_config: Path = Path("config/reranker-models.yaml"),
    reranker_model: str = "mmarco-mMiniLMv2-L12-H384-v1",
    reranker_model_path: Path | None = None,
):
    profile, model_config = load_active_embedding_profile(models_config)
    provider = SentenceTransformerEmbeddingProvider(
        model_config,
        device=device,
        batch_size=batch_size,
    )
    vector = VectorRetriever(
        connection,
        provider,
        profile,
        model_config=model_config,
        config=retrieval_config(settings),
    )
    if mode == "vector":
        return vector
    candidate_k = candidate_k or settings.reranker_candidate_k
    reranker_model_config = RerankerModelRegistry.from_yaml(reranker_models_config).get_enabled(
        reranker_model
    )
    reranker_provider = CrossEncoderRerankerProvider(
        reranker_model_config,
        device=device,
        model_path=reranker_model_path,
    )
    return RerankedRetriever(
        vector,
        reranker_provider,
        config=RerankerRetrievalConfig(
            default_top_k=min(settings.generation_retrieval_top_k, candidate_k),
            maximum_top_k=candidate_k,
            candidate_k=candidate_k,
        ),
    )


def build_llm_provider(settings: Settings) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        OpenAICompatibleConfig(
            base_url=settings.llm_base_url or "",
            api_key=settings.llm_api_key or "",
            model=settings.llm_model or "",
            timeout_seconds=settings.llm_timeout_seconds,
        )
    )


def _print_result(result: GenerationResult, *, debug: bool) -> None:
    if result.status == GenerationStatus.FAILED:
        print(f"error: {result.failure_reason}", file=sys.stderr)
        return
    print("Answer:")
    print(result.answer.answer)
    print()
    if result.answer.sources:
        print("Sources:")
        for source in result.answer.sources:
            section = " > ".join(source.section_path) or "(root)"
            print(f"[{source.evidence_id}] {source.title}")
            print(f"    source={source.source_id}:{source.source_path}")
            print(f"    section={section}")
        print()
    if result.status == GenerationStatus.INSUFFICIENT_EVIDENCE and result.failure_reason:
        print(f"insufficient evidence reason: {result.failure_reason}")
    diagnostics = result.diagnostics
    print("Diagnostics:")
    print(f"  status={result.status.value}")
    print(f"  retrieval_mode={diagnostics.retrieval_mode}")
    print(f"  retrieval_top_k={diagnostics.retrieval_top_k}")
    print(f"  evidence_count={diagnostics.evidence_count}")
    print(f"  retrieval_latency_ms={diagnostics.retrieval_latency_ms:.3f}")
    print(f"  provider={diagnostics.provider}")
    print(f"  model={diagnostics.model}")
    print(f"  generation_latency_ms={diagnostics.generation_latency_ms:.3f}")
    print(f"  total_latency_ms={diagnostics.total_latency_ms:.3f}")
    print(f"  repair_attempts={diagnostics.repair_attempts}")
    if diagnostics.input_tokens is not None:
        print(f"  input_tokens={diagnostics.input_tokens}")
    if diagnostics.output_tokens is not None:
        print(f"  output_tokens={diagnostics.output_tokens}")
    if debug:
        for source in result.answer.sources:
            score = "n/a" if source.score is None else f"{source.score:.6f}"
            print(f"  debug [{source.evidence_id}] chunk_id={source.chunk_id} score={score}")
        if result.raw_answer is not None:
            print(f"  debug raw_answer={result.raw_answer[:200]}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = get_settings()
    if not settings.llm_provider_configured:
        print(
            "Generation provider not configured: set ZGLAB_RAG_LLM_BASE_URL, "
            "ZGLAB_RAG_LLM_API_KEY and ZGLAB_RAG_LLM_MODEL in .env",
            file=sys.stderr,
        )
        return 1
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
        result = service.answer(args.query)
        _print_result(result, debug=args.debug)
        return 0 if result.status != GenerationStatus.FAILED else 1
    except GenerationError as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
