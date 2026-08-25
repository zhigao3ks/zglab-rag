"""Authentication & Access Control layer (Phase 11).

Boundary: identity, sessions, credential tokens, quotas and audit live
here and in the dedicated ``auth.db``. This package never touches the
knowledge index, retrieval or generation internals; the API layer wires
it in front of the protected endpoints.
"""

from zglab_rag.auth.audit import AuditLogger
from zglab_rag.auth.database import AUTH_SCHEMA_VERSION, AuthDatabase
from zglab_rag.auth.errors import (
    AccountUnavailableError,
    AuthDatabaseError,
    AuthError,
    CsrfError,
    DuplicateUsernameError,
    InvalidCredentialsError,
    LoginThrottledError,
    OriginError,
    PasswordPolicyError,
    QuotaExceededError,
    ServiceDisabledError,
    SessionError,
    TokenExpiredError,
    TokenInvalidError,
    UsernamePolicyError,
    UserNotFoundError,
)
from zglab_rag.auth.identity import (
    IdentityConfig,
    IdentityService,
    ProvisionedUser,
    normalize_username,
)
from zglab_rag.auth.models import (
    AuditEvent,
    AuthenticatedPrincipal,
    CredentialTokenRecord,
    SessionRecord,
    TokenPurpose,
    UserRecord,
    UserRole,
    UserStatus,
)
from zglab_rag.auth.quota import QuotaConfig, UsageGuard
from zglab_rag.auth.session import LoginResult, SessionConfig, SessionService, csrf_token_for
from zglab_rag.auth.throttle import LoginThrottle, LoginThrottleConfig

__all__ = [
    "AUTH_SCHEMA_VERSION",
    "AccountUnavailableError",
    "AuditEvent",
    "AuditLogger",
    "AuthDatabase",
    "AuthDatabaseError",
    "AuthError",
    "AuthenticatedPrincipal",
    "CredentialTokenRecord",
    "CsrfError",
    "DuplicateUsernameError",
    "IdentityConfig",
    "IdentityService",
    "InvalidCredentialsError",
    "LoginResult",
    "LoginThrottle",
    "LoginThrottleConfig",
    "LoginThrottledError",
    "OriginError",
    "PasswordPolicyError",
    "ProvisionedUser",
    "QuotaConfig",
    "QuotaExceededError",
    "ServiceDisabledError",
    "SessionConfig",
    "SessionError",
    "SessionRecord",
    "SessionService",
    "TokenExpiredError",
    "TokenInvalidError",
    "TokenPurpose",
    "UsageGuard",
    "UserNotFoundError",
    "UsernamePolicyError",
    "UserRecord",
    "UserRole",
    "UserStatus",
    "csrf_token_for",
    "normalize_username",
]
