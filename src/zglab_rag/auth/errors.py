"""Authentication & Access Control errors (Phase 11).

Error messages are intentionally generic: they never distinguish between
"unknown username", "wrong password" and "disabled account", and never
include tokens, hashes or other secrets. Internal logs may carry more
detail, but only via explicit structured fields.
"""

from __future__ import annotations


class AuthError(Exception):
    """Base class for all authentication / identity errors."""


class UsernamePolicyError(AuthError):
    """Username is malformed or reserved."""


class DuplicateUsernameError(AuthError):
    """A user with the same normalized username already exists."""


class PasswordPolicyError(AuthError):
    """Password does not satisfy the length-first policy."""


class UserNotFoundError(AuthError):
    """Internal: no user matches. Never surfaced with this detail publicly."""


class AccountUnavailableError(AuthError):
    """Account exists but cannot authenticate (disabled / not activated)."""


class InvalidCredentialsError(AuthError):
    """Unified public failure for bad username or bad password."""


class TokenError(AuthError):
    """Base class for credential token failures (activation / reset)."""


class TokenInvalidError(TokenError):
    """Token unknown, malformed, revoked or already consumed."""


class TokenExpiredError(TokenError):
    """Token exists but its validity window has passed."""


class SessionError(AuthError):
    """Session token missing, unknown, expired or revoked."""


class CsrfError(AuthError):
    """CSRF token missing or mismatched for a state-changing request."""


class OriginError(AuthError):
    """Origin / Referer validation failed for a state-changing request."""


class LoginThrottledError(AuthError):
    """Too many login attempts for this IP or username."""

    def __init__(self, retry_after_seconds: float) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__("Too many login attempts; retry later")


class QuotaExceededError(AuthError):
    """Per-user rate limit or daily quota exceeded."""

    def __init__(self, retry_after_seconds: float) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__("Usage quota exceeded; retry later")


class ServiceDisabledError(AuthError):
    """A capability kill switch (e.g. LLM_ENABLED=false) is active."""


class AuthDatabaseError(AuthError):
    """Auth database initialization or schema validation failure."""
