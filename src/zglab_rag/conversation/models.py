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
