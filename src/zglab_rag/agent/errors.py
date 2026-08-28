"""Safe, domain-level Phase 14A adapter errors."""

from __future__ import annotations

from enum import StrEnum


class AgentErrorCode(StrEnum):
    CAPABILITY_FAILED = "CAPABILITY_FAILED"
    CAPABILITY_DISABLED = "CAPABILITY_DISABLED"
    TOOL_FAILED = "TOOL_FAILED"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
    INVALID_OBSERVATION = "INVALID_OBSERVATION"


class AgentError(Exception):
    def __init__(self, code: AgentErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
