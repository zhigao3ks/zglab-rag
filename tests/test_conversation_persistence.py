"""Phase 15A1 tests for the independent conversation persistence foundation."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from zglab_rag.conversation.database import (
    CONVERSATION_SCHEMA_VERSION,
    ConversationDatabase,
    ConversationDatabaseError,
)
from zglab_rag.conversation.models import MessageRole
from zglab_rag.conversation.repositories import ConversationRepository, MessageRepository


@pytest.fixture
def conversation_connection(tmp_path: Path):
    connection = ConversationDatabase(tmp_path / "conversation.db").connect(initialize=True)
    yield connection
    connection.close()


def test_database_initializes_with_explicit_schema_version_and_sqlite_guards(
    tmp_path: Path,
) -> None:
    path = tmp_path / "conversation.db"
    connection = ConversationDatabase(path).connect(initialize=True)
    try:
        assert ConversationDatabase.schema_version(connection) == CONVERSATION_SCHEMA_VERSION
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"schema_metadata", "conversations", "messages"} <= tables
    finally:
        connection.close()


def test_schema_mismatch_and_foreign_database_fail_fast(tmp_path: Path) -> None:
    path = tmp_path / "conversation.db"
    connection = ConversationDatabase(path).connect(initialize=True)
    connection.execute("UPDATE schema_metadata SET value='99' WHERE key='schema_version'")
    connection.close()

    with pytest.raises(ConversationDatabaseError, match="Unsupported conversation schema version"):
        ConversationDatabase(path).connect(initialize=True)

    foreign_path = tmp_path / "foreign.db"
    raw = sqlite3.connect(foreign_path)
    raw.execute("CREATE TABLE unrelated(id INTEGER PRIMARY KEY)")
    raw.close()
    with pytest.raises(ConversationDatabaseError, match="not an initialized"):
        ConversationDatabase(foreign_path).connect(initialize=True)


def test_create_get_and_list_are_owner_scoped_and_ordered(
    conversation_connection: sqlite3.Connection,
) -> None:
    conversations = ConversationRepository(conversation_connection)
    first_time = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)
    second_time = first_time + timedelta(minutes=1)
    first = conversations.create(owner_user_id=11, title="First", now=first_time)
    second = conversations.create(owner_user_id=11, title="Second", now=second_time)
    other = conversations.create(owner_user_id=22, title="Other", now=second_time)

    assert conversations.get(owner_user_id=11, conversation_id=first.id) == first
    assert conversations.get(owner_user_id=22, conversation_id=first.id) is None
    assert conversations.list(owner_user_id=11) == [second, first]
    assert conversations.list(owner_user_id=22) == [other]


def test_append_returns_stable_message_history_and_touches_conversation(
    conversation_connection: sqlite3.Connection,
) -> None:
    conversations = ConversationRepository(conversation_connection)
    messages = MessageRepository(conversation_connection)
    created_at = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)
    conversation = conversations.create(owner_user_id=11, title="Project", now=created_at)
    same_time = created_at + timedelta(minutes=1)
    first = messages.append(
        owner_user_id=11,
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content="Question",
        now=same_time,
    )
    second = messages.append(
        owner_user_id=11,
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        content="Answer",
        now=same_time,
    )

    assert first is not None
    assert second is not None
    assert messages.list_for_conversation(owner_user_id=11, conversation_id=conversation.id) == [
        first,
        second,
    ]
    refreshed = conversations.get(owner_user_id=11, conversation_id=conversation.id)
    assert refreshed is not None
    assert refreshed.updated_at == same_time


def test_cross_owner_mutations_and_history_are_denied(
    conversation_connection: sqlite3.Connection,
) -> None:
    conversations = ConversationRepository(conversation_connection)
    messages = MessageRepository(conversation_connection)
    conversation = conversations.create(owner_user_id=11, title="Private")

    assert conversations.update_title(
        owner_user_id=22, conversation_id=conversation.id, title="Stolen"
    ) is None
    assert messages.append(
        owner_user_id=22,
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content="Unauthorized",
    ) is None
    assert messages.list_for_conversation(owner_user_id=22, conversation_id=conversation.id) == []
    assert conversations.delete(owner_user_id=22, conversation_id=conversation.id) is False
    assert conversations.get(owner_user_id=11, conversation_id=conversation.id) == conversation


def test_owner_delete_cascades_messages(conversation_connection: sqlite3.Connection) -> None:
    conversations = ConversationRepository(conversation_connection)
    messages = MessageRepository(conversation_connection)
    conversation = conversations.create(owner_user_id=11, title="Disposable")
    assert messages.append(
        owner_user_id=11,
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content="Delete me",
    ) is not None

    assert conversations.delete(owner_user_id=11, conversation_id=conversation.id) is True
    assert messages.list_for_conversation(owner_user_id=11, conversation_id=conversation.id) == []
    assert conversation_connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
