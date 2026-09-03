from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from zglab_rag.conversation.database import CONVERSATION_SCHEMA_VERSION, ConversationDatabase
from zglab_rag.conversation.models import SessionResourceType
from zglab_rag.conversation.repositories import ConversationRepository, SessionResourceRepository
from zglab_rag.conversation.resources import canonical_text, resource_key, tool_resource_key


def _conversation(repository: ConversationRepository, owner: int = 1) -> int:
    return repository.create(owner_user_id=owner, title="workspace").id


def test_fresh_database_is_schema_v3(tmp_path) -> None:
    connection = ConversationDatabase(tmp_path / "conversation.db").connect()
    try:
        assert ConversationDatabase.schema_version(connection) == CONVERSATION_SCHEMA_VERSION == 3
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='session_resources'"
        ).fetchone()
    finally:
        connection.close()


def test_v2_migrates_without_losing_existing_conversation_state(tmp_path) -> None:
    path = tmp_path / "conversation.db"
    raw = sqlite3.connect(path)
    raw.executescript(
        """
        CREATE TABLE schema_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO schema_metadata VALUES ('schema_version', '2');
        CREATE TABLE conversations (id INTEGER PRIMARY KEY, owner_user_id INTEGER NOT NULL,
          title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE messages (id INTEGER PRIMARY KEY, conversation_id INTEGER NOT NULL,
          role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE conversation_summaries (
          conversation_id INTEGER PRIMARY KEY, content TEXT NOT NULL,
          covered_through_message_id INTEGER NOT NULL, created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        INSERT INTO conversations VALUES(
          1, 7, 'preserved', '2026-01-01T00:00:00+00:00',
          '2026-01-01T00:00:00+00:00'
        );
        INSERT INTO messages VALUES(
          1, 1, 'USER', 'preserved message', '2026-01-01T00:00:00+00:00'
        );
        INSERT INTO conversation_summaries VALUES(
          1, 'preserved summary', 1, '2026-01-01T00:00:00+00:00',
          '2026-01-01T00:00:00+00:00'
        );
        """
    )
    raw.close()
    connection = ConversationDatabase(path).connect()
    try:
        assert ConversationDatabase.schema_version(connection) == 3
        assert (
            connection.execute("SELECT content FROM messages").fetchone()[0] == "preserved message"
        )
        assert (
            connection.execute("SELECT content FROM conversation_summaries").fetchone()[0]
            == "preserved summary"
        )
    finally:
        connection.close()


def test_resource_repository_scopes_expiry_and_deterministic_eviction(tmp_path) -> None:
    connection = ConversationDatabase(tmp_path / "conversation.db").connect()
    try:
        conversations = ConversationRepository(connection)
        first = _conversation(conversations, 1)
        second = _conversation(conversations, 1)
        other = _conversation(conversations, 2)
        repository = SessionResourceRepository(connection)
        now = datetime(2026, 1, 1, tzinfo=UTC)
        for key in ("a", "b", "c"):
            repository.put_bounded(
                owner_user_id=1,
                conversation_id=first,
                resource_type=SessionResourceType.TOOL_RESULT,
                resource_key=key,
                payload={"version": 1, "output": key},
                provenance={"tool_id": "json_format"},
                producer_fingerprint="tool",
                source_request_id="r",
                ttl_seconds=60,
                max_items=2,
                max_bytes=1000,
                max_item_bytes=500,
                now=now,
            )
            now += timedelta(seconds=1)
        assert (
            repository.get_valid(
                owner_user_id=1,
                conversation_id=first,
                resource_type=SessionResourceType.TOOL_RESULT,
                resource_key="a",
                producer_fingerprint="tool",
                now=now,
            )
            is None
        )
        assert (
            repository.get_valid(
                owner_user_id=1,
                conversation_id=second,
                resource_type=SessionResourceType.TOOL_RESULT,
                resource_key="b",
                producer_fingerprint="tool",
                now=now,
            )
            is None
        )
        assert (
            repository.get_valid(
                owner_user_id=2,
                conversation_id=first,
                resource_type=SessionResourceType.TOOL_RESULT,
                resource_key="b",
                producer_fingerprint="tool",
                now=now,
            )
            is None
        )
        assert (
            repository.get_valid(
                owner_user_id=2,
                conversation_id=other,
                resource_type=SessionResourceType.TOOL_RESULT,
                resource_key="b",
                producer_fingerprint="tool",
                now=now,
            )
            is None
        )
        hit = repository.get_valid(
            owner_user_id=1,
            conversation_id=first,
            resource_type=SessionResourceType.TOOL_RESULT,
            resource_key="b",
            producer_fingerprint="tool",
            now=now,
        )
        assert hit and hit.last_used_at < now
        expired = repository.get_valid(
            owner_user_id=1,
            conversation_id=first,
            resource_type=SessionResourceType.TOOL_RESULT,
            resource_key="b",
            producer_fingerprint="tool",
            now=now + timedelta(seconds=61),
        )
        assert expired is None
    finally:
        connection.close()


def test_resource_keys_are_canonical_and_item_limit_is_strict(tmp_path) -> None:
    assert canonical_text(" Ａ\tTest ") == "a test"
    assert tool_resource_key(
        tool_id="json_format", arguments={"a": 1, "b": 2}
    ) == tool_resource_key(tool_id="json_format", arguments={"b": 2, "a": 1})
    assert resource_key({"x": 1}) != resource_key({"x": 2})
    connection = ConversationDatabase(tmp_path / "conversation.db").connect()
    try:
        conversation = _conversation(ConversationRepository(connection))
        try:
            SessionResourceRepository(connection).put_bounded(
                owner_user_id=1,
                conversation_id=conversation,
                resource_type=SessionResourceType.WEB_EVIDENCE,
                resource_key="too-large",
                payload={"version": 1, "content": "x" * 200},
                provenance={"url": "https://example.test"},
                producer_fingerprint="web",
                source_request_id="r",
                ttl_seconds=60,
                max_items=4,
                max_bytes=1000,
                max_item_bytes=20,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("oversize resource was persisted")
    finally:
        connection.close()
