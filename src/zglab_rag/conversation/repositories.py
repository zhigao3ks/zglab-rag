"""Owner-scoped repositories over the standalone conversation database."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

from zglab_rag.conversation.models import (
    Conversation,
    ConversationSummary,
    Message,
    MessageRole,
    SessionResource,
    SessionResourceType,
)


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

    def list_for_conversation(self, *, owner_user_id: int, conversation_id: int) -> list[Message]:
        rows = self.connection.execute(
            "SELECT messages.id, messages.conversation_id, messages.role, messages.content, "
            "messages.created_at FROM messages JOIN conversations "
            "ON conversations.id=messages.conversation_id "
            "WHERE conversations.id=? AND conversations.owner_user_id=? "
            "ORDER BY messages.created_at ASC, messages.id ASC",
            (conversation_id, owner_user_id),
        ).fetchall()
        return [_message_from_row(row) for row in rows]

    def list_bounded_for_conversation(
        self,
        *,
        owner_user_id: int,
        conversation_id: int,
        limit: int,
    ) -> list[Message]:
        """Return the newest `limit` messages for context scanning."""
        rows = self.connection.execute(
            "SELECT messages.id, messages.conversation_id, messages.role, messages.content, "
            "messages.created_at FROM messages JOIN conversations "
            "ON conversations.id=messages.conversation_id "
            "WHERE conversations.id=? AND conversations.owner_user_id=? "
            "ORDER BY messages.created_at DESC, messages.id DESC LIMIT ?",
            (conversation_id, owner_user_id, limit),
        ).fetchall()
        return [_message_from_row(row) for row in reversed(rows)]

    def list_after_message_id(
        self,
        *,
        owner_user_id: int,
        conversation_id: int,
        after_message_id: int,
        limit: int,
    ) -> list[Message]:
        """Return a bounded chronological prefix after summary coverage."""
        rows = self.connection.execute(
            "SELECT messages.id, messages.conversation_id, messages.role, messages.content, "
            "messages.created_at FROM messages JOIN conversations "
            "ON conversations.id=messages.conversation_id "
            "WHERE conversations.id=? AND conversations.owner_user_id=? AND messages.id>? "
            "ORDER BY messages.id ASC LIMIT ?",
            (conversation_id, owner_user_id, after_message_id, limit),
        ).fetchall()
        return [_message_from_row(row) for row in rows]


def _summary_from_row(row: sqlite3.Row) -> ConversationSummary:
    return ConversationSummary(
        conversation_id=row["conversation_id"],
        content=row["content"],
        covered_through_message_id=row["covered_through_message_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


class ConversationSummaryRepository:
    """Owner-scoped summary storage with monotonic coverage enforcement."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get(self, *, owner_user_id: int, conversation_id: int) -> ConversationSummary | None:
        """Load summary only if the conversation belongs to the owner."""
        row = self.connection.execute(
            "SELECT cs.conversation_id, cs.content, cs.covered_through_message_id, "
            "cs.created_at, cs.updated_at "
            "FROM conversation_summaries cs "
            "JOIN conversations c ON c.id = cs.conversation_id "
            "WHERE cs.conversation_id = ? AND c.owner_user_id = ?",
            (conversation_id, owner_user_id),
        ).fetchone()
        return _summary_from_row(row) if row else None

    def upsert(
        self,
        *,
        owner_user_id: int,
        conversation_id: int,
        content: str,
        covered_through_message_id: int,
        now: datetime | None = None,
    ) -> ConversationSummary:
        """Insert or update summary with strict validation.

        Enforces:
        - owner isolation
        - non-blank content
        - positive coverage
        - message belongs to conversation and is ASSISTANT
        - monotonic coverage (no rollback)
        """
        if not content or not content.strip():
            raise ValueError("Summary content must not be blank")
        if covered_through_message_id <= 0:
            raise ValueError("covered_through_message_id must be positive")

        timestamp = _format_timestamp(now or utc_now())

        self.connection.execute("BEGIN IMMEDIATE")
        try:
            # Verify ownership
            owned = self.connection.execute(
                "SELECT 1 FROM conversations WHERE id=? AND owner_user_id=?",
                (conversation_id, owner_user_id),
            ).fetchone()
            if owned is None:
                self.connection.rollback()
                raise ValueError(
                    f"Conversation {conversation_id} not found for owner {owner_user_id}"
                )

            # Verify message belongs to conversation and is ASSISTANT
            message_row = self.connection.execute(
                "SELECT role FROM messages WHERE id=? AND conversation_id=?",
                (covered_through_message_id, conversation_id),
            ).fetchone()
            if message_row is None:
                self.connection.rollback()
                raise ValueError(
                    "Message does not belong to the requested conversation: "
                    f"message_id={covered_through_message_id} conversation_id={conversation_id}"
                )
            if message_row["role"] != MessageRole.ASSISTANT.value:
                self.connection.rollback()
                raise ValueError(
                    "covered_through_message_id must reference an ASSISTANT message, "
                    f"got {message_row['role']}"
                )

            # Check monotonic coverage
            existing = self.connection.execute(
                "SELECT covered_through_message_id FROM conversation_summaries "
                "WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()

            if existing is not None:
                old_coverage = existing["covered_through_message_id"]
                if covered_through_message_id < old_coverage:
                    self.connection.rollback()
                    raise ValueError(
                        "Coverage rollback forbidden: "
                        f"new={covered_through_message_id} < old={old_coverage}"
                    )
                # Update
                self.connection.execute(
                    "UPDATE conversation_summaries SET content=?, covered_through_message_id=?, "
                    "updated_at=? WHERE conversation_id=?",
                    (content, covered_through_message_id, timestamp, conversation_id),
                )
            else:
                # Insert
                self.connection.execute(
                    "INSERT INTO conversation_summaries "
                    "(conversation_id, content, covered_through_message_id, created_at, "
                    "updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (conversation_id, content, covered_through_message_id, timestamp, timestamp),
                )

            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

        return self.get(owner_user_id=owner_user_id, conversation_id=conversation_id)


def _resource_from_row(row: sqlite3.Row) -> SessionResource:
    return SessionResource(
        id=row["id"],
        conversation_id=row["conversation_id"],
        resource_type=SessionResourceType(row["resource_type"]),
        resource_key=row["resource_key"],
        payload_json=row["payload_json"],
        provenance_json=row["provenance_json"],
        producer_fingerprint=row["producer_fingerprint"],
        source_request_id=row["source_request_id"],
        size_bytes=row["size_bytes"],
        created_at=datetime.fromisoformat(row["created_at"]),
        expires_at=datetime.fromisoformat(row["expires_at"]),
        last_used_at=datetime.fromisoformat(row["last_used_at"]),
    )


class SessionResourceRepository:
    """SQLite persistence for typed resource reuse, always owner scoped."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get_valid(
        self,
        *,
        owner_user_id: int,
        conversation_id: int,
        resource_type: SessionResourceType,
        resource_key: str,
        producer_fingerprint: str,
        now: datetime | None = None,
    ) -> SessionResource | None:
        moment = now or utc_now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                """
                SELECT sr.* FROM session_resources sr
                JOIN conversations c ON c.id=sr.conversation_id
                WHERE c.owner_user_id=? AND sr.conversation_id=?
                  AND sr.resource_type=? AND sr.resource_key=?
                """,
                (owner_user_id, conversation_id, resource_type.value, resource_key),
            ).fetchone()
            if row is None:
                self.connection.commit()
                return None
            if datetime.fromisoformat(row["expires_at"]) <= moment:
                self.connection.execute("DELETE FROM session_resources WHERE id=?", (row["id"],))
                self.connection.commit()
                return None
            if row["producer_fingerprint"] != producer_fingerprint:
                self.connection.commit()
                return None
            stamp = _format_timestamp(moment)
            self.connection.execute(
                "UPDATE session_resources SET last_used_at=? WHERE id=?", (stamp, row["id"])
            )
            self.connection.commit()
            return _resource_from_row(row)
        except Exception:
            self.connection.rollback()
            raise

    def put_bounded(
        self,
        *,
        owner_user_id: int,
        conversation_id: int,
        resource_type: SessionResourceType,
        resource_key: str,
        payload: dict,
        provenance: dict,
        producer_fingerprint: str,
        source_request_id: str,
        ttl_seconds: int,
        max_items: int,
        max_bytes: int,
        max_item_bytes: int,
        now: datetime | None = None,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        payload_json = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        provenance_json = json.dumps(
            provenance, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        size_bytes = len(payload_json.encode("utf-8")) + len(provenance_json.encode("utf-8"))
        if not 0 < size_bytes <= max_item_bytes or size_bytes > max_bytes:
            raise ValueError("resource exceeds configured byte limit")
        moment = now or utc_now()
        timestamp = _format_timestamp(moment)
        expires_at = _format_timestamp(moment + timedelta(seconds=ttl_seconds))
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            owned = self.connection.execute(
                "SELECT 1 FROM conversations WHERE id=? AND owner_user_id=?",
                (conversation_id, owner_user_id),
            ).fetchone()
            if owned is None:
                self.connection.rollback()
                raise ValueError("conversation is not owned")
            self.connection.execute(
                "DELETE FROM session_resources WHERE conversation_id=? AND expires_at<=?",
                (conversation_id, timestamp),
            )
            self.connection.execute(
                "INSERT INTO session_resources("
                "conversation_id,resource_type,resource_key,payload_json,provenance_json,"
                "producer_fingerprint,source_request_id,size_bytes,created_at,expires_at,last_used_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(conversation_id,resource_type,resource_key) DO UPDATE SET "
                "payload_json=excluded.payload_json,provenance_json=excluded.provenance_json,producer_fingerprint=excluded.producer_fingerprint,source_request_id=excluded.source_request_id,size_bytes=excluded.size_bytes,expires_at=excluded.expires_at,last_used_at=excluded.last_used_at",
                (
                    conversation_id,
                    resource_type.value,
                    resource_key,
                    payload_json,
                    provenance_json,
                    producer_fingerprint,
                    source_request_id,
                    size_bytes,
                    timestamp,
                    expires_at,
                    timestamp,
                ),
            )
            while True:
                totals = self.connection.execute(
                    "SELECT COUNT(*) AS count, COALESCE(SUM(size_bytes),0) AS bytes "
                    "FROM session_resources WHERE conversation_id=?",
                    (conversation_id,),
                ).fetchone()
                if totals["count"] <= max_items and totals["bytes"] <= max_bytes:
                    break
                victim = self.connection.execute(
                    "SELECT id FROM session_resources WHERE conversation_id=? "
                    "ORDER BY last_used_at ASC, created_at ASC, id ASC LIMIT 1",
                    (conversation_id,),
                ).fetchone()
                if victim is None:
                    break
                self.connection.execute("DELETE FROM session_resources WHERE id=?", (victim["id"],))
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
