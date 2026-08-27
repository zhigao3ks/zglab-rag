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
    # Phase 11 security error codes. Login failures deliberately never
    # distinguish unknown user / wrong password / disabled account.
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    ACCOUNT_UNAVAILABLE = "ACCOUNT_UNAVAILABLE"
    CSRF_REJECTED = "CSRF_REJECTED"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    SERVICE_DISABLED = "SERVICE_DISABLED"
    API_RETIRED = "API_RETIRED"
    # Phase 12D capability-policy codes: the web kill switch refused the
    # request, or the account lacks the web-research permission.
    CAPABILITY_DISABLED = "CAPABILITY_DISABLED"
    CAPABILITY_DENIED = "CAPABILITY_DENIED"


class PublicAskRequest(BaseModel):
    """Public ask request body.

    The request stays narrow: question plus the additive Phase 12D ``mode``
    selector (auto/personal/web). Arbitrary capability ids are never
    accepted; unknown fields are still rejected outright. Clients that only
    send ``question`` keep working unchanged (mode defaults to auto).
    """

    model_config = {"extra": "forbid"}

    question: str = Field(min_length=1)
    mode: Literal["auto", "personal", "web"] = "auto"


class PublicSource(BaseModel):
    """A citation source safe for public display.

    Internal fields like chunk_id, document_id, revision, scores, and
    absolute paths are never exposed.

    Phase 12D additive fields: ``origin`` distinguishes personal knowledge
    from web evidence; web sources carry the provenance-validated ``url``
    and ``domain`` (never model output). Personal sources keep the frozen
    Phase 9 shape with origin=personal and url/domain absent.
    """

    id: str  # Evidence ID (E1, E2, ...)
    title: str
    section: list[str]
    source_path: str
    origin: Literal["personal", "web"] = "personal"
    url: str | None = None
    domain: str | None = None


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


# ---------------------------------------------------------------------------
# Phase 9B: SSE stream contracts
# ---------------------------------------------------------------------------


class PublicStreamStage(StrEnum):
    """Public lifecycle stages emitted as SSE status events.

    Only `completed` may carry the final validated answer; stage events
    carry nothing but request_id and the stage name.
    """

    ACCEPTED = "accepted"
    RETRIEVING = "retrieving"
    RESEARCHING = "researching"
    GENERATING = "generating"
    VALIDATING = "validating"
    COMPLETED = "completed"


class PublicStreamStatus(BaseModel):
    """Narrow SSE status event payload (accepted/retrieving/generating/validating).

    Intentionally contains no evidence content, scores, provider details,
    token usage or diagnostics.
    """

    request_id: str
    stage: PublicStreamStage


# completed reuses the Phase 9A public response; SSE error reuses the
# Phase 9A public error envelope. Aliases keep the stream contract explicit
# without duplicating schema definitions.
PublicStreamCompleted = PublicAskResponse
PublicStreamError = PublicErrorResponse


# ---------------------------------------------------------------------------
# Phase 11: authenticated API v2 contracts
# ---------------------------------------------------------------------------


class AuthLoginRequest(BaseModel):
    """Login request body; narrow and extra-field rejecting."""

    model_config = {"extra": "forbid"}

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class AuthActivateRequest(BaseModel):
    """Activation / password-reset consumption body (single-use token)."""

    model_config = {"extra": "forbid"}

    token: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=256)


class AuthChangePasswordRequest(BaseModel):
    model_config = {"extra": "forbid"}

    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class AuthUserPublic(BaseModel):
    """The minimal user representation safe for the SPA."""

    username: str
    role: Literal["ADMIN", "USER"]


class AuthSessionResponse(BaseModel):
    """Successful login / me response.

    The CSRF token is session-bound and held by the SPA in memory only;
    the session token itself never appears in any response body.
    """

    request_id: str
    user: AuthUserPublic
    csrf_token: str


class AuthResultResponse(BaseModel):
    """Generic success envelope for logout / activate / change-password."""

    request_id: str
    result: Literal[
        "logged_out",
        "account_activated",
        "password_updated",
        "password_changed",
    ]
