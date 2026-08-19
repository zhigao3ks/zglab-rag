from __future__ import annotations

import argparse
import sys
from pathlib import Path

from zglab_rag.application.runtime import (
    build_embedding_components,
    build_generation_retriever,
    build_llm_provider,
)
from zglab_rag.config import get_settings
from zglab_rag.generation.context import ContextBudget
from zglab_rag.generation.contracts import GenerationResult, GenerationStatus
from zglab_rag.generation.errors import GenerationError
from zglab_rag.generation.service import GroundedAnswerService, GroundedGenerationConfig
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
        # Use shared factory from application.runtime
        embedding_components = build_embedding_components(
            args.models_config,
            device=args.device,
            batch_size=args.batch_size,
        )
        retriever = build_generation_retriever(
            args.mode,
            connection=connection,
            settings=settings,
            embedding_components=embedding_components,
            candidate_k=args.candidate_k,
            reranker_models_config=args.reranker_models_config,
            reranker_model=args.reranker_model,
            reranker_model_path=args.reranker_model_path,
            device=args.device,
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
