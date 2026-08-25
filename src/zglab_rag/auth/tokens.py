"""High-entropy credential tokens (activation / password reset).

Tokens are generated with the OS CSPRNG via ``secrets`` and carry at
least 256 bits of entropy. The database only ever stores the SHA-256
digest of a token; the plaintext exists exclusively in the one-time
URL handed to the admin and is never logged or re-displayed.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

# 48 random bytes -> 64 URL-safe characters -> 384 bits of entropy,
# comfortably above the 256-bit requirement.
TOKEN_RANDOM_BYTES = 48


def generate_credential_token() -> str:
    """Generate a new single-use credential token (URL-safe)."""
    return secrets.token_urlsafe(TOKEN_RANDOM_BYTES)


def generate_session_token() -> str:
    """Generate a new random session token (URL-safe)."""
    return secrets.token_urlsafe(TOKEN_RANDOM_BYTES)


def generate_csrf_secret() -> str:
    """Generate the per-session CSRF secret bound to a server session."""
    return secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    """Return the SHA-256 hex digest stored in the database.

    Plaintext tokens are never persisted; lookups always compare digests.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_digests_equal(expected: str, actual: str) -> bool:
    """Constant-time comparison of two token digests."""
    return hmac.compare_digest(expected.encode("utf-8"), actual.encode("utf-8"))
