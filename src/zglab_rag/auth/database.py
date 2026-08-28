"""Independent auth database (Phase 11).

Identity and security state lives in a dedicated ``auth.db`` and never in
``knowledge.db``: the two databases have completely different lifecycles
(knowledge index rebuilds vs. identity continuity). The schema carries an
explicit version, initialization is explicit, and any mismatch fails fast
instead of silently falling back.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from zglab_rag.auth.errors import AuthDatabaseError

AUTH_SCHEMA_VERSION = 4

AUTH_SCHEMA = """
CREATE TABLE schema_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT,
    role TEXT NOT NULL CHECK(role IN ('ADMIN', 'USER')),
    status TEXT NOT NULL CHECK(status IN ('PENDING', 'ACTIVE', 'DISABLED')),
    credential_status TEXT NOT NULL DEFAULT 'VALID'
        CHECK(credential_status IN ('VALID', 'RESET_REQUIRED')),
    created_at TEXT NOT NULL,
    created_by TEXT,
    activated_at TEXT,
    password_changed_at TEXT
);

CREATE TABLE sessions (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    session_hash TEXT UNIQUE NOT NULL,
    csrf_secret TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    idle_expires_at TEXT NOT NULL,
    absolute_expires_at TEXT NOT NULL,
    revoked_at TEXT,
    client_hint TEXT,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX sessions_user_id_idx ON sessions(user_id);

CREATE TABLE credential_tokens (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    purpose TEXT NOT NULL CHECK(purpose IN ('ACTIVATE_ACCOUNT', 'RESET_PASSWORD')),
    token_hash TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    revoked_at TEXT,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX credential_tokens_user_purpose_idx
    ON credential_tokens(user_id, purpose);

CREATE TABLE usage (
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    minute TEXT NOT NULL,
    requests INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(user_id, day, minute),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE web_usage (
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    minute TEXT NOT NULL,
    requests INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(user_id, day, minute),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE agent_usage (
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    minute TEXT NOT NULL,
    requests INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(user_id, day, minute),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE audit_events (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    event TEXT NOT NULL,
    user_id INTEGER,
    request_id TEXT,
    result TEXT NOT NULL,
    client_hint TEXT
);
CREATE INDEX audit_events_created_at_idx ON audit_events(created_at);
"""


class AuthDatabase:
    """Handle to the standalone auth database.

    Connections are short-lived (opened per operation) and use WAL, which
    suits the single-instance write pattern: one API process plus occasional
    admin CLI usage. sqlite-vec is intentionally not loaded here.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def connect(self, *, initialize: bool = True, migrate: bool = True) -> sqlite3.Connection:
        """Open a connection with schema validation or explicit init.

        Raises AuthDatabaseError on any schema mismatch; callers must not
        silently fall back to a degraded auth mode. A freshly created
        database file is hardened to 0600: it holds password/session/token
        hashes and must never be group/world readable.
        """
        existed_before = self.path.is_file()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, isolation_level=None, timeout=10.0)
        except sqlite3.Error as exc:
            raise AuthDatabaseError(f"Unable to open auth database: {exc}") from exc
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        try:
            self._validate_or_initialize(connection, initialize=initialize, migrate=migrate)
        except Exception:
            connection.close()
            raise
        if not existed_before:
            os.chmod(self.path, 0o600)
        return connection

    @staticmethod
    def _validate_or_initialize(
        connection: sqlite3.Connection, *, initialize: bool, migrate: bool = True
    ) -> None:
        has_metadata = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_metadata'"
        ).fetchone()
        if not has_metadata:
            has_any_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') LIMIT 1"
            ).fetchone()
            if has_any_table or not initialize:
                raise AuthDatabaseError("Database is not an initialized ZGLab auth database")
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.executescript(AUTH_SCHEMA)
                connection.execute(
                    "INSERT INTO schema_metadata(key, value) VALUES ('schema_version', ?)",
                    (str(AUTH_SCHEMA_VERSION),),
                )
                connection.commit()
            except sqlite3.Error as exc:
                connection.rollback()
                raise AuthDatabaseError(f"Unable to initialize auth database: {exc}") from exc

        row = connection.execute(
            "SELECT value FROM schema_metadata WHERE key='schema_version'"
        ).fetchone()
        if row is None:
            raise AuthDatabaseError("Auth database is missing schema_version")
        try:
            version = int(row[0])
        except (TypeError, ValueError) as exc:
            raise AuthDatabaseError(f"Invalid auth schema_version: {row[0]!r}") from exc
        if version == 1 and migrate:
            try:
                migrate_v1_to_v2(connection)
            except sqlite3.Error as exc:
                raise AuthDatabaseError(f"Unable to migrate auth schema v1 to v2: {exc}") from exc
            version = 2
        if version == 2 and migrate:
            try:
                migrate_v2_to_v3(connection)
            except sqlite3.Error as exc:
                raise AuthDatabaseError(f"Unable to migrate auth schema v2 to v3: {exc}") from exc
            version = 3
        if version == 3 and migrate:
            try:
                migrate_v3_to_v4(connection)
            except sqlite3.Error as exc:
                raise AuthDatabaseError(f"Unable to migrate auth schema v3 to v4: {exc}") from exc
            version = 4
        if version != AUTH_SCHEMA_VERSION:
            raise AuthDatabaseError(
                f"Unsupported auth schema version {version}; expected {AUTH_SCHEMA_VERSION}"
            )

    @staticmethod
    def schema_version(connection: sqlite3.Connection) -> int:
        return int(
            connection.execute(
                "SELECT value FROM schema_metadata WHERE key='schema_version'"
            ).fetchone()[0]
        )


def migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
    """Add the credential_status column introduced in Phase 11 hardening."""
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            "ALTER TABLE users ADD COLUMN credential_status TEXT NOT NULL "
            "DEFAULT 'VALID' CHECK(credential_status IN ('VALID', 'RESET_REQUIRED'))"
        )
        connection.execute("UPDATE schema_metadata SET value='2' WHERE key='schema_version'")
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def migrate_v2_to_v3(connection: sqlite3.Connection) -> None:
    """Add the separate web-research quota bucket introduced in Phase 12D.

    Web research cost is deliberately isolated from the ordinary ask
    bucket, so a dedicated table (not a column change on ``usage``) keeps
    both accounting paths simple and independently prunable.
    """
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            "CREATE TABLE web_usage (\n"
            "    user_id INTEGER NOT NULL,\n"
            "    day TEXT NOT NULL,\n"
            "    minute TEXT NOT NULL,\n"
            "    requests INTEGER NOT NULL DEFAULT 0,\n"
            "    PRIMARY KEY(user_id, day, minute),\n"
            "    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE\n"
            ")"
        )
        connection.execute("UPDATE schema_metadata SET value='3' WHERE key='schema_version'")
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def migrate_v3_to_v4(connection: sqlite3.Connection) -> None:
    """Add the isolated Phase 14 agent quota bucket atomically."""
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS agent_usage ("
            "user_id INTEGER NOT NULL, day TEXT NOT NULL, minute TEXT NOT NULL, "
            "requests INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(user_id, day, minute), "
            "FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE)"
        )
        connection.execute("UPDATE schema_metadata SET value='4' WHERE key='schema_version'")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
