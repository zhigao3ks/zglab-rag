"""Auth repositories over the standalone auth database.

All timestamps are stored as ISO-8601 UTC strings, matching the knowledge
index convention. Repositories never log or return plaintext tokens or
passwords: credential tokens are looked up by SHA-256 digest only.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from zglab_rag.auth.models import (
    AuditEvent,
    CredentialStatus,
    CredentialTokenRecord,
    SessionRecord,
    TokenPurpose,
    UserRecord,
    UserRole,
    UserStatus,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def parse_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _user_from_row(row: sqlite3.Row) -> UserRecord:
    return UserRecord(
        id=row["id"],
        username=row["username"],
        role=UserRole(row["role"]),
        status=UserStatus(row["status"]),
        credential_status=CredentialStatus(row["credential_status"]),
        password_hash=row["password_hash"],
        created_at=parse_timestamp(row["created_at"]),  # type: ignore[arg-type]
        created_by=row["created_by"],
        activated_at=parse_timestamp(row["activated_at"]),
        password_changed_at=parse_timestamp(row["password_changed_at"]),
    )


def _session_from_row(row: sqlite3.Row) -> SessionRecord:
    return SessionRecord(
        id=row["id"],
        user_id=row["user_id"],
        session_hash=row["session_hash"],
        csrf_secret=row["csrf_secret"],
        created_at=parse_timestamp(row["created_at"]),  # type: ignore[arg-type]
        last_seen_at=parse_timestamp(row["last_seen_at"]),  # type: ignore[arg-type]
        idle_expires_at=parse_timestamp(row["idle_expires_at"]),  # type: ignore[arg-type]
        absolute_expires_at=parse_timestamp(row["absolute_expires_at"]),  # type: ignore[arg-type]
        revoked_at=parse_timestamp(row["revoked_at"]),
        client_hint=row["client_hint"],
    )


def _token_from_row(row: sqlite3.Row) -> CredentialTokenRecord:
    return CredentialTokenRecord(
        id=row["id"],
        user_id=row["user_id"],
        purpose=TokenPurpose(row["purpose"]),
        token_hash=row["token_hash"],
        created_at=parse_timestamp(row["created_at"]),  # type: ignore[arg-type]
        expires_at=parse_timestamp(row["expires_at"]),  # type: ignore[arg-type]
        consumed_at=parse_timestamp(row["consumed_at"]),
        revoked_at=parse_timestamp(row["revoked_at"]),
    )


class UserRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def create(
        self,
        *,
        username: str,
        role: UserRole,
        created_by: str | None,
        now: datetime | None = None,
    ) -> UserRecord:
        """Insert a PENDING user without password; activation sets it later."""
        timestamp = format_timestamp(now or utc_now())
        cursor = self.connection.execute(
            "INSERT INTO users(username, role, status, created_at, created_by) "
            "VALUES (?, ?, 'PENDING', ?, ?)",
            (username, role.value, timestamp, created_by),
        )
        return self.get_by_id(cursor.lastrowid)  # type: ignore[arg-type]

    def get_by_id(self, user_id: int) -> UserRecord | None:
        row = self.connection.execute(
            "SELECT * FROM users WHERE id=?", (user_id,)
        ).fetchone()
        return _user_from_row(row) if row else None

    def get_by_username(self, username: str) -> UserRecord | None:
        row = self.connection.execute(
            "SELECT * FROM users WHERE username=?", (username,)
        ).fetchone()
        return _user_from_row(row) if row else None

    def list_users(self) -> list[UserRecord]:
        rows = self.connection.execute(
            "SELECT * FROM users ORDER BY id"
        ).fetchall()
        return [_user_from_row(row) for row in rows]

    def set_password(
        self, user_id: int, password_hash: str, *, now: datetime | None = None
    ) -> None:
        timestamp = format_timestamp(now or utc_now())
        self.connection.execute(
            "UPDATE users SET password_hash=?, password_changed_at=? WHERE id=?",
            (password_hash, timestamp, user_id),
        )

    def set_status(self, user_id: int, status: UserStatus) -> None:
        self.connection.execute(
            "UPDATE users SET status=? WHERE id=?", (status.value, user_id)
        )

    def set_credential_status(self, user_id: int, credential_status: CredentialStatus) -> None:
        self.connection.execute(
            "UPDATE users SET credential_status=? WHERE id=?",
            (credential_status.value, user_id),
        )

    def mark_activated(self, user_id: int, *, now: datetime | None = None) -> None:
        timestamp = format_timestamp(now or utc_now())
        self.connection.execute(
            "UPDATE users SET status='ACTIVE', activated_at=? WHERE id=?",
            (timestamp, user_id),
        )


class SessionRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def create(self, session: SessionRecord) -> SessionRecord:
        cursor = self.connection.execute(
            "INSERT INTO sessions(user_id, session_hash, csrf_secret, created_at, "
            "last_seen_at, idle_expires_at, absolute_expires_at, client_hint) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session.user_id,
                session.session_hash,
                session.csrf_secret,
                format_timestamp(session.created_at),
                format_timestamp(session.last_seen_at),
                format_timestamp(session.idle_expires_at),
                format_timestamp(session.absolute_expires_at),
                session.client_hint,
            ),
        )
        return self.get_by_id(cursor.lastrowid)  # type: ignore[arg-type,return-value]

    def get_by_id(self, session_id: int) -> SessionRecord | None:
        row = self.connection.execute(
            "SELECT * FROM sessions WHERE id=?", (session_id,)
        ).fetchone()
        return _session_from_row(row) if row else None

    def find_by_hash(self, session_hash: str) -> SessionRecord | None:
        row = self.connection.execute(
            "SELECT * FROM sessions WHERE session_hash=?", (session_hash,)
        ).fetchone()
        return _session_from_row(row) if row else None

    def touch(
        self, session_id: int, idle_expires_at: datetime, *, now: datetime | None = None
    ) -> None:
        timestamp = format_timestamp(now or utc_now())
        self.connection.execute(
            "UPDATE sessions SET last_seen_at=?, idle_expires_at=? WHERE id=?",
            (timestamp, format_timestamp(idle_expires_at), session_id),
        )

    def revoke(self, session_id: int, *, now: datetime | None = None) -> None:
        timestamp = format_timestamp(now or utc_now())
        self.connection.execute(
            "UPDATE sessions SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
            (timestamp, session_id),
        )

    def revoke_all_for_user(
        self, user_id: int, *, except_session_id: int | None = None, now: datetime | None = None
    ) -> int:
        timestamp = format_timestamp(now or utc_now())
        if except_session_id is not None:
            cursor = self.connection.execute(
                "UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL "
                "AND id != ?",
                (timestamp, user_id, except_session_id),
            )
        else:
            cursor = self.connection.execute(
                "UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                (timestamp, user_id),
            )
        return cursor.rowcount

    def prune_expired(self, *, now: datetime | None = None) -> int:
        """Delete expired/revoked sessions so the table stays small."""
        timestamp = format_timestamp(now or utc_now())
        cursor = self.connection.execute(
            "DELETE FROM sessions WHERE revoked_at IS NOT NULL "
            "OR idle_expires_at < ? OR absolute_expires_at < ?",
            (timestamp, timestamp),
        )
        return cursor.rowcount


class CredentialTokenRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def create(self, token: CredentialTokenRecord) -> CredentialTokenRecord:
        cursor = self.connection.execute(
            "INSERT INTO credential_tokens(user_id, purpose, token_hash, created_at, "
            "expires_at) VALUES (?, ?, ?, ?, ?)",
            (
                token.user_id,
                token.purpose.value,
                token.token_hash,
                format_timestamp(token.created_at),
                format_timestamp(token.expires_at),
            ),
        )
        return self.get_by_id(cursor.lastrowid)  # type: ignore[arg-type,return-value]

    def get_by_id(self, token_id: int) -> CredentialTokenRecord | None:
        row = self.connection.execute(
            "SELECT * FROM credential_tokens WHERE id=?", (token_id,)
        ).fetchone()
        return _token_from_row(row) if row else None

    def find_by_hash(self, token_hash: str) -> CredentialTokenRecord | None:
        row = self.connection.execute(
            "SELECT * FROM credential_tokens WHERE token_hash=?", (token_hash,)
        ).fetchone()
        return _token_from_row(row) if row else None

    def mark_consumed(self, token_id: int, *, now: datetime | None = None) -> None:
        timestamp = format_timestamp(now or utc_now())
        self.connection.execute(
            "UPDATE credential_tokens SET consumed_at=? WHERE id=? AND consumed_at IS NULL",
            (timestamp, token_id),
        )

    def revoke_superseded(
        self, user_id: int, purpose: TokenPurpose, *, now: datetime | None = None
    ) -> int:
        """Revoke all unconsumed tokens of a purpose (a new token supersedes)."""
        timestamp = format_timestamp(now or utc_now())
        cursor = self.connection.execute(
            "UPDATE credential_tokens SET revoked_at=? WHERE user_id=? AND purpose=? "
            "AND consumed_at IS NULL AND revoked_at IS NULL",
            (timestamp, user_id, purpose.value),
        )
        return cursor.rowcount


class UsageRepository:
    """Per-user consumption counters (minute bucket + daily total).

    Phase 12D: the same counter shape backs the separate web-research
    bucket via ``table="web_usage"``; the default stays the frozen
    personal-ask ``usage`` table.
    """

    def __init__(self, connection: sqlite3.Connection, *, table: str = "usage") -> None:
        if table not in ("usage", "web_usage"):
            raise ValueError(f"unknown usage table: {table!r}")
        self.connection = connection
        self.table = table

    def counts(self, user_id: int, *, now: datetime | None = None) -> tuple[int, int]:
        """Return (requests_this_minute, requests_today) before recording."""
        moment = now or utc_now()
        day = moment.strftime("%Y-%m-%d")
        minute = moment.strftime("%Y-%m-%dT%H:%M")
        minute_row = self.connection.execute(
            f"SELECT requests FROM {self.table} WHERE user_id=? AND day=? AND minute=?",
            (user_id, day, minute),
        ).fetchone()
        day_row = self.connection.execute(
            f"SELECT COALESCE(SUM(requests), 0) FROM {self.table} WHERE user_id=? AND day=?",
            (user_id, day),
        ).fetchone()
        return (
            int(minute_row["requests"]) if minute_row else 0,
            int(day_row[0]) if day_row else 0,
        )

    def record(self, user_id: int, *, now: datetime | None = None) -> None:
        moment = now or utc_now()
        day = moment.strftime("%Y-%m-%d")
        minute = moment.strftime("%Y-%m-%dT%H:%M")
        self.connection.execute(
            f"INSERT INTO {self.table}(user_id, day, minute, requests) VALUES (?, ?, ?, 1) "
            "ON CONFLICT(user_id, day, minute) DO UPDATE SET requests=requests + 1",
            (user_id, day, minute),
        )

    def refund(self, user_id: int, *, now: datetime | None = None) -> None:
        """Decrement the current minute bucket (never below zero)."""
        moment = now or utc_now()
        day = moment.strftime("%Y-%m-%d")
        minute = moment.strftime("%Y-%m-%dT%H:%M")
        self.connection.execute(
            f"UPDATE {self.table} SET requests = MAX(requests - 1, 0) "
            "WHERE user_id=? AND day=? AND minute=?",
            (user_id, day, minute),
        )

    def prune_old(self, *, before_day: str) -> int:
        cursor = self.connection.execute(
            f"DELETE FROM {self.table} WHERE day < ?", (before_day,)
        )
        return cursor.rowcount


class AuditRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def record(
        self,
        event: AuditEvent,
        *,
        result: str,
        user_id: int | None = None,
        request_id: str | None = None,
        client_hint: str | None = None,
        now: datetime | None = None,
    ) -> None:
        """Persist one audit event; secret material must never reach here."""
        self.connection.execute(
            "INSERT INTO audit_events(created_at, event, user_id, request_id, result, "
            "client_hint) VALUES (?, ?, ?, ?, ?, ?)",
            (
                format_timestamp(now or utc_now()),
                event.value,
                user_id,
                request_id,
                result,
                client_hint,
            ),
        )

    def recent(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM audit_events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
