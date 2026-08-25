"""Top-level operational CLI for production maintenance."""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta
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
    backup.add_argument(
        "--auth",
        action="store_true",
        help="Back up the Phase 11 auth database instead of the knowledge index",
    )

    auth = commands.add_parser("auth", help="Auth database maintenance")
    auth_commands = auth.add_subparsers(dest="auth_command", required=True)
    auth_init = auth_commands.add_parser("init", help="Explicitly initialize auth.db schema")
    auth_init.add_argument("--database", type=Path)

    user = commands.add_parser("user", help="Admin user management (no public registration)")
    user_commands = user.add_subparsers(dest="user_command", required=True)

    user_create = user_commands.add_parser(
        "create", help="Create a user and print the activation URL"
    )
    user_create.add_argument("username")
    user_create.add_argument("--role", choices=["ADMIN", "USER"], default="USER")

    user_commands.add_parser("list", help="List all users")

    user_show = user_commands.add_parser(
        "show", help="Show one user (no secrets, no token reprint)"
    )
    user_show.add_argument("username")

    user_disable = user_commands.add_parser(
        "disable", help="Disable a user and revoke all sessions"
    )
    user_disable.add_argument("username")

    user_enable = user_commands.add_parser("enable", help="Re-enable a disabled user")
    user_enable.add_argument("username")

    user_reset = user_commands.add_parser(
        "reset-password",
        help="Revoke sessions and print a one-time password reset URL",
    )
    user_reset.add_argument("username")

    user_revoke = user_commands.add_parser("revoke-sessions", help="Revoke all sessions of a user")
    user_revoke.add_argument("username")

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
    if getattr(args, "auth", False):
        result = backup_database(
            args.database or settings.auth_database_path,
            args.backup_dir or settings.backup_dir,
            retain_count=args.retain_count or settings.backup_retain_count,
            prefix="auth",
        )
    else:
        result = backup_database(
            args.database or settings.database_path,
            args.backup_dir or settings.backup_dir,
            retain_count=args.retain_count or settings.backup_retain_count,
        )
    print(f"backup={result.path} pruned={len(result.removed)}")
    return 0


# ---------------------------------------------------------------------------
# Phase 11 auth administration
# ---------------------------------------------------------------------------


def _auth_service_context(args: argparse.Namespace):
    """Open the auth database and construct the identity service.

    Yields (connection, identity_service); the activation/reset URLs are
    built from settings.auth_public_base_url.
    """
    from zglab_rag.auth.audit import AuditLogger
    from zglab_rag.auth.database import AuthDatabase
    from zglab_rag.auth.identity import IdentityConfig, IdentityService

    settings = get_settings()
    database_path = getattr(args, "database", None) or settings.auth_database_path
    database = AuthDatabase(database_path)
    connection = database.connect(initialize=True)
    config = IdentityConfig(
        password_min_length=settings.auth_password_min_length,
        password_max_length=settings.auth_password_max_length,
        activation_token_ttl=timedelta(hours=settings.auth_activation_token_hours),
        reset_token_ttl=timedelta(hours=settings.auth_reset_token_hours),
    )
    service = IdentityService(connection, AuditLogger(connection), config)
    return connection, service, settings


def _activation_url(settings, token: str, *, purpose: str = "activate") -> str:
    """Build the one-time credential URL using FRAGMENT transport.

    The token lives after '#', so it is never sent to the server: it stays
    out of Nginx access logs, application logs, server-side URL handling
    and Referer headers. The SPA reads it from location.hash, strips it
    from history immediately and submits it only in a POST body.
    """
    base = settings.auth_public_base_url.rstrip("/")
    if purpose == "reset":
        return f"{base}/activate#token={token}&purpose=reset"
    return f"{base}/activate#token={token}"


def _auth(args: argparse.Namespace) -> int:
    from zglab_rag.auth.database import AUTH_SCHEMA_VERSION, AuthDatabase

    settings = get_settings()
    database = AuthDatabase(args.database or settings.auth_database_path)
    connection = database.connect(initialize=True)
    try:
        version = AuthDatabase.schema_version(connection)
    finally:
        connection.close()
    print(f"auth_database={database.path} schema_version={version}")
    if version != AUTH_SCHEMA_VERSION:
        return 1
    return 0


def _user(args: argparse.Namespace) -> int:
    from zglab_rag.auth.models import TokenPurpose, UserRole

    connection, service, settings = _auth_service_context(args)
    try:
        if args.user_command == "create":
            provisioned = service.provision_user(
                args.username,
                role=UserRole(args.role),
                created_by="cli",
            )
            print(
                f"user={provisioned.user.username} id={provisioned.user.id} "
                f"role={provisioned.user.role.value} status={provisioned.user.status.value}"
            )
            # The activation URL is a sensitive one-time credential: shown
            # once to the admin, never logged and never re-displayed.
            print(f"activation_url={_activation_url(settings, provisioned.token)}")
            print("note=send this URL to the user over a trusted channel; it is single-use")
            return 0
        if args.user_command == "list":
            for user in service.users.list_users():
                print(
                    f"user={user.username} id={user.id} role={user.role.value} "
                    f"status={user.status.value} created_at={user.created_at.isoformat()} "
                    f"activated_at={user.activated_at.isoformat() if user.activated_at else '-'}"
                )
            return 0
        if args.user_command == "show":
            from zglab_rag.auth.identity import normalize_username

            user = service.users.get_by_username(normalize_username(args.username))
            if user is None:
                raise LookupError(f"Username '{args.username}' does not exist")
            print(
                f"user={user.username} id={user.id} role={user.role.value} "
                f"status={user.status.value} created_by={user.created_by or '-'} "
                f"created_at={user.created_at.isoformat()} "
                f"activated_at={user.activated_at.isoformat() if user.activated_at else '-'} "
                f"password_changed_at="
                f"{user.password_changed_at.isoformat() if user.password_changed_at else '-'}"
            )
            return 0
        if args.user_command == "disable":
            user = service.set_enabled(args.username, enabled=False)
            print(f"user={user.username} status={user.status.value} sessions=revoked")
            return 0
        if args.user_command == "enable":
            user = service.set_enabled(args.username, enabled=True)
            print(f"user={user.username} status={user.status.value}")
            return 0
        if args.user_command == "reset-password":
            provisioned = service.admin_reset_password(args.username)
            is_reset = provisioned.purpose == TokenPurpose.RESET_PASSWORD
            credential_note = " credential=RESET_REQUIRED" if is_reset else ""
            print(
                f"user={provisioned.user.username} purpose={provisioned.purpose.value} "
                f"sessions=revoked{credential_note}"
            )
            reset_purpose = "reset" if is_reset else "activate"
            reset_url = _activation_url(settings, provisioned.token, purpose=reset_purpose)
            print(f"reset_url={reset_url}")
            note = (
                "note=this URL is single-use; the old password stops working immediately"
                if is_reset
                else "note=this URL is single-use; it sets the first password"
            )
            print(note)
            return 0
        if args.user_command == "revoke-sessions":
            revoked = service.revoke_sessions(args.username)
            print(f"user={args.username.lower()} revoked_sessions={revoked}")
            return 0
        raise ValueError(f"unknown user command: {args.user_command}")
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "backup":
            return _backup(args)
        if args.command == "auth":
            return _auth(args)
        if args.command == "user":
            return _user(args)
        return _sync(args)
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
