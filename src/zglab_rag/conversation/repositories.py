"""Owner-scoped repositories over the standalone conversation database."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from zglab_rag.conversation.models import Conversation, Message, MessageRole


def utc_now() -> datetime:
    return datetime.now(UTC)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _conversation_from_row(row: sqlite3.Row) -> Conversation:
    return Conversation(
        id=row["id"],
        owner_user_id=row["owner_user_id"],
        title=row["title"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _message_from_row(row: sqlite3.Row) -> Message:
    return Message(
        id=row["id"],
        conversation_id=row["conversation_id"],
        role=MessageRole(row["role"]),
        content=row["content"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


class ConversationRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def create(
        self, *, owner_user_id: int, title: str, now: datetime | None = None
    ) -> Conversation:
        timestamp = _format_timestamp(now or utc_now())
        cursor = self.connection.execute(
            "INSERT INTO conversations(owner_user_id, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (owner_user_id, title, timestamp, timestamp),
        )
        return self.get(owner_user_id=owner_user_id, conversation_id=cursor.lastrowid)  # type: ignore[arg-type]

    def get(self, *, owner_user_id: int, conversation_id: int) -> Conversation | None:
        row = self.connection.execute(
            "SELECT id, owner_user_id, title, created_at, updated_at FROM conversations "
            "WHERE id=? AND owner_user_id=?",
            (conversation_id, owner_user_id),
        ).fetchone()
        return _conversation_from_row(row) if row else None

    def list(self, *, owner_user_id: int) -> list[Conversation]:
        rows = self.connection.execute(
            "SELECT id, owner_user_id, title, created_at, updated_at FROM conversations "
            "WHERE owner_user_id=? ORDER BY updated_at DESC, id DESC",
            (owner_user_id,),
        ).fetchall()
        return [_conversation_from_row(row) for row in rows]

    def update_title(
        self,
        *,
        owner_user_id: int,
        conversation_id: int,
        title: str,
        now: datetime | None = None,
    ) -> Conversation | None:
        cursor = self.connection.execute(
            "UPDATE conversations SET title=?, updated_at=? WHERE id=? AND owner_user_id=?",
            (title, _format_timestamp(now or utc_now()), conversation_id, owner_user_id),
        )
        if cursor.rowcount == 0:
            return None
        return self.get(owner_user_id=owner_user_id, conversation_id=conversation_id)

    def delete(self, *, owner_user_id: int, conversation_id: int) -> bool:
        cursor = self.connection.execute(
            "DELETE FROM conversations WHERE id=? AND owner_user_id=?",
            (conversation_id, owner_user_id),
        )
        return cursor.rowcount == 1


class MessageRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def append(
        self,
        *,
        owner_user_id: int,
        conversation_id: int,
        role: MessageRole,
        content: str,
        now: datetime | None = None,
    ) -> Message | None:
        """Append only to an owned conversation and atomically touch its timestamp."""
        timestamp = _format_timestamp(now or utc_now())
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            owned = self.connection.execute(
                "SELECT 1 FROM conversations WHERE id=? AND owner_user_id=?",
                (conversation_id, owner_user_id),
            ).fetchone()
            if owned is None:
                self.connection.rollback()
                return None
            cursor = self.connection.execute(
                "INSERT INTO messages(conversation_id, role, content, created_at) "
                "VALUES (?, ?, ?, ?)",
                (conversation_id, role.value, content, timestamp),
            )
            self.connection.execute(
                "UPDATE conversations SET updated_at=? WHERE id=? AND owner_user_id=?",
                (timestamp, conversation_id, owner_user_id),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

        row = self.connection.execute(
            "SELECT id, conversation_id, role, content, created_at FROM messages WHERE id=?",
            (cursor.lastrowid,),
        ).fetchone()
        return _message_from_row(row)

    def list_for_conversation(
        self, *, owner_user_id: int, conversation_id: int
    ) -> list[Message]:
        rows = self.connection.execute(
            "SELECT messages.id, messages.conversation_id, messages.role, messages.content, "
            "messages.created_at FROM messages JOIN conversations "
            "ON conversations.id=messages.conversation_id "
            "WHERE conversations.id=? AND conversations.owner_user_id=? "
            "ORDER BY messages.created_at ASC, messages.id ASC",
            (conversation_id, owner_user_id),
        ).fetchall()
        return [_message_from_row(row) for row in rows]
