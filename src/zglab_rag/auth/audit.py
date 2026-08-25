"""Auth security audit (Phase 11).

Every security-relevant event is recorded twice: as a structured JSON log
line (reaching journald in production) and as a row in the auth database.
Records only carry timestamp, event, request_id, user_id, result and a
safe client hint. Passwords, hashes, tokens, CSRF secrets, API keys and
full questions are never part of an audit record.
"""

from __future__ import annotations

import json
import logging
import sqlite3

from zglab_rag.auth.models import AuditEvent
from zglab_rag.auth.repositories import AuditRepository, utc_now

logger = logging.getLogger("zglab_rag.auth.audit")

# Safe client hints are truncated hard identifiers only (e.g. an IP from a
# trusted proxy). Full User-Agent strings are intentionally excluded.
MAX_CLIENT_HINT_LENGTH = 64


def sanitize_client_hint(client_hint: str | None) -> str | None:
    if not client_hint:
        return None
    return client_hint[:MAX_CLIENT_HINT_LENGTH]


class AuditLogger:
    """Writes auth audit events to the database and the application log."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.repository = AuditRepository(connection)

    def record(
        self,
        event: AuditEvent,
        *,
        result: str,
        user_id: int | None = None,
        request_id: str | None = None,
        client_hint: str | None = None,
    ) -> None:
        hint = sanitize_client_hint(client_hint)
        self.repository.record(
            event,
            result=result,
            user_id=user_id,
            request_id=request_id,
            client_hint=hint,
            now=utc_now(),
        )
        logger.info(
            "%s",
            json.dumps(
                {
                    "event": "auth_audit",
                    "audit_event": event.value,
                    "result": result,
                    "user_id": user_id,
                    "request_id": request_id,
                    "client_hint": hint,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
