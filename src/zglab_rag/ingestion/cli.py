from __future__ import annotations

import argparse
from pathlib import Path

from pydantic import ValidationError

from zglab_rag.config import get_settings
from zglab_rag.ingestion.chunking import ChunkingConfig, MarkdownHeadingChunker
from zglab_rag.ingestion.errors import IngestionError
from zglab_rag.ingestion.markdown import MarkdownDocumentParser
from zglab_rag.ingestion.pipeline import (
    LocalMarkdownIngestionPipeline,
    MarkdownSourceIngestionPipeline,
)
from zglab_rag.sources.errors import SourceError
from zglab_rag.sources.factory import create_source_adapter
from zglab_rag.sources.local_markdown import LocalMarkdownSourceLoader
from zglab_rag.sources.registry import SourceRegistry


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Parse and chunk registered Markdown sources")
    parser.add_argument("path", nargs="?", type=Path, help="Registered local Markdown document")
    parser.add_argument("--source", help="Enabled source ID from the source registry")
    parser.add_argument("--sources-config", type=Path, help="Source registry YAML path")
    parser.add_argument("--target-size", type=int, help="Preferred oversized-section chunk size")
    parser.add_argument("--max-size", type=int, help="Maximum chunk size")
    parser.add_argument("--overlap", type=int, help="Overlap for oversized sections only")
    return parser


def main(argv: list[str] | None = None) -> int:
    argument_parser = _argument_parser()
    args = argument_parser.parse_args(argv)
    if bool(args.path) == bool(args.source):
        argument_parser.error("provide exactly one of path or --source")
    settings = get_settings()
    project_root = Path.cwd()
    sources_config = args.sources_config or settings.sources_config

    try:
        registry = SourceRegistry.from_yaml(sources_config)
        chunking_config = ChunkingConfig(
            target_size=(
                settings.chunk_target_size if args.target_size is None else args.target_size
            ),
            max_size=settings.chunk_max_size if args.max_size is None else args.max_size,
            overlap=settings.chunk_overlap if args.overlap is None else args.overlap,
        )
        chunker = MarkdownHeadingChunker(chunking_config)
        markdown_parser = MarkdownDocumentParser()
        if args.source:
            source = registry.get_enabled(args.source)
            adapter = create_source_adapter(
                source,
                project_root=project_root,
                source_checkout_root=settings.source_checkout_root,
            )
            source_result = MarkdownSourceIngestionPipeline(
                adapter=adapter,
                parser=markdown_parser,
                chunker=chunker,
            ).ingest(source)
        else:
            source = registry.local_for_path(args.path, project_root=project_root)
            result = LocalMarkdownIngestionPipeline(
                loader=LocalMarkdownSourceLoader(project_root),
                parser=markdown_parser,
                chunker=chunker,
            ).ingest(source)
    except (IngestionError, SourceError, KeyError, ValidationError, ValueError) as exc:
        raise SystemExit(f"ingestion failed: {exc}") from exc

    if args.source:
        print(f"source id: {source_result.source_id}")
        print(f"revision: {source_result.revision or '-'}")
        print(f"document count: {len(source_result.documents)}")
        print(f"chunk count: {len(source_result.chunks)}")
        return 0

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
