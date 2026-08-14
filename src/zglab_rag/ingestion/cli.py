from __future__ import annotations

import argparse
from pathlib import Path

from pydantic import ValidationError

from zglab_rag.config import get_settings
from zglab_rag.ingestion.chunking import ChunkingConfig, MarkdownHeadingChunker
from zglab_rag.ingestion.errors import IngestionError
from zglab_rag.ingestion.markdown import MarkdownDocumentParser
from zglab_rag.ingestion.pipeline import LocalMarkdownIngestionPipeline
from zglab_rag.sources.local_markdown import LocalMarkdownSourceLoader, LocalSourceError
from zglab_rag.sources.registry import SourceRegistry


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Parse and chunk one registered Markdown source")
    parser.add_argument("path", type=Path, help="Path to a registered local Markdown document")
    parser.add_argument("--sources-config", type=Path, help="Source registry YAML path")
    parser.add_argument("--target-size", type=int, help="Preferred oversized-section chunk size")
    parser.add_argument("--max-size", type=int, help="Maximum chunk size")
    parser.add_argument("--overlap", type=int, help="Overlap for oversized sections only")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    settings = get_settings()
    project_root = Path.cwd()
    sources_config = args.sources_config or settings.sources_config

    try:
        registry = SourceRegistry.from_yaml(sources_config)
        source = registry.local_for_path(args.path, project_root=project_root)
        chunking_config = ChunkingConfig(
            target_size=(
                settings.chunk_target_size if args.target_size is None else args.target_size
            ),
            max_size=settings.chunk_max_size if args.max_size is None else args.max_size,
            overlap=settings.chunk_overlap if args.overlap is None else args.overlap,
        )
        pipeline = LocalMarkdownIngestionPipeline(
            loader=LocalMarkdownSourceLoader(project_root),
            parser=MarkdownDocumentParser(),
            chunker=MarkdownHeadingChunker(chunking_config),
        )
        result = pipeline.ingest(source)
    except (IngestionError, LocalSourceError, KeyError, ValidationError, ValueError) as exc:
        raise SystemExit(f"ingestion failed: {exc}") from exc

    print(f"document id: {result.document.document_id}")
    print(f"title: {result.document.title}")
    print(f"chunk count: {len(result.chunks)}")
    for chunk in result.chunks:
        section_path = " > ".join(chunk.section_path) or "(document root)"
        preview = " ".join(chunk.content.split())[:80]
        print(f"[{chunk.chunk_index}] {section_path} | {chunk.char_count} chars | {preview}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
