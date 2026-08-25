"""Framework-independent auth domain models (Phase 11).

These models are plain dataclasses / enums: they carry no FastAPI or
SQLite types so the identity layer stays decoupled from the protocol
and storage layers, mirroring the project's domain/ boundary rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class UserRole(StrEnum):
    ADMIN = "ADMIN"
    USER = "USER"


class UserStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class CredentialStatus(StrEnum):
    """Password credential validity, separate from the account status.

    RESET_REQUIRED is set the moment an admin issues a password-reset
    token: the old password stops authenticating immediately, before the
    one-time reset token is consumed. It returns to VALID only through a
    successful token consumption. This is fail-closed: an unconsumed or
    expired reset token leaves the account unable to log in until the
    admin reissues one.
    """

    VALID = "VALID"
    RESET_REQUIRED = "RESET_REQUIRED"


class TokenPurpose(StrEnum):
    """Credential token purposes; tokens never cross purpose boundaries."""

    ACTIVATE_ACCOUNT = "ACTIVATE_ACCOUNT"
    RESET_PASSWORD = "RESET_PASSWORD"


class AuditEvent(StrEnum):
    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILURE = "login_failure"
    LOGOUT = "logout"
    ACCOUNT_CREATED = "account_created"
    ACCOUNT_ACTIVATED = "account_activated"
    PASSWORD_CHANGED = "password_changed"
    PASSWORD_RESET_REQUESTED_BY_ADMIN = "password_reset_requested_by_admin"
    SESSION_REVOKED = "session_revoked"
    ACCOUNT_DISABLED = "account_disabled"
    ACCOUNT_ENABLED = "account_enabled"
    QUOTA_EXCEEDED = "quota_exceeded"


@dataclass(frozen=True, slots=True)
class UserRecord:
    id: int
    username: str
    role: UserRole
    status: UserStatus
    credential_status: CredentialStatus
    password_hash: str | None
    created_at: datetime
    created_by: str | None
    activated_at: datetime | None
    password_changed_at: datetime | None


@dataclass(frozen=True, slots=True)
class SessionRecord:
    id: int
    user_id: int
    session_hash: str
    csrf_secret: str
    created_at: datetime
    last_seen_at: datetime
    idle_expires_at: datetime
    absolute_expires_at: datetime
    revoked_at: datetime | None
    client_hint: str | None


@dataclass(frozen=True, slots=True)
class CredentialTokenRecord:
    id: int
    user_id: int
    purpose: TokenPurpose
    token_hash: str
    created_at: datetime
    expires_at: datetime
    consumed_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    """Identity handed to protected handlers after server-side AuthN.

    Deliberately minimal: no RBAC graph yet. Future phases may extend it
    with permissions / capabilities, never with client-asserted data.
    """

    user_id: int
    username: str
    role: UserRole
    session_id: int


@dataclass(frozen=True, slots=True)
class CapabilityPolicy:
    """Reserved capability switches for future cost-bearing skills.

    Phase 11 only consumes `ask`; the other flags are extension points for
    Phase 12+ (Web Research / MCP / Agent) so quota and authorization can
    grow without redesigning the policy shape. Nothing here is enforced
    by client-asserted data.
    """

    ask_allowed: bool = True
    web_research_allowed: bool = False
    mcp_allowed: bool = False
    agent_allowed: bool = False
