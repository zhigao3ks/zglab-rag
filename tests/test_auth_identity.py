"""Phase 11A tests: auth database, identity lifecycle and credential tokens.

Covers: schema versioning / fail-fast, admin provisioning, username
normalization, activation token hashing / single-use / expiration,
purpose isolation between activation and reset tokens, Argon2id password
hashing, password policy, disable/enable and session revocation hooks.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from zglab_rag.auth.audit import AuditLogger
from zglab_rag.auth.database import AUTH_SCHEMA_VERSION, AuthDatabase
from zglab_rag.auth.errors import (
    AccountUnavailableError,
    AuthDatabaseError,
    DuplicateUsernameError,
    InvalidCredentialsError,
    PasswordPolicyError,
    TokenExpiredError,
    TokenInvalidError,
    UsernamePolicyError,
    UserNotFoundError,
)
from zglab_rag.auth.identity import IdentityConfig, IdentityService, normalize_username
from zglab_rag.auth.models import CredentialStatus, TokenPurpose, UserRole, UserStatus
from zglab_rag.auth.repositories import SessionRepository, UserRepository, utc_now
from zglab_rag.auth.session import SessionService
from zglab_rag.auth.tokens import generate_credential_token, token_digest

LONG_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def auth_connection(tmp_path: Path):
    database = AuthDatabase(tmp_path / "auth.db")
    connection = database.connect(initialize=True)
    yield connection
    connection.close()


@pytest.fixture
def identity(auth_connection):
    config = IdentityConfig(
        activation_token_ttl=timedelta(hours=24),
        reset_token_ttl=timedelta(hours=24),
    )
    return IdentityService(auth_connection, AuditLogger(auth_connection), config)


# ---------------------------------------------------------------------------
# Auth database schema
# ---------------------------------------------------------------------------


def test_schema_initialized_with_explicit_version(tmp_path: Path) -> None:
    database = AuthDatabase(tmp_path / "auth.db")
    connection = database.connect(initialize=True)
    try:
        assert AuthDatabase.schema_version(connection) == AUTH_SCHEMA_VERSION
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"users", "sessions", "credential_tokens", "usage", "audit_events"} <= tables
    finally:
        connection.close()


def test_schema_initialization_is_idempotent(tmp_path: Path) -> None:
    database = AuthDatabase(tmp_path / "auth.db")
    first = database.connect(initialize=True)
    first.close()
    second = database.connect(initialize=True)
    try:
        assert AuthDatabase.schema_version(second) == AUTH_SCHEMA_VERSION
    finally:
        second.close()


def test_foreign_database_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "foreign.db"
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE unrelated(id INTEGER PRIMARY KEY)")
    raw.close()
    with pytest.raises(AuthDatabaseError):
        AuthDatabase(path).connect(initialize=True)


def test_uninitialized_mode_fails_fast_on_empty_file(tmp_path: Path) -> None:
    database = AuthDatabase(tmp_path / "auth.db")
    with pytest.raises(AuthDatabaseError):
        database.connect(initialize=False)


def test_wrong_schema_version_fails_fast(tmp_path: Path) -> None:
    database = AuthDatabase(tmp_path / "auth.db")
    connection = database.connect(initialize=True)
    connection.execute(
        "UPDATE schema_metadata SET value='99' WHERE key='schema_version'"
    )
    connection.close()
    with pytest.raises(AuthDatabaseError):
        database.connect(initialize=True)


# ---------------------------------------------------------------------------
# Admin provisioning
# ---------------------------------------------------------------------------


def test_admin_creates_pending_user(identity: IdentityService) -> None:
    provisioned = identity.provision_user("alice", created_by="cli")
    assert provisioned.user.username == "alice"
    assert provisioned.user.role == UserRole.USER
    assert provisioned.user.status == UserStatus.PENDING
    assert provisioned.user.password_hash is None
    assert provisioned.purpose == TokenPurpose.ACTIVATE_ACCOUNT
    assert len(provisioned.token) >= 43  # >= 256 bits of entropy


def test_admin_can_create_admin_role(identity: IdentityService) -> None:
    provisioned = identity.provision_user("owner", role=UserRole.ADMIN)
    assert provisioned.user.role == UserRole.ADMIN


def test_duplicate_username_rejected(identity: IdentityService) -> None:
    identity.provision_user("alice")
    with pytest.raises(DuplicateUsernameError):
        identity.provision_user("alice")


def test_username_normalization_prevents_case_duplicates(identity: IdentityService) -> None:
    identity.provision_user("Admin")
    # `admin`, `Admin` and `ADMIN` must collapse to one account.
    with pytest.raises(DuplicateUsernameError):
        identity.provision_user("admin")
    with pytest.raises(DuplicateUsernameError):
        identity.provision_user("  ADMIN  ")
    assert normalize_username("Admin") == "admin"


@pytest.mark.parametrize("bad", ["", "a", "has space", "-lead", "_lead", "ümlaut", "x" * 40])
def test_username_policy_rejects_invalid(bad: str) -> None:
    with pytest.raises(UsernamePolicyError):
        normalize_username(bad)


def test_activation_token_hash_only_in_database(
    identity: IdentityService, auth_connection: sqlite3.Connection
) -> None:
    provisioned = identity.provision_user("alice")
    rows = auth_connection.execute("SELECT token_hash FROM credential_tokens").fetchall()
    assert len(rows) == 1
    assert rows[0]["token_hash"] == token_digest(provisioned.token)
    # Plaintext token never appears anywhere in the database file.
    dump = "\n".join(auth_connection.iterdump())
    assert provisioned.token not in dump


# ---------------------------------------------------------------------------
# Activation
# ---------------------------------------------------------------------------


def test_activation_sets_argon2id_password_and_activates(identity: IdentityService) -> None:
    provisioned = identity.provision_user("alice")
    user = identity.activate_account(provisioned.token, LONG_PASSWORD)
    assert user.status == UserStatus.ACTIVE
    assert user.activated_at is not None
    assert user.password_hash is not None
    assert user.password_hash.startswith("$argon2id$")


def test_activation_token_is_single_use(identity: IdentityService) -> None:
    provisioned = identity.provision_user("alice")
    identity.activate_account(provisioned.token, LONG_PASSWORD)
    with pytest.raises(TokenInvalidError):
        identity.activate_account(provisioned.token, LONG_PASSWORD)


def test_activation_token_expiration(identity: IdentityService) -> None:
    provisioned = identity.provision_user("alice")
    # Force the stored expiry into the past.
    identity.connection.execute(
        "UPDATE credential_tokens SET expires_at='2000-01-01T00:00:00+00:00'"
    )
    with pytest.raises(TokenExpiredError):
        identity.activate_account(provisioned.token, LONG_PASSWORD)


def test_new_activation_token_supersedes_previous(identity: IdentityService) -> None:
    first = identity.provision_user("alice")
    second = identity.admin_reset_password("alice")  # PENDING -> re-issued activation
    assert second.purpose == TokenPurpose.ACTIVATE_ACCOUNT
    with pytest.raises(TokenInvalidError):
        identity.activate_account(first.token, LONG_PASSWORD)
    user = identity.activate_account(second.token, LONG_PASSWORD)
    assert user.status == UserStatus.ACTIVE


def test_unknown_token_rejected(identity: IdentityService) -> None:
    with pytest.raises(TokenInvalidError):
        identity.activate_account(generate_credential_token(), LONG_PASSWORD)


def test_activation_requires_valid_password_policy(identity: IdentityService) -> None:
    provisioned = identity.provision_user("alice")
    with pytest.raises(PasswordPolicyError):
        identity.activate_account(provisioned.token, "short")
    with pytest.raises(PasswordPolicyError):
        identity.activate_account(provisioned.token, "x" * 129)


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------


def test_reset_token_cannot_be_used_for_activation(identity: IdentityService) -> None:
    provisioned = identity.provision_user("alice")
    identity.activate_account(provisioned.token, LONG_PASSWORD)
    reset = identity.admin_reset_password("alice")
    assert reset.purpose == TokenPurpose.RESET_PASSWORD
    # Purpose isolation: reset token must not satisfy an activation flow.
    with pytest.raises(TokenInvalidError):
        identity.activate_account(reset.token, "another-long-password-1")


def test_activation_token_cannot_reset_password(identity: IdentityService) -> None:
    provisioned = identity.provision_user("alice")
    with pytest.raises(TokenInvalidError):
        identity.reset_password_with_token(provisioned.token, "another-long-password-1")


def test_reset_password_flow_replaces_password_and_revokes_sessions(
    identity: IdentityService, auth_connection: sqlite3.Connection
) -> None:
    provisioned = identity.provision_user("alice")
    identity.activate_account(provisioned.token, LONG_PASSWORD)

    # Simulate an existing session row for the user.
    sessions = SessionRepository(auth_connection)
    from zglab_rag.auth.models import SessionRecord
    from zglab_rag.auth.tokens import generate_csrf_secret, generate_session_token

    now = utc_now()
    sessions.create(
        SessionRecord(
            id=0,
            user_id=provisioned.user.id,
            session_hash=token_digest(generate_session_token()),
            csrf_secret=generate_csrf_secret(),
            created_at=now,
            last_seen_at=now,
            idle_expires_at=now + timedelta(days=1),
            absolute_expires_at=now + timedelta(days=2),
            revoked_at=None,
            client_hint=None,
        )
    )

    reset = identity.admin_reset_password("alice")
    row = auth_connection.execute("SELECT revoked_at FROM sessions").fetchone()
    assert row["revoked_at"] is not None

    new_password = "fresh-and-long-enough-pass"
    user = identity.reset_password_with_token(reset.token, new_password)
    assert user.status == UserStatus.ACTIVE

    from zglab_rag.auth.passwords import verify_password

    assert verify_password(user.password_hash, new_password)
    assert not verify_password(user.password_hash, LONG_PASSWORD)

    # The reset token is single-use.
    with pytest.raises(TokenInvalidError):
        identity.reset_password_with_token(reset.token, new_password)


def test_reset_password_rejects_disabled_user(identity: IdentityService) -> None:
    provisioned = identity.provision_user("alice")
    identity.activate_account(provisioned.token, LONG_PASSWORD)
    identity.set_enabled("alice", enabled=False)
    reset = identity.admin_reset_password("alice")
    with pytest.raises(AccountUnavailableError):
        identity.reset_password_with_token(reset.token, "another-long-password-1")


# ---------------------------------------------------------------------------
# Admin state transitions
# ---------------------------------------------------------------------------


def test_disable_and_enable_lifecycle(identity: IdentityService) -> None:
    provisioned = identity.provision_user("alice")
    identity.activate_account(provisioned.token, LONG_PASSWORD)

    user = identity.set_enabled("alice", enabled=False)
    assert user.status == UserStatus.DISABLED

    user = identity.set_enabled("alice", enabled=True)
    assert user.status == UserStatus.ACTIVE

    # Enabling a non-disabled account is rejected.
    with pytest.raises(AccountUnavailableError):
        identity.set_enabled("alice", enabled=True)


def test_admin_operations_for_unknown_user(identity: IdentityService) -> None:
    with pytest.raises(UserNotFoundError):
        identity.admin_reset_password("ghost")
    with pytest.raises(UserNotFoundError):
        identity.set_enabled("ghost", enabled=False)
    with pytest.raises(UserNotFoundError):
        identity.revoke_sessions("ghost")


def test_audit_events_recorded_without_secrets(
    identity: IdentityService, auth_connection: sqlite3.Connection
) -> None:
    provisioned = identity.provision_user("alice")
    identity.activate_account(provisioned.token, LONG_PASSWORD)
    events = [row["event"] for row in auth_connection.execute("SELECT event FROM audit_events")]
    assert "account_created" in events
    assert "account_activated" in events
    dump = "\n".join(auth_connection.iterdump())
    assert provisioned.token not in dump
    assert LONG_PASSWORD not in dump


def test_user_repository_lookup_roundtrip(auth_connection: sqlite3.Connection) -> None:
    users = UserRepository(auth_connection)
    created = users.create(username="bob", role=UserRole.USER, created_by="cli")
    assert users.get_by_id(created.id) is not None
    assert users.get_by_username("bob") is not None
    assert users.get_by_username("BOB") is None  # stored normalized


# ---------------------------------------------------------------------------
# Hardening review: immediate credential invalidation on reset
# ---------------------------------------------------------------------------


def _service_pair(auth_connection):
    audit = AuditLogger(auth_connection)
    identity = IdentityService(auth_connection, audit)
    sessions = SessionService(auth_connection, audit)
    return identity, sessions


def test_reset_invalidates_old_password_immediately(
    auth_connection: sqlite3.Connection,
) -> None:
    """After `user reset-password`, the old password must lose login
    ability at once — not only when the reset token is consumed."""
    identity, sessions = _service_pair(auth_connection)
    provisioned = identity.provision_user("alice")
    identity.activate_account(provisioned.token, LONG_PASSWORD)
    sessions.login("alice", LONG_PASSWORD)  # sanity: old password works

    identity.admin_reset_password("alice")

    # Old password is dead immediately, token still unconsumed.
    with pytest.raises(InvalidCredentialsError):
        sessions.login("alice", LONG_PASSWORD)
    user = identity.users.get_by_username("alice")
    assert user is not None
    assert user.credential_status == CredentialStatus.RESET_REQUIRED


def test_reset_token_consumption_restores_login(
    auth_connection: sqlite3.Connection,
) -> None:
    identity, sessions = _service_pair(auth_connection)
    provisioned = identity.provision_user("alice")
    identity.activate_account(provisioned.token, LONG_PASSWORD)
    reset = identity.admin_reset_password("alice")

    new_password = "fresh-and-long-enough-pass"
    identity.reset_password_with_token(reset.token, new_password)

    user = identity.users.get_by_username("alice")
    assert user is not None
    assert user.credential_status == CredentialStatus.VALID
    sessions.login("alice", new_password)  # must not raise
    with pytest.raises(InvalidCredentialsError):
        sessions.login("alice", LONG_PASSWORD)


def test_pending_user_reset_does_not_mark_reset_required(
    auth_connection: sqlite3.Connection,
) -> None:
    """PENDING users get an activation token instead; no credential exists
    yet, so credential_status stays VALID."""
    identity, _sessions = _service_pair(auth_connection)
    identity.provision_user("bob")
    provisioned = identity.admin_reset_password("bob")
    assert provisioned.purpose == TokenPurpose.ACTIVATE_ACCOUNT
    user = identity.users.get_by_username("bob")
    assert user is not None
    assert user.credential_status == CredentialStatus.VALID


def test_schema_v1_migrates_to_v2(tmp_path: Path) -> None:
    """Existing v1 auth databases upgrade through v2 to the latest schema."""
    path = tmp_path / "auth.db"
    database = AuthDatabase(path)
    connection = database.connect(initialize=True)
    # Downgrade the freshly created database to the historical v1 shape.
    connection.execute("BEGIN IMMEDIATE")
    connection.executescript(
        """
        CREATE TABLE users_backup AS SELECT id, username, password_hash, role,
            status, created_at, created_by, activated_at, password_changed_at
            FROM users;
        DROP TABLE users;
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT,
            role TEXT NOT NULL CHECK(role IN ('ADMIN', 'USER')),
            status TEXT NOT NULL CHECK(status IN ('PENDING', 'ACTIVE', 'DISABLED')),
            created_at TEXT NOT NULL,
            created_by TEXT,
            activated_at TEXT,
            password_changed_at TEXT
        );
        INSERT INTO users SELECT * FROM users_backup;
        DROP TABLE users_backup;
        DROP TABLE web_usage;
        UPDATE schema_metadata SET value='1' WHERE key='schema_version';
        """
    )
    connection.commit()
    connection.close()

    upgraded = database.connect(initialize=True, migrate=True)
    try:
        # Chained migration: v1 -> v2 (credential_status) -> v3 (web_usage).
        assert AuthDatabase.schema_version(upgraded) == AUTH_SCHEMA_VERSION
        columns = {
            row["name"]
            for row in upgraded.execute("PRAGMA table_info(users)")
        }
        assert "credential_status" in columns
        tables = {
            row["name"]
            for row in upgraded.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "web_usage" in tables
    finally:
        upgraded.close()


def test_new_auth_database_is_created_0600(tmp_path: Path) -> None:
    """auth.db holds hashes; it must not be group/world readable."""
    path = tmp_path / "auth.db"
    connection = AuthDatabase(path).connect(initialize=True)
    connection.close()
    assert path.stat().st_mode & 0o777 == 0o600


def test_backup_never_more_permissive_than_source(tmp_path: Path) -> None:
    """Backups inherit the source file mode instead of the umask."""
    from zglab_rag.production.backup import backup_database

    source = tmp_path / "auth.db"
    connection = AuthDatabase(source).connect(initialize=True)
    connection.close()
    assert source.stat().st_mode & 0o777 == 0o600
    result = backup_database(source, tmp_path / "backups", prefix="auth")
    assert result.path.stat().st_mode & 0o777 == 0o600
