"""Typed, bounded session-resource reuse for one authenticated conversation.

This is deliberately not a generic cache.  Its only callers are the Personal,
Web, and deterministic Tool paths and every operation is bound to an owner and
conversation before capability code receives it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Protocol

from zglab_rag.conversation.database import ConversationDatabase
from zglab_rag.conversation.models import SessionResourceType
from zglab_rag.conversation.repositories import SessionResourceRepository

logger = logging.getLogger(__name__)

PERSONAL_RESOURCE_VERSION = 1
WEB_RESOURCE_VERSION = 1
TOOL_RESOURCE_VERSION = 1


def canonical_text(value: str) -> str:
    """Canonical text used only inside opaque deterministic keys."""
    normalized = unicodedata.normalize("NFKC", value)
    return re.sub(r"\s+", " ", normalized.strip()).lower()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def resource_key(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def personal_resource_key(*, query: str, mode: str, top_k: int, snapshot: str, config: str) -> str:
    return resource_key(
        {
            "v": PERSONAL_RESOURCE_VERSION,
            "query": canonical_text(query),
            "mode": mode,
            "top_k": top_k,
            "knowledge_snapshot": snapshot,
            "config": config,
        }
    )


def web_resource_key(*, query: str, provider: str, config: str) -> str:
    return resource_key(
        {
            "v": WEB_RESOURCE_VERSION,
            "query": canonical_text(query),
            "provider": provider,
            "config": config,
        }
    )


def tool_resource_key(*, tool_id: str, arguments: dict[str, Any]) -> str:
    return resource_key({"v": TOOL_RESOURCE_VERSION, "tool_id": tool_id, "arguments": arguments})


def knowledge_snapshot_fingerprint(connection) -> str | None:
    """Fingerprint the latest completed index run; no run means no reuse."""
    row = connection.execute(
        "SELECT run_id, embedding_profile_id, source_snapshot_json FROM index_runs "
        "WHERE status='completed' ORDER BY finished_at DESC, started_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return resource_key(
        {"run_id": row[0], "embedding_profile_id": row[1], "source_snapshot_json": row[2]}
    )


class SessionWorkspaceProtocol(Protocol):
    def get(
        self, resource_type: SessionResourceType, resource_key: str, *, producer_fingerprint: str
    ) -> dict[str, Any] | None: ...
    def put(
        self,
        resource_type: SessionResourceType,
        resource_key: str,
        *,
        payload: dict[str, Any],
        provenance: dict[str, Any],
        producer_fingerprint: str,
        source_request_id: str,
        ttl_seconds: int,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class SessionWorkspace:
    """Owner- and conversation-bound façade; capability code cannot retarget it."""

    database: ConversationDatabase
    owner_user_id: int
    conversation_id: int
    enabled: bool
    max_items: int
    max_bytes: int
    max_item_bytes: int
    personal_ttl_seconds: int = 21600
    web_ttl_seconds: int = 300
    tool_ttl_seconds: int = 86400

    def get(
        self, resource_type: SessionResourceType, resource_key: str, *, producer_fingerprint: str
    ) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        try:
            connection = self.database.connect(initialize=True)
            try:
                row = SessionResourceRepository(connection).get_valid(
                    owner_user_id=self.owner_user_id,
                    conversation_id=self.conversation_id,
                    resource_type=resource_type,
                    resource_key=resource_key,
                    producer_fingerprint=producer_fingerprint,
                )
            finally:
                connection.close()
            if row is None:
                self._log_lookup(resource_type, "miss", resource_key)
                return None
            try:
                payload = json.loads(row.payload_json)
                if not isinstance(payload, dict) or payload.get("version") != self._version(
                    resource_type
                ):
                    raise ValueError("unknown resource payload version")
            except (TypeError, ValueError, json.JSONDecodeError):
                self._log_lookup(resource_type, "invalid", resource_key)
                return None
            self._log_lookup(resource_type, "hit", resource_key)
            return payload
        except Exception:
            logger.info("session_resource_lookup type=%s outcome=miss", resource_type.value)
            return None

    def put(
        self,
        resource_type: SessionResourceType,
        resource_key: str,
        *,
        payload: dict[str, Any],
        provenance: dict[str, Any],
        producer_fingerprint: str,
        source_request_id: str,
        ttl_seconds: int,
    ) -> None:
        if not self.enabled:
            return
        try:
            connection = self.database.connect(initialize=True)
            try:
                SessionResourceRepository(connection).put_bounded(
                    owner_user_id=self.owner_user_id,
                    conversation_id=self.conversation_id,
                    resource_type=resource_type,
                    resource_key=resource_key,
                    payload=payload,
                    provenance=provenance,
                    producer_fingerprint=producer_fingerprint,
                    source_request_id=source_request_id,
                    ttl_seconds=ttl_seconds,
                    max_items=self.max_items,
                    max_bytes=self.max_bytes,
                    max_item_bytes=self.max_item_bytes,
                )
            finally:
                connection.close()
            logger.info(
                "session_resource_store type=%s status=stored key=%s",
                resource_type.value,
                resource_key[:8],
            )
        except ValueError:
            logger.info(
                "session_resource_store type=%s status=skipped_too_large key=%s",
                resource_type.value,
                resource_key[:8],
            )
        except Exception:
            logger.info(
                "session_resource_store type=%s status=failed key=%s",
                resource_type.value,
                resource_key[:8],
            )

    @staticmethod
    def _version(resource_type: SessionResourceType) -> int:
        return {
            SessionResourceType.PERSONAL_RETRIEVAL: PERSONAL_RESOURCE_VERSION,
            SessionResourceType.WEB_EVIDENCE: WEB_RESOURCE_VERSION,
            SessionResourceType.TOOL_RESULT: TOOL_RESOURCE_VERSION,
        }[resource_type]

    @staticmethod
    def _log_lookup(resource_type: SessionResourceType, outcome: str, key: str) -> None:
        logger.info(
            "session_resource_lookup type=%s outcome=%s key=%s",
            resource_type.value,
            outcome,
            key[:8],
        )
