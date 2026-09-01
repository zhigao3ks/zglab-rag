from __future__ import annotations

import argparse
from pathlib import Path

from zglab_rag.config import get_settings
from zglab_rag.sources.errors import SourceError
from zglab_rag.sources.factory import create_source_adapter
from zglab_rag.sources.registry import SourceRegistry


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect registered local knowledge sources")
    parser.add_argument("--sources-config", type=Path, help="Source registry YAML path")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="List and inspect all enabled registered sources")
    inspect_parser = subparsers.add_parser("inspect", help="Inspect one registered source")
    inspect_parser.add_argument("source_id")
    inspect_parser.add_argument("--limit", type=int, default=20)
    return parser


def _print_snapshot(source, snapshot, *, include_documents: bool, limit: int = 20) -> None:
    print(source.id)
    print(f"  type: {snapshot.kind.value}")
    print(f"  path: {snapshot.configured_path}")
    print(f"  revision: {snapshot.revision or '-'}")
    print(f"  visibility: {source.visibility.value}")
    print(f"  documents: {len(snapshot.document_paths)}")
    if snapshot.remote_url:
        print(f"  remote: {snapshot.remote_url}")
    if include_documents:
        print("  matched documents:")
        for path in snapshot.document_paths[:limit]:
            print(f"    - {path}")
        remaining = len(snapshot.document_paths) - limit
        if remaining > 0:
            print(f"    ... {remaining} more")


def main(argv: list[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    settings = get_settings()
    project_root = Path.cwd()
    config_path = args.sources_config or settings.sources_config

    try:
        registry = SourceRegistry.from_yaml(config_path)
        if args.command == "list":
            for source in registry.all():
                adapter = create_source_adapter(
                    source,
                    project_root=project_root,
                    source_checkout_root=settings.source_checkout_root,
                )
                _print_snapshot(source, adapter.inspect(source), include_documents=False)
        else:
            source = registry.get_enabled(args.source_id)
            adapter = create_source_adapter(
                source,
                project_root=project_root,
                source_checkout_root=settings.source_checkout_root,
            )
            _print_snapshot(
                source,
                adapter.inspect(source),
                include_documents=True,
                limit=max(0, args.limit),
            )
    except SourceError as exc:
        raise SystemExit(f"source inspection failed: {exc}") from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
