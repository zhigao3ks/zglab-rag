"""Framework-free Conversation domain models for Phase 15A1."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class MessageRole(StrEnum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"


@dataclass(frozen=True, slots=True)
class Conversation:
    id: int
    owner_user_id: int
    title: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class Message:
    id: int
    conversation_id: int
    role: MessageRole
    content: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ConversationSummary:
    """Compressed conversation state — never Evidence, never a Source."""

    conversation_id: int
    content: str
    covered_through_message_id: int
    created_at: datetime
    updated_at: datetime


class SessionResourceType(StrEnum):
    """The only typed, bounded reuse resources supported by Phase 15D."""

    PERSONAL_RETRIEVAL = "PERSONAL_RETRIEVAL"
    WEB_EVIDENCE = "WEB_EVIDENCE"
    TOOL_RESULT = "TOOL_RESULT"


@dataclass(frozen=True, slots=True)
class SessionResource:
    id: int
    conversation_id: int
    resource_type: SessionResourceType
    resource_key: str
    payload_json: str
    provenance_json: str
    producer_fingerprint: str
    source_request_id: str
    size_bytes: int
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime
