"""Independent SQLite lifecycle for Conversation and Message persistence."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

CONVERSATION_SCHEMA_VERSION = 2

CONVERSATION_SCHEMA = """
CREATE TABLE schema_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE conversations (
    id INTEGER PRIMARY KEY,
    owner_user_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX conversations_owner_updated_idx
    ON conversations(owner_user_id, updated_at DESC, id DESC);

CREATE TABLE messages (
    id INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('USER', 'ASSISTANT')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE INDEX messages_conversation_created_idx
    ON messages(conversation_id, created_at ASC, id ASC);

CREATE TABLE conversation_summaries (
    conversation_id INTEGER PRIMARY KEY,
    content TEXT NOT NULL,
    covered_through_message_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(conversation_id)
        REFERENCES conversations(id)
        ON DELETE CASCADE
);
"""


class ConversationDatabaseError(RuntimeError):
    """Raised when conversation storage is absent, foreign, or mismatched."""


class ConversationDatabase:
    """Open and validate the standalone ``conversation.db`` database.

    ``owner_user_id`` intentionally has no foreign key to ``auth.db``: these
    SQLite files have separate lifecycles. Future protected service wiring
    must supply the authenticated principal and repositories enforce it on
    every owner-scoped operation.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def connect(self, *, initialize: bool = True) -> sqlite3.Connection:
        existed_before = self.path.is_file()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, isolation_level=None, timeout=10.0)
        except sqlite3.Error as exc:
            raise ConversationDatabaseError(f"Unable to open conversation database: {exc}") from exc
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        try:
            self._validate_or_initialize(connection, initialize=initialize)
        except Exception:
            connection.close()
            raise
        if not existed_before:
            os.chmod(self.path, 0o600)
        return connection

    @staticmethod
    def _validate_or_initialize(connection: sqlite3.Connection, *, initialize: bool) -> None:
        has_metadata = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_metadata'"
        ).fetchone()
        if not has_metadata:
            has_any_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') LIMIT 1"
            ).fetchone()
            if has_any_table or not initialize:
                raise ConversationDatabaseError(
                    "Database is not an initialized ZGLab conversation database"
                )
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.executescript(CONVERSATION_SCHEMA)
                connection.execute(
                    "INSERT INTO schema_metadata(key, value) VALUES ('schema_version', ?)",
                    (str(CONVERSATION_SCHEMA_VERSION),),
                )
                connection.commit()
            except sqlite3.Error as exc:
                connection.rollback()
                raise ConversationDatabaseError(
                    f"Unable to initialize conversation database: {exc}"
                ) from exc

        row = connection.execute(
            "SELECT value FROM schema_metadata WHERE key='schema_version'"
        ).fetchone()
        if row is None:
            raise ConversationDatabaseError("Conversation database is missing schema_version")
        try:
            version = int(row[0])
        except (TypeError, ValueError) as exc:
            raise ConversationDatabaseError(
                f"Invalid conversation schema_version: {row[0]!r}"
            ) from exc

        # Migration path: v1 → v2
        if version == 1:
            ConversationDatabase._migrate_v1_to_v2(connection)
            version = 2

        if version != CONVERSATION_SCHEMA_VERSION:
            raise ConversationDatabaseError(
                "Unsupported conversation schema version "
                f"{version}; expected {CONVERSATION_SCHEMA_VERSION}"
            )

    @staticmethod
    def _migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
        """Atomic migration from v1 to v2: add conversation_summaries table."""
        try:
            connection.execute("BEGIN IMMEDIATE")

            # Create the new table
            connection.execute("""
                CREATE TABLE conversation_summaries (
                    conversation_id INTEGER PRIMARY KEY,
                    content TEXT NOT NULL,
                    covered_through_message_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(conversation_id)
                        REFERENCES conversations(id)
                        ON DELETE CASCADE
                )
            """)

            # Update schema version
            connection.execute(
                "UPDATE schema_metadata SET value=? WHERE key='schema_version'", (str(2),)
            )

            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise ConversationDatabaseError(f"Migration from v1 to v2 failed: {exc}") from exc

    @staticmethod
    def schema_version(connection: sqlite3.Connection) -> int:
        return int(
            connection.execute(
                "SELECT value FROM schema_metadata WHERE key='schema_version'"
            ).fetchone()[0]
        )
