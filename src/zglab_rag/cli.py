"""Top-level operational CLI for production maintenance."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from zglab_rag.config import get_settings
from zglab_rag.indexing.profile import load_active_embedding_profile
from zglab_rag.indexing.sync import acquire_sources, apply_sync, plan_sync
from zglab_rag.production.backup import backup_database
from zglab_rag.sources.registry import SourceRegistry
from zglab_rag.sources.sync import fast_forward_registered_sources
from zglab_rag.storage.database import Database
from zglab_rag.storage.repositories import IndexRepository


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zglab-rag", description="ZGLab RAG production operations"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    backup = commands.add_parser("backup", help="Create an atomic SQLite knowledge-index backup")
    backup.add_argument("--database", type=Path)
    backup.add_argument("--backup-dir", type=Path)
    backup.add_argument("--retain-count", type=int)

    sync = commands.add_parser("sync", help="Plan, apply or inspect configured knowledge sources")
    sync_commands = sync.add_subparsers(dest="sync_command", required=True)
    for name, help_text in (
        ("plan", "Show configured-source changes without writing the index"),
        ("apply", "Incrementally apply configured-source changes"),
        ("status", "Show indexed and current configured-source status"),
    ):
        child = sync_commands.add_parser(name, help=help_text)
        _sync_paths(child)
        child.add_argument("--source", action="append", dest="source_ids")
        if name == "apply":
            child.add_argument(
                "--no-backup",
                action="store_true",
                help="Do not create a pre-apply backup (not recommended in production)",
            )
            child.add_argument(
                "--skip-git-fetch",
                action="store_true",
                help="Index the current registered Git checkouts without fetching remotes",
            )
    return parser


def _sync_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--database", type=Path)
    parser.add_argument("--sources-config", type=Path)
    parser.add_argument("--models-config", type=Path, default=Path("config/embedding-models.yaml"))
    parser.add_argument("--batch-size", type=int, default=32)


def _source_ids(config_path: Path, requested: list[str] | None) -> list[str]:
    if requested:
        return list(dict.fromkeys(requested))
    return [source.id for source in SourceRegistry.from_yaml(config_path).all()]


def _print_sync_plan(sync_plan) -> None:
    for source in sync_plan.sources:
        stats = source.plan.statistics()
        print(
            f"source={source.source_id} status={source.state} "
            f"current_revision={source.current_revision or '-'} "
            f"indexed_revision={source.indexed_revision or '-'} "
            f"new={stats['new']} changed={stats['changed']} "
            f"removed={stats['deleted']} unchanged={stats['unchanged']}"
        )
    aggregate = sync_plan.aggregate.statistics()
    print("aggregate " + " ".join(f"{key}={value}" for key, value in aggregate.items()))


def _backup_before_apply(
    database_path: Path, backup_dir: Path, retain_count: int
):
    """Back up an existing index; the first index build has nothing to copy."""
    if not database_path.is_file():
        return None
    return backup_database(database_path, backup_dir, retain_count=retain_count)


def _sync(args: argparse.Namespace) -> int:
    settings = get_settings()
    config_path = args.sources_config or settings.sources_config
    database = Database(args.database or settings.database_path)
    profile, model_config = load_active_embedding_profile(args.models_config)
    source_ids = _source_ids(config_path, args.source_ids)
    registry = SourceRegistry.from_yaml(config_path)
    if args.sync_command == "apply" and not args.skip_git_fetch:
        git_results = fast_forward_registered_sources(
            [registry.get_enabled(source_id) for source_id in source_ids],
            project_root=Path.cwd(),
        )
        for git_result in git_results:
            print(
                f"git_source={git_result.source_id} updated={str(git_result.changed).lower()} "
                f"before={git_result.before_revision} after={git_result.after_revision}"
            )
    source_inputs = acquire_sources(
        source_ids,
        sources_config=config_path,
        project_root=Path.cwd(),
        settings=settings,
    )
    sync_plan = plan_sync(database, source_inputs, profile)
    _print_sync_plan(sync_plan)
    if args.sync_command in {"plan", "status"}:
        if args.sync_command == "status" and database.path.is_file():
            connection = database.connect(read_only=True, initialize=False)
            try:
                last_run = IndexRepository(connection).last_run()
                if last_run is not None:
                    print(
                        f"last_sync={last_run['finished_at'] or last_run['started_at']} "
                        f"index_status={last_run['status']} run_id={last_run['run_id']}"
                    )
            finally:
                connection.close()
        return 0

    if not sync_plan.has_changes:
        print("apply: no changes; runtime reload not required")
        return 0
    if not args.no_backup:
        result = _backup_before_apply(
            database.path,
            settings.backup_dir,
            settings.backup_retain_count,
        )
        if result is None:
            print("backup=skipped reason=initial_index")
        else:
            print(f"backup={result.path} pruned={len(result.removed)}")
    result = apply_sync(
        database,
        sync_plan,
        profile,
        model_config,
        batch_size=args.batch_size,
    )
    if result is not None:
        print(
            f"apply: run_id={result.run_id} embedded={result.embedded_chunks} "
            f"elapsed_seconds={result.elapsed_seconds:.3f}"
        )
    return 0


def _backup(args: argparse.Namespace) -> int:
    settings = get_settings()
    result = backup_database(
        args.database or settings.database_path,
        args.backup_dir or settings.backup_dir,
        retain_count=args.retain_count or settings.backup_retain_count,
    )
    print(f"backup={result.path} pruned={len(result.removed)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "backup":
            return _backup(args)
        return _sync(args)
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
