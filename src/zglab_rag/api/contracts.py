"""Public API contracts for Phase 9A.

This module defines the narrow public request/response models for the
public API. The design principle is to expose only what is safe and
necessary for the visitor; internal diagnostics, scores, and paths
are never leaked.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class PublicStatus(StrEnum):
    """Public response status values."""

    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class PublicErrorCode(StrEnum):
    """Public error codes for the error envelope."""

    INVALID_REQUEST = "INVALID_REQUEST"
    RATE_LIMITED = "RATE_LIMITED"
    SERVICE_BUSY = "SERVICE_BUSY"
    GENERATION_TIMEOUT = "GENERATION_TIMEOUT"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class PublicAskRequest(BaseModel):
    """Public ask request body.

    The request is intentionally narrow: only the question is accepted.
    Extra fields are rejected to prevent accidental or malicious parameter
    injection.
    """

    model_config = {"extra": "forbid"}

    question: str = Field(min_length=1)


class PublicSource(BaseModel):
    """A citation source safe for public display.

    Internal fields like chunk_id, document_id, revision, scores, and
    absolute paths are never exposed.
    """

    id: str  # Evidence ID (E1, E2, ...)
    title: str
    section: list[str]
    source_path: str


class PublicAskResponse(BaseModel):
    """Public ask response for answered or insufficient_evidence status.

    The response is narrow and stable. It never includes internal
    diagnostics, scores, provider details, or absolute paths.
    """

    request_id: str
    status: Literal["answered", "insufficient_evidence"]
    answer: str
    sources: list[PublicSource]


class PublicErrorDetail(BaseModel):
    """Error detail for the public error envelope."""

    code: PublicErrorCode
    message: str


class PublicErrorResponse(BaseModel):
    """Public error response envelope.

    All errors (validation, rate limit, timeout, internal) use this
    envelope. The response never includes traceback, exception class,
    or internal paths.
    """

    request_id: str
    error: PublicErrorDetail
